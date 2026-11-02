"""Small things every route that talks to a browser needs alike."""

from datetime import datetime

from fastapi import Request, Response


def client_address(request: Request) -> str | None:
    """The address uvicorn resolved (FORWARDED_ALLOW_IPS), which rate limits
    are kept by; None from a test client or a Unix socket."""
    return request.client.host if request.client else None


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
