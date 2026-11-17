"""POST /api/telemetry: what the scan and register pages report about
themselves (page_telemetry.py, frontend/src/telemetry.ts)."""

import dataclasses
import re
from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from inventory_tng import page_telemetry
from inventory_tng.local_env import CLONE
from inventory_tng.settings import RateLimits

PATH = "/api/telemetry"
RETRY = {"kind": "outbox_retry", "reason": "unavailable"}


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()


def test_a_report_is_taken_with_nothing_in_reply(client: TestClient) -> None:
    taken = client.post(PATH, json={"events": [RETRY]})
    assert taken.status_code == 204
    assert taken.content == b""


@pytest.mark.parametrize(
    "events",
    [
        [],
        [RETRY] * (page_telemetry.MOST_EVENTS + 1),
        [{"kind": "page_view"}],
        [RETRY | {"url": "https://example.org/"}],
        [{"kind": "error", "page": "scan", "source": "error", "message": "x"}],
        [{"kind": "error", "page": "admin", "source": "error"}],
        [
            {
                "kind": "web_vital",
                "page": "scan",
                "name": "TTFB",
                "value": 1,
                "rating": "good",
            }
        ],
        [
            {
                "kind": "web_vital",
                "page": "scan",
                "name": "LCP",
                "value": -1,
                "rating": "good",
            }
        ],
        [{"kind": "scan_to_save", "seconds": 86_401}],
    ],
)
def test_only_what_the_schema_names_is_taken(
    client: TestClient, events: list[dict[str, object]]
) -> None:
    """The schema is the list of what may be sent: nothing free, nothing
    unbounded."""
    assert client.post(PATH, json={"events": events}).status_code == 422


def test_a_reports_errors_are_logged_once_without_anything_the_page_said(
    client: TestClient,
) -> None:
    event = {"kind": "error", "page": "scan", "source": "error"}
    with capture_logs() as logs:
        assert client.post(PATH, json={"events": [event, event]}).status_code == 204
    assert {"event": "page errors", "count": 2, "log_level": "warning"} in logs


def test_reports_count_against_their_address(client: TestClient) -> None:
    for _ in range(RateLimits().page_reports):
        assert client.post(PATH, json={"events": [RETRY]}).status_code == 204
    refused = client.post(PATH, json={"events": [RETRY]})
    assert refused.status_code == 429
    assert refused.json() == {"reason": "rate_limited"}


def test_the_limit_on_reports_is_a_setting(
    make_app: Callable[..., FastAPI], make_client: Callable[..., TestClient]
) -> None:
    """RATE_LIMIT_PAGE_REPORTS, read as each report comes."""
    app = make_app()
    app.state.settings = dataclasses.replace(
        app.state.settings, rate_limits=RateLimits(page_reports=3)
    )
    client = make_client(app)
    for _ in range(3):
        assert client.post(PATH, json={"events": [RETRY]}).status_code == 204
    assert client.post(PATH, json={"events": [RETRY]}).status_code == 429


@pytest.mark.clone
def test_the_page_clamps_scan_to_save_to_what_is_taken() -> None:
    """frontend/src/telemetry.ts clamps it to the bound the schema has, which
    the generated client types cannot carry: a value past it would have the
    whole report refused."""
    source = (CLONE / "frontend" / "src" / "telemetry.ts").read_text()
    clamp = re.search(r"MOST_SECONDS_TO_SAVE = ([\d_]+);", source)
    assert clamp is not None
    (bound,) = (
        meta.le
        for meta in page_telemetry.ScanToSave.model_fields["seconds"].metadata
        if hasattr(meta, "le")
    )
    assert int(clamp.group(1).replace("_", "")) == bound
