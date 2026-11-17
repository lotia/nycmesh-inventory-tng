"""The headers every response carries for the browser's protection: an
enforced Content-Security-Policy and where its violations are reported,
nosniff, the Referrer-Policy and the Permissions-Policy (security_headers.py,
docs/decisions/0010; inventory-tng-e0y)."""

import base64
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable, Iterator, MutableMapping
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from structlog.testing import capture_logs

from inventory_tng import security_headers, sign_in
from inventory_tng.bundle import MANIFEST
from inventory_tng.local_env import CLONE
from inventory_tng.settings import FrontendConfig, RateLimits

# A build with every entry the pages load, and one file to serve.
MANIFEST_ENTRIES = {
    "src/scan.tsx": {"file": "assets/scan-a.js", "isEntry": True},
    "src/register.tsx": {"file": "assets/register-a.js", "isEntry": True},
    "src/admin.ts": {"file": "assets/admin-a.js", "isEntry": True},
    "src/styles.css": {"file": "assets/styles-a.css", "isEntry": True},
}
DEV_SERVER = "http://localhost:5173"
# The policy outside development, directive by directive.
ENFORCED = {
    "default-src": ["'self'"],
    "script-src": ["'self'", "'wasm-unsafe-eval'"],
    "style-src": ["'self'"],
    "style-src-attr": ["'unsafe-hashes'", security_headers.WEBKIT_SELECT_HASH],
    "img-src": ["'self'"],
    "connect-src": ["'self'"],
    "object-src": ["'none'"],
    "base-uri": ["'none'"],
    "frame-ancestors": ["'none'"],
    "form-action": ["'self'"],
    "report-uri": [security_headers.REPORTS],
    "report-to": [security_headers.REPORT_GROUP],
}


def directives(response: Response) -> dict[str, list[str]]:
    """The response's policy, by directive, each directive's sources."""
    policy = response.headers["content-security-policy"]
    parsed = {}
    for directive in policy.split(";"):
        name, *sources = directive.split()
        parsed[name] = sources
    return parsed


def assert_protected(response: Response) -> None:
    assert directives(response) == ENFORCED
    assert response.headers["reporting-endpoints"] == (
        f'{security_headers.REPORT_GROUP}="{security_headers.REPORTS}"'
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    permissions = dict(
        feature.split("=")
        for feature in response.headers["permissions-policy"].split(", ")
    )
    assert permissions.pop("camera") == "(self)"
    assert permissions
    assert set(permissions.values()) == {"()"}


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    (tmp_path / MANIFEST).parent.mkdir(parents=True)
    (tmp_path / MANIFEST).write_text(json.dumps(MANIFEST_ENTRIES))
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "scan-a.js").write_text("export {};\n")
    return tmp_path


@pytest.fixture
def built(
    make_client_without_database: Callable[..., TestClient], dist: Path
) -> TestClient:
    return make_client_without_database(frontend=FrontendConfig(dist=dist))


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("GET", "/scan", 200),
        ("GET", "/register", 200),
        ("GET", "/admin/sign-in", 200),
        ("GET", "/nowhere", 404),
        ("GET", "/api/nowhere", 404),
        ("GET", "/static/assets/scan-a.js", 200),
        ("GET", "/", 302),
    ],
)
def test_every_kind_of_response_is_protected(
    built: TestClient, method: str, path: str, status: int
) -> None:
    response = built.request(method, path)
    assert response.status_code == status
    assert_protected(response)


def test_a_signed_in_page_is_protected(admin_client: TestClient) -> None:
    response = admin_client.get("/admin")
    assert response.status_code == 200
    assert_protected(response)


def test_a_refused_secret_field_is_protected(admin_client: TestClient) -> None:
    """The confirm page's refusal of a secret field (inventory-tng-2qy6)."""
    form = {"email": "ada@example.org", "api_token": "hunter2"}
    response = admin_client.post(f"{sign_in.ACCOUNT}/email", data=form)
    assert response.status_code == 400
    assert_protected(response)


def test_a_refused_cross_site_request_is_protected(built: TestClient) -> None:
    response = built.post("/admin/sign-in", headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403
    assert_protected(response)


@pytest.fixture
def failing(make_app_without_database: Callable[..., FastAPI]) -> Iterator[TestClient]:
    app = make_app_without_database()

    def fail() -> None:
        raise RuntimeError("a bug")

    app.add_api_route("/broken", fail)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_a_failure_page_is_protected(failing: TestClient) -> None:
    response = failing.get("/broken")
    assert response.status_code == 500
    assert_protected(response)


def test_no_page_needs_a_nonce_outside_development(built: TestClient) -> None:
    for path in ("/scan", "/admin/sign-in"):
        assert "csp-nonce" not in built.get(path).text


def test_the_one_style_attribute_allowed_is_webkits_for_a_select(
    built: TestClient,
) -> None:
    """WebKit bug 327212: Safari 27.2 checks a <select>'s own style against
    the page's policy. Its hash, checked here against the text, allows that
    style alone (docs/decisions/0010)."""
    digest = hashlib.sha256(b"text-overflow:inherit").digest()
    hashed = f"'sha256-{base64.b64encode(digest).decode()}'"
    allowed = directives(built.get("/scan"))["style-src-attr"]
    assert allowed == ["'unsafe-hashes'", hashed]


@pytest.fixture
def dev(make_client_without_database: Callable[..., TestClient]) -> TestClient:
    return make_client_without_database(
        frontend=FrontendConfig(dev_server=DEV_SERVER), development=True
    )


@pytest.mark.parametrize("path", ["/scan", "/admin/sign-in"])
def test_in_development_the_dev_server_and_its_styles_are_allowed(
    dev: TestClient, path: str
) -> None:
    """Vite's dev server serves the scripts and styles, reloads them over a
    websocket, and adds each stylesheet as a <style>, with the nonce its
    client reads from the page (frontend/vite.config.ts)."""
    response = dev.get(path)
    policy = directives(response)
    (nonce,) = re.findall(r'<meta property="csp-nonce" nonce="([^"]+)">', response.text)
    for name in ("default-src", "script-src", "img-src"):
        assert policy[name] == [*ENFORCED[name], DEV_SERVER]
    assert policy["style-src"] == ["'self'", DEV_SERVER, f"'nonce-{nonce}'"]
    assert policy["connect-src"] == ["'self'", DEV_SERVER, "ws://localhost:5173"]
    # A new one each time.
    assert nonce not in dev.get(path).text


def test_a_dev_server_over_https_reloads_over_wss() -> None:
    policy = security_headers.content_security_policy(
        dev_server="https://localhost:5173", nonce="n"
    )
    assert "wss://localhost:5173" in policy


def test_the_api_docs_load_swagger_with_a_nonce_in_development(
    make_client_without_database: Callable[..., TestClient],
) -> None:
    """FastAPI's page loads Swagger UI from its CDN and starts it with an
    inline script, which only that page's policy allows, by its nonce; never
    kept, as the nonce is this response's only."""
    client = make_client_without_database(development=True)
    response = client.get(security_headers.DOCS)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    policy = directives(response)
    (nonce,) = set(re.findall(r'<script nonce="([^"]+)">', response.text))
    assert f"'nonce-{nonce}'" in policy["script-src"]
    assert security_headers.SWAGGER_CDN in policy["script-src"]
    assert security_headers.SWAGGER_CDN in policy["style-src"]
    assert "<script>" not in response.text
    assert nonce not in client.get(security_headers.DOCS).text


def test_no_api_docs_outside_development(built: TestClient) -> None:
    """A CDN's script would run as this origin's: outside development there
    is no docs page, and its address has the policy every page has. The
    schema stays."""
    response = built.get(security_headers.DOCS)
    assert response.status_code == 404
    assert_protected(response)
    assert built.get("/api/schema").status_code == 200


def test_the_pages_render_without_this_module() -> None:
    """The pages' templates read the nonce through pages.py alone, so a
    page renders wherever pages.templates is used, the app or not."""
    script = (
        "import sys; from starlette.requests import Request;"
        " from inventory_tng.pages import templates;"
        " request = Request({'type': 'http', 'headers': []});"
        " templates.get_template('_csp_nonce.html').render(request=request);"
        " assert 'inventory_tng.security_headers' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", script], check=True)


@pytest.mark.clone
def test_no_template_has_inline_script_or_style() -> None:
    """The policy refuses them: a page's script is a file Vite builds
    (frontend/src/admin.ts), its look a class in the shared stylesheet."""
    inline = re.compile(
        r"<script(?![^>]*\bsrc=)[^>]*>|<style\b|\sstyle=|\son[a-z]+=|javascript:",
        re.IGNORECASE,
    )
    templates = CLONE / "backend" / "src" / "inventory_tng" / "templates"
    found = {
        path.name: match.group(0)
        for path in templates.iterdir()
        for match in [inline.search(path.read_text())]
        if match
    }
    assert found == {}


# What browsers send: report-uri's one report, and the Reporting API's list.
REPORT_URI = {
    "csp-report": {
        "document-uri": "https://inventory.example/scan",
        "blocked-uri": "https://elsewhere.example/x.js",
        "effective-directive": "script-src-elem",
        "violated-directive": "script-src-elem",
        "original-policy": "default-src 'self'",
    }
}
REPORTING_API = [
    {
        "type": "csp-violation",
        "url": "https://inventory.example/admin",
        "user_agent": "Mozilla/5.0 (Phone)",
        "body": {
            "documentURL": "https://inventory.example/admin",
            "blockedURL": "inline",
            "effectiveDirective": "style-src-attr",
            "sample": "color: red",
        },
    },
    {"type": "deprecation", "body": {"id": "x"}},
    {"type": "csp-violation", "body": {"effectiveDirective": "made-up-src"}},
]


def refusals(logs: list[MutableMapping[str, Any]]) -> list[object]:
    """Why the report endpoint said each refusal it logged was refused."""
    return [line["reason"] for line in logs if line["event"] == "policy report refused"]


@pytest.fixture
def reports(make_client_without_database: Callable[..., TestClient]) -> TestClient:
    return make_client_without_database()


def send(client: TestClient, report: object, content_type: str) -> Response:
    return client.post(
        security_headers.REPORTS,
        content=json.dumps(report),
        headers={"Content-Type": content_type},
    )


@pytest.mark.parametrize(
    ("report", "content_type", "found"),
    [
        (REPORT_URI, "application/csp-report", ["script-src-elem"]),
        (REPORTING_API, "application/reports+json", ["style-src-attr", "other"]),
        # Older browsers name the whole directive, sources and all.
        (
            {"csp-report": {"violated-directive": "img-src 'self'"}},
            "application/csp-report",
            ["img-src"],
        ),
        ({"csp-report": {"effective-directive": 7}}, "application/json", ["other"]),
        (
            {"csp-report": {"effective-directive": {"script-src": 1}}},
            "application/csp-report",
            ["other"],
        ),
        (
            {
                "csp-report": {
                    "effective-directive": "img-src",
                    "violated-directive": " ",
                }
            },
            "application/csp-report",
            ["img-src"],
        ),
        (
            [{"type": "csp-violation", "body": {"effectiveDirective": ["img-src"]}}],
            "application/reports+json",
            ["other"],
        ),
    ],
)
def test_a_report_is_counted_by_directive_and_nothing_it_says_is_kept(
    reports: TestClient, report: object, content_type: str, found: list[str]
) -> None:
    with capture_logs() as logs:
        response = send(reports, report, content_type)
    assert (response.status_code, response.content) == (204, b"")
    assert security_headers.violated(report) == found
    ours = [line for line in logs if line["event"] != "request"]
    assert ours == [
        {
            "event": "content security policy violated",
            "directives": sorted(set(found)),
            "count": len(found),
            "log_level": "warning",
        }
    ]


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"\xff",
        b"{}",
        b"[]",
        b'{"csp-report": "x"}',
        b'[{"type": "deprecation"}]',
        # A report that names no directive is no report of one.
        b'{"csp-report": {}}',
        b'{"csp-report": {"violated-directive": " "}}',
        b'[{"type": "csp-violation", "body": {}}]',
        # Nested deeper than the parser goes, well inside MOST_BYTES.
        b"[" * 10_000 + b"]" * 10_000,
        b"[" * 20_000,
    ],
)
def test_what_is_no_report_is_refused(reports: TestClient, body: bytes) -> None:
    with capture_logs() as logs:
        response = reports.post(
            security_headers.REPORTS,
            content=body,
            headers={"Content-Type": "application/csp-report"},
        )
    assert response.status_code == 400
    assert refusals(logs) == ["not_a_report"]
    assert_protected(response)


@pytest.mark.parametrize(
    "content_type", ["text/plain", "application/x-www-form-urlencoded", None]
)
def test_a_report_of_another_type_is_refused(
    reports: TestClient, content_type: str | None
) -> None:
    """Browsers send reports as JSON of their own types; nothing else is one."""
    headers = {} if content_type is None else {"Content-Type": content_type}
    with capture_logs() as logs:
        response = reports.post(
            security_headers.REPORTS, content=json.dumps(REPORT_URI), headers=headers
        )
    assert response.status_code == 415
    assert refusals(logs) == ["not_a_report_type"]
    assert_protected(response)


def test_a_report_too_large_is_refused(reports: TestClient) -> None:
    long = "x" * security_headers.MOST_BYTES
    padded = {"csp-report": {"effective-directive": "img-src", "sample": long}}
    with capture_logs() as logs:
        response = send(reports, padded, "application/csp-report")
    assert response.status_code == 413
    assert refusals(logs) == ["too_large"]


def test_a_batch_counts_only_so_many() -> None:
    many = [REPORTING_API[0]] * (security_headers.MOST_REPORTS + 5)
    assert len(security_headers.violated(many)) == security_headers.MOST_REPORTS


def test_reports_count_against_their_address(reports: TestClient) -> None:
    for _ in range(RateLimits().csp_reports):
        assert send(reports, REPORT_URI, "application/csp-report").status_code == 204
    refused = send(reports, REPORT_URI, "application/csp-report")
    assert refused.status_code == 429
    assert refused.json() == {"reason": "rate_limited"}


def test_the_limit_on_reports_is_a_setting(
    make_client_without_database: Callable[..., TestClient],
) -> None:
    """RATE_LIMIT_CSP_REPORTS, read as each report comes."""
    client = make_client_without_database(rate_limits=RateLimits(csp_reports=3))
    for _ in range(3):
        assert send(client, REPORT_URI, "application/csp-report").status_code == 204
    assert send(client, REPORT_URI, "application/csp-report").status_code == 429
