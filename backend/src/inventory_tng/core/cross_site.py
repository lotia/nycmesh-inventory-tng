"""Refuses requests that change something when another site's page sent them
(docs/decisions/0012), instead of CSRF tokens.

Browsers say where a request came from: Sec-Fetch-Site, or, in older ones,
Origin. A request with neither did not come from a browser page, so a
forged one cannot be made that way. Go's net/http CrossOriginProtection
works the same way.
"""

from typing import Unpack
from urllib.parse import urlsplit

import structlog
from opentelemetry import metrics
from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from inventory_tng.core.phrases import lazy_gettext
from inventory_tng.core.refusals import Fields, refusals

log = structlog.get_logger(__name__)


@refusals(
    log,
    metrics.get_meter(__name__).create_counter(
        "inventory.cross_site.refused",
        unit="{request}",
        description="Requests that change something refused as another "
        "site's page sent them",
    ),
)
def refused(event: str, **fields: Unpack[Fields]) -> None: ...


REFUSED = lazy_gettext("Refused: this came from another site's page.")

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# "none" is the person typing the address or using a bookmark.
SAME_SITE = frozenset({"same-origin", "none"})


def cross_site(method: str, headers: Headers) -> bool:
    if method in SAFE_METHODS:
        return False
    if (site := headers.get("sec-fetch-site")) is not None:
        return site not in SAME_SITE
    if (origin := headers.get("origin")) is not None:
        # Compared with Host: a proxy in front may have changed the scheme.
        return origin == "null" or urlsplit(origin).netloc != headers.get("host")
    return False


class CrossSiteRefusal:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and cross_site(
            scope["method"], Headers(scope=scope)
        ):
            refused("cross-site request refused", level="warning", path=scope["path"])
            response = PlainTextResponse(str(REFUSED), status_code=403)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
