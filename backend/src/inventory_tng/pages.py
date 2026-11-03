"""Server-rendered pages (docs/decisions/0002): their templates, and how a
page or a redirect is sent."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

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


def not_found() -> HTMLResponse:
    return HTMLResponse("Not found", status_code=404)


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
