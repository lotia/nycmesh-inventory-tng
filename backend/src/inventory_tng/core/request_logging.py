"""A request id on every log line written during a request, and one line per
request (docs/decisions/0006)."""

import re
import secrets
import time
from collections.abc import Callable, Iterable
from contextvars import ContextVar

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.typing import EventDict

log = structlog.get_logger(__name__)

# Fields bound during a request, the request line's included. One mutable
# dict per request: a copy of the context (a sync dependency, a thread) holds
# the same dict, so what it binds is still seen afterwards.
_fields: ContextVar[dict[str, object] | None] = ContextVar(
    "inventory_request_fields", default=None
)

REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
TRACEPARENT = re.compile(r"[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}")


def bind(**fields: object) -> None:
    """Put fields on every later line of this request, the request line
    included, from anywhere in it. structlog's own bind_contextvars() does not
    reach the request line: something between this middleware and the route
    runs in a copy of the context. Outside a request, it is that."""
    current = _fields.get()
    if current is None:
        structlog.contextvars.bind_contextvars(**fields)
    else:
        current.update(fields)


def request_fields() -> dict[str, object]:
    return dict(_fields.get() or {})


def merge_request_fields(_logger: object, _method: str, event: EventDict) -> EventDict:
    """A structlog processor (logs.SHARED): the fields bind() added."""
    for key, value in (_fields.get() or {}).items():
        event.setdefault(key, value)
    return event


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
    with a 500 that carries the id too: `failed`'s answer (pages.ErrorPages),
    or plain text if that fails as well. An app-wide handler for Exception
    would therefore never see it; the app's handlers are for the errors it
    raises on purpose, such as HTTPException.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        client_address: bool,
        quiet_paths: frozenset[str],
        failed: Callable[[Scope], Response],
    ) -> None:
        self.app = app
        self.failed = failed
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

        fields = _fields.set({})
        try:
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
                    await self._failure(scope)(scope, receive, send_with_id)
                    return
                # Successful requests to quiet paths go to debug, where
                # LOG_LEVEL=DEBUG still shows them. Failures always show.
                quiet = scope["path"] in self.quiet_paths and status == 200
                emit = log.debug if quiet else log.info
                emit("request", **details, status=status, duration_ms=_ms(started))

        finally:
            _fields.reset(fields)

    def _failure(self, scope: Scope) -> Response:
        try:
            return self.failed(scope)
        except Exception:
            log.exception("the failure page failed")
            return PlainTextResponse("Internal Server Error", status_code=500)


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
