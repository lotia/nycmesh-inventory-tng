"""What every route that needs a registered device depends on
(docs/decisions/0009): the device cookie, and the device it names; and
the reply to a refused device, DeviceRefusedReply, which the endpoints
needing one declare. The rules are in devices.py.
"""

from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyCookie
from opentelemetry import metrics
from pydantic import BaseModel
from sqlalchemy import select

from inventory_tng.core import request_logging
from inventory_tng.core.db import DbSession, record_last_seen
from inventory_tng.devices.devices import (
    COOKIE,
    LAST_SEEN_EVERY,
    DeviceRefused,
    Refusal,
    digest,
)
from inventory_tng.models.devices import Device

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

refused = meter.create_counter(
    "inventory.devices.refused",
    unit="{request}",
    description="Requests refused for want of a registered device, by reason",
)


class DeviceRefusedReply(BaseModel):
    reason: Refusal


# The device cookie, as the API's schema declares it, so /api/docs and the
# schema say which routes need it (openapi-typescript's types do not).
# Missing, it is None: the refusal is current_device's own (DeviceRefused,
# its metric and log).
device_cookie = APIKeyCookie(
    name=COOKIE,
    scheme_name="device",
    description="Set by POST /api/devices when a device registers with a code. "
    "A browser sends its own: Authorize on /api/docs cannot set a cookie.",
    auto_error=False,
)


def current_device(
    request: Request,
    session: DbSession,
    token: Annotated[str | None, Depends(device_cookie)],
) -> Device:
    """The registered device making this request, or DeviceRefused. Reads the
    row every time: the cookie alone is never enough, or revoking a device
    would not stop it. Puts device_id on the request's log lines."""
    device = (
        session.scalar(select(Device).where(Device.token_hash == digest(token)))
        if token
        else None
    )
    if device is None or device.revoked_at is not None:
        reason: Refusal = "unregistered" if device is None else "revoked"
        refused.add(1, {"reason": reason})
        log.info("device refused", reason=reason, device_id=device and device.id)
        raise DeviceRefused(reason)
    request_logging.bind(device_id=device.id)
    now = datetime.now(UTC)
    if device.last_seen_at <= now - LAST_SEEN_EVERY:
        record_last_seen(request.app.state.db, Device, device.id, now)
    return device


RegisteredDevice = Annotated[Device, Depends(current_device)]


def install_device_refused(app: FastAPI) -> None:
    """The reply to a refused device, which the scan page can show."""

    @app.exception_handler(DeviceRefused)
    def device_refused(_request: Request, error: DeviceRefused) -> JSONResponse:
        body = DeviceRefusedReply(reason=error.reason)
        return JSONResponse(body.model_dump(), status_code=403)
