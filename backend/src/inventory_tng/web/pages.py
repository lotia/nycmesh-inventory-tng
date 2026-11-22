"""Server-rendered pages (docs/decisions/0002): their templates, and how a
page or a redirect is sent."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, NoReturn, cast
from urllib.parse import urlencode

from babel.support import LazyProxy
from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException
from starlette.types import Scope

from inventory_tng.core.db_errors import Gone
from inventory_tng.core.local_time import show, show_day
from inventory_tng.core.phrases import BRAND, lazy_gettext, number
from inventory_tng.web.formatting import (
    amount,
    keyboard,
    plain_amount,
    printed_length,
)
from inventory_tng.web.navigation import sections, sections_without_badges
from inventory_tng.web.template_phrases import install

templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
install(templates.env)
# The app's name, the same in every language (phrases.BRAND).
cast(dict[str, Any], templates.env.globals)["BRAND"] = BRAND
# {{ moment | when(zone) }}: a stored UTC time as people read it
# (docs/decisions/0013); {{ moment | day(zone) }}, its day alone ("14 Nov").
templates.env.filters["when"] = show
templates.env.filters["day"] = show_day
# {{ size | number }}: a number as the active locale writes it ("1,000").
templates.env.filters["number"] = number

# Where a response's Content-Security-Policy nonce is kept, in the
# request's state (web/security_headers.py).
NONCE = "csp_nonce"


def csp_nonce(request: Request) -> str | None:
    """This response's nonce; None where the policy needs none, as outside
    development."""
    return request.scope.get("state", {}).get(NONCE)


cast(dict[str, Any], templates.env.globals)["csp_nonce"] = csp_nonce

# Where the administrator a request is signed in as is kept, in its state:
# what an error page's masthead shows, read without the database.
_SIGNED_IN_AS = "signed_in_as"


@dataclass(frozen=True)
class SignedInAs:
    """What the masthead reads of the administrator signed in (_masthead.html),
    as plain values: an ORM row may be detached or expired by the time an
    error page is drawn, and reading it would query the database."""

    username: str


def remember_signed_in(request: Request, username: str) -> None:
    """Keep, for an error page later in this request, who the sign-in guard
    found (admins/guards.signed_in)."""
    request.scope.setdefault("state", {})[_SIGNED_IN_AS] = SignedInAs(username)


def signed_in_as(request: Request) -> SignedInAs | None:
    """Who this request was found signed in as; None if nothing checked."""
    return request.scope.get("state", {}).get(_SIGNED_IN_AS)


# {{ quantity | amount }}: a quantity as people read it ("1,234.5"); a
# form's box or hidden field takes {{ quantity | plain_amount }} ("1234.5"),
# as the form reads it back.
templates.env.filters["amount"] = amount
templates.env.filters["plain_amount"] = plain_amount
templates.env.filters["printed_length"] = printed_length
templates.env.filters["keyboard"] = keyboard


# What a page says after a change, by the ?notice= it was sent back with
# (redirect(..., notice=)). A fixed list: a link can only show one of these.
# Each module adds its own with add_notices(); this view can't be changed.
_NOTICES: dict[str, str | LazyProxy] = {}
NOTICES = MappingProxyType(_NOTICES)
# Where on a page a notice is said: by the heading of the section with this
# id (_notice.html), which the redirect also scrolls to; without one, under
# the page's title. A fixed list, which a test checks against the templates'
# sections both ways.
NOTICE_SECTIONS = frozenset(
    {
        "adjust",
        "aliases",
        "authenticators",
        "count",
        "email",
        "holding",
        "open-packages",
        "packages",
        "password",
        "reasons",
        "recovery-codes",
        "retired",
        "scan-page",
        "sessions",
        "sign-in",
        "time-zone",
    }
)


def add_notices(notices: Mapping[str, str | LazyProxy]) -> None:
    """Adds a module's notices to NOTICES. A key another module has
    already added is refused: one key with two pages' words would have
    whichever module was imported last speak for both."""
    taken = _NOTICES.keys() & notices.keys()
    if taken:
        raise ValueError(f"notice already added: {', '.join(sorted(taken))}")
    _NOTICES.update(notices)


def page(
    request: Request,
    template: str,
    context: Mapping[str, Any],
    status_code: int = 200,
) -> HTMLResponse:
    """A page, never cached: some show secrets once, and all show what only
    the person signed in may see. Forms read `problems` and `values`. A page
    for an administrator (`admin`) has the navigation, unless the context
    gives its `sections`, read already."""
    notice = NOTICES.get(request.query_params.get("notice", ""))
    section = request.query_params.get("section", "")
    full = {
        "problems": {},
        "values": {},
        "notice": notice,
        # Only its section's heading says it (_notice.html), if there is one.
        "notice_section": section if notice and section in NOTICE_SECTIONS else None,
    } | dict(context)
    if full.get("admin") is not None and full.get("sections") is None:
        full["sections"] = sections(full["admin"])
    return templates.TemplateResponse(
        request,
        template,
        full,
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


def not_found() -> NoReturn:
    """There is no such page, or nothing here by that id: answered as
    install() says."""
    raise HTTPException(status_code=404)


def redirect(
    path: str, *, notice: str | None = None, section: str | None = None, **query: str
) -> RedirectResponse:
    """After a form, so reloading does not send it again; `notice`, one of
    NOTICES, is said on the page it goes to, by the heading of `section` if
    given (one of NOTICE_SECTIONS), which the browser scrolls to."""
    if notice is not None:
        if notice not in NOTICES:
            raise ValueError(f"no such notice: {notice}")
        query["notice"] = notice
    if section is not None:
        if notice is None or section not in NOTICE_SECTIONS:
            raise ValueError(f"no notice for section {section!r}")
        query["section"] = section
    if query:
        path += ("&" if "?" in path else "?") + urlencode(query)
    if section is not None:
        path += f"#{section}"
    return RedirectResponse(path, status_code=303)


# What a page answering an error says, by status code: 400 for any other
# client error, 500 for any server error.
DID_NOT_WORK = lazy_gettext("That didn't work")
NOT_TAKEN = lazy_gettext(
    "The inventory could not take that as it was sent. Go back and try again."
)
NOT_FOUND = lazy_gettext("Not found")
NOTHING_HERE = lazy_gettext("There is nothing here. The link may be old, or mistyped.")
NOT_HERE = lazy_gettext("Not here")
NOT_AT_THIS_ADDRESS = lazy_gettext("That can't be done at this address.")
WENT_WRONG = lazy_gettext("Something went wrong")
COULD_NOT_DO = lazy_gettext(
    "The inventory could not do that. Try again; if it happens again, "
    "tell whoever looks after it."
)
ERRORS = {
    400: (DID_NOT_WORK, NOT_TAKEN),
    404: (NOT_FOUND, NOTHING_HERE),
    405: (NOT_HERE, NOT_AT_THIS_ADDRESS),
    500: (WENT_WRONG, COULD_NOT_DO),
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
        under_admin = self._under(request, self.admin)
        context: dict[str, Any] = {
            "title": title,
            "words": words,
            "scan": self.scan,
            # The logo goes where this person most likely came from.
            "brand_home": self.admin if under_admin else self.scan,
        }
        # Under the administrator pages, signed in as this request found:
        # their masthead, with no badges, as the database is not read here.
        # A mistyped address checked no sign-in, so its page stays plain.
        admin = signed_in_as(request)
        if under_admin and admin is not None:
            context |= {"admin": admin, "sections": sections_without_badges()}
        return page(request, "error.html", context, status)

    def failed(self, scope: Scope) -> Response:
        """A request that raised (request_logging.RequestLogging)."""
        request = Request(scope)
        if self.in_api(request):
            return JSONResponse({"detail": "Internal Server Error"}, 500)
        return self.page(request, 500)

    def install(self, app: FastAPI) -> None:
        @app.exception_handler(Gone)
        async def gone(request: Request, _error: Gone) -> Response:
            """What a change was for is not as the page found it any more:
            there is nothing here by that id, as there would be on a fresh
            look."""
            return await http_error(request, HTTPException(status_code=404))

        @app.exception_handler(HTTPException)
        async def http_error(request: Request, error: HTTPException) -> Response:
            # Below 400 (a 304, say) is no error page's to answer.
            if self.in_api(request) or error.status_code < 400:
                return await http_exception_handler(request, error)
            answer = self.page(request, error.status_code)
            answer.headers.update(error.headers or {})
            return answer
