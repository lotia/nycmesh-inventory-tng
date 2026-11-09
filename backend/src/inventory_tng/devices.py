"""Registered devices and the codes that register them (docs/decisions/0009).

A phone registers once, with a code an administrator hands out: a QR code in
the room, or a phrase of words for a remote volunteer. The code only lets the
device register; the device then has its own random id, carried in a cookie.
Every request that needs a device reads the cookie and looks up its row, so
revoking one device takes effect at once.

Only established calls here (AGENTS.md rule 3): `secrets` makes the phrase and
the cookie, and `hashlib` hashes them for the database. Nothing is signed: the
row lookup is what decides, so a value someone makes up just finds no row.

The words are EFF's large wordlist (eff_large_wordlist.txt), by the Electronic
Frontier Foundation, under CC BY 3.0 US:
https://www.eff.org/dice
"""

import functools
import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from importlib import resources
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Annotated, Literal, NoReturn

import structlog
from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyCookie
from limits import RateLimitItemPerMinute
from opentelemetry import metrics
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import request_logging
from inventory_tng.db import (
    DbSession,
    exists_where,
    get_locked,
    locked,
    record_last_seen,
)
from inventory_tng.models import Device, RegistrationCode
from inventory_tng.rate_limits import limiter, refund
from inventory_tng.web import client_address, set_private_cookie

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

router = APIRouter()

COOKIE = "inventory_device"
# Chrome's longest; other browsers allow more and are capped to it.
COOKIE_MAX_AGE = int(timedelta(days=400).total_seconds())
PHRASE_WORDS = 5  # About 64 bits from this list.
LAST_SEEN_EVERY = timedelta(hours=1)
# Guessing a phrase has to go through here, one address at a time, shared
# by every instance: with 64-bit phrases that is far more than enough.
ATTEMPTS = RateLimitItemPerMinute(10)

# Why a code does not register: the phrase, or the address trying too often.
BadCode = Literal["unknown", "expired", "used_up", "revoked"]
Rejection = BadCode | Literal["rate_limited"]
Refusal = Literal["unregistered", "revoked"]

registered = meter.create_counter(
    "inventory.devices.registered",
    unit="{device}",
    description="Devices registered with a code",
)
rejected = meter.create_counter(
    "inventory.registrations.rejected",
    unit="{registration}",
    description="Registration attempts refused, by reason",
)
refused = meter.create_counter(
    "inventory.devices.refused",
    unit="{request}",
    description="Requests refused for want of a registered device, by reason",
)


@functools.cache
def words() -> tuple[str, ...]:
    """The wordlist, without the few entries that are not plain letters, so
    a phrase can be typed with any separator."""
    text = resources.files("inventory_tng").joinpath("data/eff_large_wordlist.txt")
    entries = (line.split("\t")[1] for line in text.read_text().splitlines())
    return tuple(word for word in entries if word.isascii() and word.isalpha())


def new_phrase() -> str:
    return "-".join(secrets.choice(words()) for _ in range(PHRASE_WORDS))


def normalise_phrase(typed: str) -> str:
    """ "Abacus  Tulip", "abacus-tulip" and "abacus_tulip" are one phrase."""
    return "-".join(re.findall(r"[a-z]+", typed.lower()))


def digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def create_code(
    session: Session,
    *,
    max_uses: int,
    lifetime: timedelta,
    label: str | None = None,
) -> tuple[RegistrationCode, str]:
    """A new code, and its phrase: the only time the phrase is known."""
    phrase = new_phrase()
    code = RegistrationCode(
        phrase_hash=digest(phrase),
        label=label,
        max_uses=max_uses,
        expires_at=datetime.now(UTC) + lifetime,
    )
    session.add(code)
    session.flush()
    log.info(
        "registration code created", registration_code_id=code.id, max_uses=max_uses
    )
    return code, phrase


class RegistrationRejected(Exception):
    def __init__(self, reason: Rejection) -> None:
        super().__init__(reason)
        self.reason: Rejection = reason


def register(session: Session, phrase: str, address: str | None) -> tuple[Device, str]:
    """Check the code, then register a device with an id of its own. Returns
    the device and its cookie value, which is never stored. Every attempt
    counts before the code is checked, so a burst sent at once cannot all
    slip under the limit (inventory-tng-8i4.126). One whose phrase is a
    code's is no guess: refunded at once, before the code's row is waited
    for, so a room of phones behind one address scanning one code can all
    register."""
    bucket = address or "unknown"
    if not limiter(session).hit(ATTEMPTS, "register", bucket):
        _reject("rate_limited", None)
    if not exists_where(session, RegistrationCode.phrase_hash == _phrase_hash(phrase)):
        _reject("unknown", None)
    refund(session, ATTEMPTS, "register", bucket)
    code = _usable_code(session, phrase)
    code.uses += 1
    token = secrets.token_urlsafe(16)
    device = Device(
        token_hash=digest(token),
        registration_code_id=code.id,
        first_seen_from=_ip(address),
    )
    session.add(device)
    session.flush()
    registered.add(1)
    log.info("device registered", device_id=device.id, registration_code_id=code.id)
    return device, token


def _usable_code(session: Session, phrase: str) -> RegistrationCode:
    # Locked, so two phones cannot both take a code's last use.
    code = session.scalar(
        locked(
            select(RegistrationCode).where(
                RegistrationCode.phrase_hash == _phrase_hash(phrase)
            )
        )
    )
    if code is None:
        _reject("unknown", None)
    if code.revoked_at is not None:
        _reject("revoked", code)
    if code.expires_at <= datetime.now(UTC):
        _reject("expired", code)
    if code.uses >= code.max_uses:
        _reject("used_up", code)
    return code


def _phrase_hash(phrase: str) -> bytes:
    return digest(normalise_phrase(phrase))


def _ip(address: str | None) -> IPv4Address | IPv6Address | None:
    """The address, if it is one: a test client or a Unix socket gives none."""
    try:
        return ip_address(address) if address else None
    except ValueError:
        return None


def _reject(reason: Rejection, code: RegistrationCode | None) -> NoReturn:
    rejected.add(1, {"reason": reason})
    log.warning(
        "registration rejected",
        reason=reason,
        registration_code_id=code.id if code else None,
    )
    raise RegistrationRejected(reason)


def revoke_code(session: Session, code_id: int) -> bool:
    """Stop a code registering anything more. Its devices stay registered:
    devices_registered_with() is how to look them over. False if there is no
    such current code."""
    return _revoke(session, RegistrationCode, code_id, "registration_code_id")


def revoke_device(session: Session, device_id: int) -> bool:
    """Refuse this device from its next request. False if there is no such
    current device."""
    return _revoke(session, Device, device_id, "device_id")


def _revoke(
    session: Session, model: type[RegistrationCode | Device], row_id: int, field: str
) -> bool:
    # Locked first, so of two revokes at once the second sees the first's;
    # key_share, as an update does, so it does not wait for saves the
    # device is making, which refer to its row.
    found = get_locked(session, model, row_id, key_share=True)
    if found is None or found.revoked_at is not None:
        return False
    found.revoked_at = datetime.now(UTC)
    log.info(f"{model.__tablename__.replace('_', ' ')} revoked", **{field: row_id})
    return True


def devices_registered_with(
    session: Session, code_id: int
) -> list[tuple[Device, timedelta | None]] | None:
    """A code's devices, newest first, each with how long before the code's
    revocation it registered (None while the code stands). Those registered
    just before are the ones to look at first. None if there is no such
    code."""
    code = session.get(RegistrationCode, code_id)
    if code is None:
        return None
    devices = session.scalars(
        select(Device)
        .where(Device.registration_code_id == code_id)
        .order_by(Device.created_at.desc(), Device.id.desc())
    ).all()
    return [
        (device, code.revoked_at - device.created_at if code.revoked_at else None)
        for device in devices
    ]


class DeviceRefused(Exception):
    def __init__(self, reason: Refusal) -> None:
        super().__init__(reason)
        self.reason: Refusal = reason


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


class Registration(BaseModel):
    # The phrase as typed, or as the QR code's link carries it.
    code: str


class Registered(BaseModel):
    device_id: int


class CodeRefusedReply(BaseModel):
    reason: BadCode


class RateLimitedReply(BaseModel):
    reason: Literal["rate_limited"]


class DeviceRefusedReply(BaseModel):
    reason: Refusal


PATH = "/devices"
# The page a registration code's link and QR code open (bundle.PAGES).
REGISTER_PATH = "/register"


@router.post(
    PATH,
    status_code=201,
    responses={
        400: {
            "model": CodeRefusedReply,
            "description": "The code is not one that registers",
        },
        429: {"model": RateLimitedReply, "description": "Too many attempts from here"},
    },
)
def register_device(
    registration: Registration, request: Request, response: Response, session: DbSession
) -> Registered:
    """Register this browser with a code, and give it its cookie."""
    device, token = register(session, registration.code, client_address(request))
    session.commit()
    set_private_cookie(request, response, COOKIE, token, max_age=COOKIE_MAX_AGE)
    return Registered(device_id=device.id)


def install(app: FastAPI) -> None:
    """The responses for a refused device or code, which pages and the scan
    page can show."""

    @app.exception_handler(DeviceRefused)
    def device_refused(_request: Request, error: DeviceRefused) -> JSONResponse:
        body = DeviceRefusedReply(reason=error.reason)
        return JSONResponse(body.model_dump(), status_code=403)

    @app.exception_handler(RegistrationRejected)
    def registration_rejected(
        _request: Request, error: RegistrationRejected
    ) -> JSONResponse:
        if error.reason == "rate_limited":
            body = RateLimitedReply(reason=error.reason).model_dump()
            return JSONResponse(body, status_code=429)
        body = CodeRefusedReply(reason=error.reason).model_dump()
        return JSONResponse(body, status_code=400)
