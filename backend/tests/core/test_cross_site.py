"""Requests that change something are refused when another site's page sent
them: Sec-Fetch-Site decides, else Origin (core/cross_site.py,
docs/decisions/0012). Under Referrer-Policy: same-origin, a browser without
Sec-Fetch-Site sends the app's own pages' real Origin (inventory-tng-7f6e)."""

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from inventory_tng.core.cross_site import CrossSiteRefusal, cross_site


@pytest.mark.parametrize(
    ("method", "headers", "refused"),
    [
        ("POST", {"sec-fetch-site": "cross-site"}, True),
        ("POST", {"sec-fetch-site": "same-site"}, True),
        ("POST", {"sec-fetch-site": "same-origin"}, False),
        ("POST", {"sec-fetch-site": "none"}, False),
        ("POST", {"origin": "https://evil.example", "host": "inv.example"}, True),
        ("POST", {"origin": "null", "host": "inv.example"}, True),
        ("POST", {"origin": "https://inv.example", "host": "inv.example"}, False),
        # Sec-Fetch-Site wins over Origin when both are sent.
        (
            "POST",
            {"sec-fetch-site": "same-origin", "origin": "null", "host": "inv.example"},
            False,
        ),
        (
            "POST",
            {
                "sec-fetch-site": "cross-site",
                "origin": "https://inv.example",
                "host": "inv.example",
            },
            True,
        ),
        ("POST", {}, False),
        ("GET", {"sec-fetch-site": "cross-site"}, False),
    ],
)
def test_cross_site_requests(
    method: str, headers: dict[str, str], refused: bool
) -> None:
    assert cross_site(method, Headers(headers)) is refused


async def saved(_request: Request) -> PlainTextResponse:
    return PlainTextResponse("saved")


@pytest.fixture
def client() -> TestClient:
    app = Starlette(routes=[Route("/save", saved, methods=["POST"])])
    app.add_middleware(CrossSiteRefusal)
    return TestClient(app)


def test_a_same_origin_post_without_sec_fetch_site_is_accepted(
    client: TestClient,
) -> None:
    """Safari before 16.4: no Sec-Fetch-Site, and the page's own Origin."""
    response = client.post("/save", headers={"origin": "http://testserver"})
    assert response.status_code == 200
    assert response.text == "saved"


def test_a_post_from_origin_null_without_sec_fetch_site_is_refused(
    client: TestClient,
) -> None:
    """A sandboxed frame, a data: page or another site's redirect."""
    response = client.post("/save", headers={"origin": "null"})
    assert response.status_code == 403
