"""Small things every route that talks to a browser needs alike."""

from datetime import datetime

from fastapi import Request, Response
from limits import RateLimitItemPerMinute
from limits.strategies import MovingWindowRateLimiter


def client_address(request: Request) -> str | None:
    """The address uvicorn resolved (FORWARDED_ALLOW_IPS); None over a Unix
    socket."""
    return request.client.host if request.client else None


def rate_key(request: Request) -> str:
    """What rate limits by address count under: the address, or one shared
    "unknown" for requests without one."""
    return client_address(request) or "unknown"


def over_limit(request: Request, per_minute: int, kind: str) -> bool:
    """Whether a report of this kind from this address is more than
    per_minute allows, counted in this process's reports
    (scan/page_telemetry.py's install)."""
    reports: MovingWindowRateLimiter = request.app.state.page_reports
    allowed = RateLimitItemPerMinute(per_minute)
    return not reports.hit(allowed, kind, rate_key(request))


def set_private_cookie(
    request: Request,
    response: Response,
    name: str,
    value: str,
    *,
    max_age: int | None = None,
    expires: datetime | None = None,
) -> None:
    """A cookie only this site's pages send, never readable by script, and
    only over https outside development."""
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        expires=expires,
        httponly=True,
        secure=not request.app.state.settings.development,
        samesite="lax",
    )
