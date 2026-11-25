"""Writes the numbers in a request's words, its pages', refusals' and
notices' alike, with the decimal separator its number boxes take
(web/decimal_separator.py, phrases.writing_numbers), never grouped:
"1500 m" and "1.5 m", or "1,5 m" on a page whose boxes take a comma (Ali,
2026-11-23, inventory-tng-4mc3; 2026-11-24, inventory-tng-ua04).
Mail is written as anywhere else (core/mail.py), and so are printed labels
(labels/labels.py)."""

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from inventory_tng.core.phrases import writing_numbers
from inventory_tng.web.decimal_separator import separator_for


class NumbersInWords:
    """Middleware writing each HTTP request's numbers in words with its
    decimal separator, until its reply and the tasks after it end."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # Not logged: decimal_separator logs it once, where a page or form
        # takes it, not for every request.
        header = Headers(scope=scope).get("accept-language", "")
        with writing_numbers(separator_for(header)):
            await self.app(scope, receive, send)
