"""A request id on every log line written during a request, and one line per
request (docs/decisions/0006)."""

import re
import secrets
import time
from collections.abc import Iterable

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = structlog.get_logger(__name__)

REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
TRACEPARENT = re.compile(r"[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}")


def request_id(headers: Iterable[tuple[bytes, bytes]]) -> str:
    """The ingress's X-Request-ID if it is well formed, else the trace id of
    its traceparent, else a new one."""
    found = Headers(raw=list(headers))
    if REQUEST_ID.fullmatch(given := found.get("x-request-id", "")):
        return given
    if match := TRACEPARENT.fullmatch(found.get("traceparent", "")):
        return match.group(1)
    return secrets.token_hex(16)


class RequestLogging:
    """Binds the request id for every line logged while handling a request,
    echoes it in the response, and logs the request when it ends.

    Starlette's own error handler sits outside every middleware, so a failure
    is caught here instead: logged at error, with the request id, and answered
    with a 500 that carries the id too. An app-wide exception handler would
    therefore never see it; this app registers none.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        client_address: bool,
        quiet_paths: frozenset[str],
    ) -> None:
        self.app = app
        self.client_address = client_address
        self.quiet_paths = quiet_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = request_id(scope["headers"])
        status: int | None = None
        started = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message).append("x-request-id", rid)
            await send(message)

        with structlog.contextvars.bound_contextvars(request_id=rid):
            details = {"method": scope["method"], "path": scope["path"]}
            if self.client_address and scope.get("client"):
                details["client"] = scope["client"][0]
            try:
                await self.app(scope, receive, send_with_id)
            except Exception:
                log.exception(
                    "request failed",
                    **details,
                    status=status or 500,
                    duration_ms=_ms(started),
                )
                if status is not None:
                    raise  # The response has begun; there is no 500 to send.
                error = PlainTextResponse("Internal Server Error", status_code=500)
                await error(scope, receive, send_with_id)
                return
            # Successful requests to quiet paths go to debug, where
            # LOG_LEVEL=DEBUG still shows them. Failures always show.
            quiet = scope["path"] in self.quiet_paths and status == 200
            emit = log.debug if quiet else log.info
            emit("request", **details, status=status, duration_ms=_ms(started))


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
