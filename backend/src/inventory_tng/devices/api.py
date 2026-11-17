"""The devices' JSON endpoint, POST /api/devices: where a browser
registers with a code (devices.register), and the replies to a refused
device or code."""

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse

from inventory_tng.core.db import DbSession
from inventory_tng.devices.devices import (
    COOKIE,
    COOKIE_MAX_AGE,
    CodeRefusedReply,
    DeviceRefused,
    DeviceRefusedReply,
    RateLimitedReply,
    Registered,
    Registration,
    RegistrationRejected,
    register,
)
from inventory_tng.web.browser import client_address, set_private_cookie

router = APIRouter()

PATH = "/devices"


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
