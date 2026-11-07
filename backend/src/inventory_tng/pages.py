"""Server-rendered pages (docs/decisions/0002): their templates, and how a
page or a redirect is sent."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException
from starlette.types import Scope

from inventory_tng.local_time import show

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
# {{ moment | when(zone) }}: a stored UTC time as people read it
# (docs/decisions/0013).
templates.env.filters["when"] = show


# What a page says after a change, by the ?notice= it was sent back with
# (redirect(..., notice=)). A fixed list: a link can only show one of these.
NOTICES: dict[str, str] = {}


def page(
    request: Request,
    template: str,
    context: Mapping[str, Any],
    status_code: int = 200,
) -> HTMLResponse:
    """A page, never cached: some show secrets once, and all show what only
    the person signed in may see. Forms read `problems` and `values`."""
    notice = NOTICES.get(request.query_params.get("notice", ""))
    return templates.TemplateResponse(
        request,
        template,
        {"problems": {}, "values": {}, "notice": notice} | dict(context),
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


def not_found() -> NoReturn:
    """There is no such page, or nothing here by that id: answered as
    install() says."""
    raise HTTPException(status_code=404)


def redirect(path: str, *, notice: str | None = None, **query: str) -> RedirectResponse:
    """After a form, so reloading does not send it again; `notice`, one of
    NOTICES, is said on the page it goes to."""
    if notice is not None:
        if notice not in NOTICES:
            raise ValueError(f"no such notice: {notice}")
        query["notice"] = notice
    if query:
        path += ("&" if "?" in path else "?") + urlencode(query)
    return RedirectResponse(path, status_code=303)


# What a page answering an error says, by status code: 400 for any other
# client error, 500 for any server error.
ERRORS = {
    400: (
        "That didn't work",
        "The inventory could not take that as it was sent. Go back and try again.",
    ),
    404: ("Not found", "There is nothing here. The link may be old, or mistyped."),
    405: ("Not here", "That can't be done at this address."),
    500: (
        "Something went wrong",
        (
            "The inventory could not do that. Try again; if it happens again, "
            "tell whoever looks after it."
        ),
    ),
}


class ErrorPages:
    """How an error is answered: on a page, with a page of the app's own
    (error.html) and a way on; under `api`, with FastAPI's JSON."""

    def __init__(self, *, api: str, scan: str, admin: str) -> None:
        self.api, self.scan, self.admin = api, scan, admin

    def _under(self, request: Request, prefix: str) -> bool:
        """Whether the path, behind any proxy's prefix, is `prefix` or in it."""
        path = request.url.path.removeprefix(request.scope.get("root_path", ""))
        return path == prefix or path.startswith(prefix + "/")

    def in_api(self, request: Request) -> bool:
        return self._under(request, self.api)

    def page(self, request: Request, status: int) -> Response:
        title, words = ERRORS.get(status) or ERRORS[400 if status < 500 else 500]
        # The logo goes where this person most likely came from.
        home = self.admin if self._under(request, self.admin) else self.scan
        context = {
            "title": title,
            "words": words,
            "scan": self.scan,
            "brand_home": home,
        }
        return page(request, "error.html", context, status)

    def failed(self, scope: Scope) -> Response:
        """A request that raised (request_logging.RequestLogging)."""
        request = Request(scope)
        if self.in_api(request):
            return JSONResponse({"detail": "Internal Server Error"}, 500)
        return self.page(request, 500)

    def install(self, app: FastAPI) -> None:
        @app.exception_handler(HTTPException)
        async def http_error(request: Request, error: HTTPException) -> Response:
            # Below 400 (a 304, say) is no error page's to answer.
            if self.in_api(request) or error.status_code < 400:
                return await http_exception_handler(request, error)
            answer = self.page(request, error.status_code)
            answer.headers.update(error.headers or {})
            return answer
