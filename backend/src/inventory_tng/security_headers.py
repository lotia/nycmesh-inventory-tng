"""The headers every response carries for the browser's protection
(docs/decisions/0010, inventory-tng-e0y): an enforced Content-Security-Policy,
and the endpoint its violations are reported to; nosniff; the
Referrer-Policy; and the Permissions-Policy.

The policy allows scripts, styles, images and requests from this origin
only. No page has an inline script, a <style> or a style attribute: what a
page does is a file Vite builds, and its look a class in the shared
stylesheet (CONTRIBUTING.md, Styles). The decoder's WebAssembly needs
'wasm-unsafe-eval'. In development, Vite's dev server serves the scripts and
styles, reloads them over a websocket, and adds each stylesheet as a
<style>, which the policy allows by a nonce made for each response
(pages.csp_nonce, which the pages put where Vite's client reads it). So does the
API's docs page, FastAPI's, for the one inline script that starts Swagger UI,
which comes from its CDN: in development only, as the CDN's script would run
as this origin's, a signed-in administrator's requests included.

A violation is counted by its directive, from a fixed list, and nothing else
a report says is kept or logged: it names pages, addresses and what the page
held. Reports count against their address as the pages' telemetry does
(page_telemetry.py).
"""

import json
import secrets
from collections.abc import Iterable

import structlog
from fastapi import APIRouter, Request, Response
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from opentelemetry import metrics
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from inventory_tng import page_telemetry
from inventory_tng.devices import RateLimitedReply
from inventory_tng.pages import NONCE, csp_nonce
from inventory_tng.phrases import BRAND

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

router = APIRouter()

# Where browsers send violation reports: report-uri's address, and the
# Reporting API's endpoint, named by report-to.
REPORTS = "/api/csp-reports"
REPORT_GROUP = "csp"
# The API's docs page (app.py), FastAPI's Swagger UI, and where its script
# and stylesheet come from (fastapi.openapi.docs): that package only, not
# the rest of the CDN.
DOCS = "/api/docs"
SWAGGER_CDN = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/"
SWAGGER_FAVICON = "https://fastapi.tiangolo.com"

NONCE_BYTES = 16

# What browsers send reports as: report-uri's, the Reporting API's, and
# plain JSON, which older browsers used.
REPORT_TYPES = frozenset(
    {"application/csp-report", "application/reports+json", "application/json"}
)
# A report is a few hundred bytes, the Reporting API's a list of them.
MOST_BYTES = 32 * 1024
MOST_REPORTS = 20
# The directives counted by name; any other is "other".
DIRECTIVES = frozenset(
    {
        "default-src",
        "script-src",
        "script-src-elem",
        "script-src-attr",
        "style-src",
        "style-src-elem",
        "style-src-attr",
        "img-src",
        "font-src",
        "connect-src",
        "media-src",
        "object-src",
        "frame-src",
        "child-src",
        "worker-src",
        "manifest-src",
        "frame-ancestors",
        "form-action",
        "base-uri",
    }
)
OTHER = "other"

# What the pages may use: the camera, on this origin, for the scan page.
# Every other feature a page could ask for, denied.
PERMISSIONS = ", ".join(
    ["camera=(self)"]
    + [
        f"{feature}=()"
        for feature in (
            "accelerometer",
            "display-capture",
            "geolocation",
            "gyroscope",
            "hid",
            "magnetometer",
            "microphone",
            "midi",
            "payment",
            "serial",
            "usb",
        )
    ]
)
# No address is sent on: reset and sign-in links carry their token in it.
REFERRER_POLICY = "no-referrer"

violations = meter.create_counter(
    "inventory.csp.violations",
    unit="{violation}",
    description="Content-Security-Policy violations browsers reported, by directive",
)


def content_security_policy(
    *, dev_server: str | None = None, nonce: str | None = None, docs: bool = False
) -> str:
    """The policy for a response: with Vite's dev server (development only),
    and the nonce of what it adds; or for the API's docs page."""
    dev = [dev_server] if dev_server else []
    # Vite reloads over a websocket at its own address.
    reloads = [dev_server.replace("http", "ws", 1)] if dev_server else []
    allowed = [f"'nonce-{nonce}'"] if nonce else []
    sources = {
        "default-src": ["'self'", *dev],
        "script-src": ["'self'", "'wasm-unsafe-eval'", *dev],
        "style-src": ["'self'", *dev],
        "img-src": ["'self'", *dev],
        "connect-src": ["'self'", *dev, *reloads],
        "object-src": ["'none'"],
        "base-uri": ["'none'"],
        "frame-ancestors": ["'none'"],
        "form-action": ["'self'"],
        "report-uri": [REPORTS],
        "report-to": [REPORT_GROUP],
    }
    if docs:
        # Swagger UI from its CDN, started by the page's own script; its
        # stylesheet draws some icons from data: URLs.
        sources["script-src"] += [SWAGGER_CDN, *allowed]
        sources["style-src"] += [SWAGGER_CDN]
        sources["img-src"] += ["data:", SWAGGER_FAVICON]
    else:
        # Vite's styles.
        sources["style-src"] += allowed
    return "; ".join(" ".join([name, *values]) for name, values in sources.items())


class SecurityHeaders:
    """Adds the headers to every response, the app's error pages and its
    static files included. `dev_server`: Vite's, and `docs`, whether the
    API's docs page is served: both in development only."""

    def __init__(
        self, app: ASGIApp, *, dev_server: str | None, docs: bool = False
    ) -> None:
        self.app = app
        self.dev_server = dev_server
        self.docs = docs
        # Every response's, but for a nonce'd policy where one is needed.
        self.headers = {
            "Content-Security-Policy": content_security_policy(),
            "Reporting-Endpoints": f'{REPORT_GROUP}="{REPORTS}"',
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": REFERRER_POLICY,
            "Permissions-Policy": PERMISSIONS,
        }

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = self.headers
        docs = self.docs and scope["path"] == DOCS
        if self.dev_server is not None or docs:
            nonce = secrets.token_urlsafe(NONCE_BYTES)
            scope.setdefault("state", {})[NONCE] = nonce
            policy = content_security_policy(
                dev_server=self.dev_server, nonce=nonce, docs=docs
            )
            headers = headers | {"Content-Security-Policy": policy}

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).update(headers)
            await send(message)

        await self.app(scope, receive, send_with_headers)


# The API's docs page, which app.py adds in development only.
docs_router = APIRouter()


@docs_router.get(DOCS, include_in_schema=False)
def docs(request: Request) -> HTMLResponse:
    """FastAPI's docs page, its inline script allowed by the nonce, which is
    this response's only: never kept."""
    page = get_swagger_ui_html(
        openapi_url=str(request.app.openapi_url), title=f"{BRAND} API"
    )
    nonce = csp_nonce(request)
    body = bytes(page.body).replace(b"<script>", f'<script nonce="{nonce}">'.encode())
    return HTMLResponse(body, headers={"Cache-Control": "no-store"})


def violated(report: object) -> list[str]:
    """Each violation's directive, from report-uri's one report or the
    Reporting API's list (its csp-violation reports, MOST_REPORTS at most);
    "other" for one not in DIRECTIVES. One that names none is left out."""
    found: Iterable[object]
    if isinstance(report, dict) and isinstance(body := report.get("csp-report"), dict):
        # Older browsers name the whole directive, sources and all.
        named = body.get("violated-directive")
        first = named.split()[0] if isinstance(named, str) and named.strip() else None
        found = [body.get("effective-directive") or first]
    elif isinstance(report, list):
        found = (
            entry["body"].get("effectiveDirective")
            for entry in report[:MOST_REPORTS]
            if isinstance(entry, dict)
            and entry.get("type") == "csp-violation"
            and isinstance(entry.get("body"), dict)
        )
    else:
        return []
    return [
        name if isinstance(name, str) and name in DIRECTIVES else OTHER
        for name in found
        if name is not None
    ]


async def _body(request: Request) -> bytes | None:
    """The request's body; None past MOST_BYTES."""
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MOST_BYTES:
            return None
    return bytes(body)


# Async: it works in memory only, as page_telemetry.report does.
@router.post(REPORTS, status_code=204, include_in_schema=False)
async def report(request: Request) -> Response:
    """Count what a browser reports its policy refused."""
    # A page's violations come a few at a time, and each is one request in
    # browsers that use report-uri.
    limit = request.app.state.settings.rate_limits.csp_reports
    if page_telemetry.over_limit(request, limit, "csp"):
        log.info("policy report refused", reason="rate_limited")
        body = RateLimitedReply(reason="rate_limited").model_dump()
        return JSONResponse(body, status_code=429)
    content_type = request.headers.get("content-type", "").split(";")[0]
    if content_type.strip().lower() not in REPORT_TYPES:
        log.info("policy report refused", reason="not_a_report_type")
        return Response(status_code=415)
    raw = await _body(request)
    if raw is None:
        log.info("policy report refused", reason="too_large")
        return Response(status_code=413)
    try:
        found = violated(json.loads(raw))
    except ValueError, RecursionError:
        found = []
    if not found:
        log.info("policy report refused", reason="not_a_report")
        return Response(status_code=400)
    for directive in found:
        violations.add(1, {"directive": directive})
    log.warning(
        "content security policy violated",
        directives=sorted(set(found)),
        count=len(found),
    )
    return Response(status_code=204)
