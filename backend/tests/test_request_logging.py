import re

import pytest
from fastapi.testclient import TestClient
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from inventory_tng.app import create_app
from inventory_tng.request_logging import request_id
from inventory_tng.settings import LogConfig, Settings

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACEPARENT = f"00-{TRACE_ID}-00f067aa0ba902b7-01"


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ([(b"x-request-id", b"ingress-123")], "ingress-123"),
        ([(b"traceparent", TRACEPARENT.encode())], TRACE_ID),
        (
            [(b"x-request-id", b"ingress-123"), (b"traceparent", TRACEPARENT.encode())],
            "ingress-123",
        ),
    ],
)
def test_takes_the_ingress_id(
    headers: list[tuple[bytes, bytes]], expected: str
) -> None:
    assert request_id(headers) == expected


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [(b"x-request-id", b"x" * 65)],
        [(b"x-request-id", b"has spaces")],
        [(b"traceparent", b"not-a-traceparent")],
    ],
)
def test_otherwise_makes_one(headers: list[tuple[bytes, bytes]]) -> None:
    assert re.fullmatch(r"[0-9a-f]{32}", request_id(headers))


def test_echoes_the_id_and_logs_the_request(client: TestClient) -> None:
    with capture_logs(processors=[merge_contextvars]) as logs:
        response = client.get("/api/healthz", headers={"X-Request-ID": "abc-1"})

    assert response.headers["x-request-id"] == "abc-1"
    (line,) = [entry for entry in logs if entry["event"] == "request"]
    assert line["request_id"] == "abc-1"
    assert line["path"] == "/api/healthz"
    assert line["status"] == 200


def test_a_failure_is_logged_at_error_and_answered_with_the_id(
    database_url: str,
) -> None:
    app = create_app(Settings(database_url=database_url))

    @app.get("/boom")
    def boom() -> None:
        raise ValueError("boom")

    with (
        TestClient(app, raise_server_exceptions=False) as client,
        capture_logs(processors=[merge_contextvars]) as logs,
    ):
        response = client.get("/boom", headers={"X-Request-ID": "abc-2"})

    assert response.status_code == 500
    assert response.headers["x-request-id"] == "abc-2"
    (line,) = [entry for entry in logs if entry["event"] == "request failed"]
    assert line["log_level"] == "error"
    assert line["exc_info"] is True
    assert line["request_id"] == "abc-2"
    assert line["status"] == 500


@pytest.mark.parametrize("on", [True, False])
def test_the_client_address_is_logged_only_when_asked(
    database_url: str, on: bool
) -> None:
    settings = Settings(database_url=database_url, logging=LogConfig(client_address=on))
    app = create_app(settings)
    with TestClient(app) as client, capture_logs() as logs:
        client.get("/api/healthz")
    (line,) = [entry for entry in logs if entry["event"] == "request"]
    assert ("client" in line) is on


@pytest.mark.parametrize(("on", "level"), [(True, "info"), (False, "debug")])
def test_successful_health_checks_can_be_quietened(
    database_url: str, on: bool, level: str
) -> None:
    settings = Settings(database_url=database_url, logging=LogConfig(health_checks=on))
    with TestClient(create_app(settings)) as client, capture_logs() as logs:
        client.get("/api/healthz")
    (line,) = [entry for entry in logs if entry["event"] == "request"]
    assert line["log_level"] == level


def test_a_failing_health_check_always_shows() -> None:
    url = "postgresql+psycopg://u:p@127.0.0.1:1/x"
    settings = Settings(database_url=url, logging=LogConfig(health_checks=False))
    with TestClient(create_app(settings)) as client, capture_logs() as logs:
        client.get("/api/healthz")
    (line,) = [entry for entry in logs if entry["event"] == "request"]
    assert (line["status"], line["log_level"]) == (503, "info")
