"""Server-rendered pages (docs/decisions/0002): their templates, and how a
page or a redirect is sent."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def page(
    request: Request,
    template: str,
    context: Mapping[str, Any],
    status_code: int = 200,
) -> HTMLResponse:
    """A page, never cached: some show secrets once, and all show what only
    the person signed in may see. Forms read `problems` and `values`."""
    return templates.TemplateResponse(
        request,
        template,
        {"problems": {}, "values": {}} | dict(context),
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


def redirect(path: str) -> RedirectResponse:
    """After a form, so reloading does not send it again."""
    return RedirectResponse(path, status_code=303)
