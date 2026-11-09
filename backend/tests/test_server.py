import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from inventory_tng import server
from inventory_tng.app import create_app
from inventory_tng.db import make_engine
from inventory_tng.server import create_server, flush_when_stopped
from inventory_tng.settings import Redaction, Settings


@pytest.mark.usefixtures("restore_logging")
def test_sets_up_logging_then_builds_the_app(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:secret@db.example:5433/x")
    monkeypatch.setenv("LOG_FORMAT", "json")

    assert isinstance(create_server(), FastAPI)

    lines = [json.loads(out) for out in capsys.readouterr().out.splitlines()]
    # Whatever else is said, such as that the scan page is not built.
    (line,) = [line for line in lines if line["event"] == "starting"]
    assert line["database_host"] == "db.example"
    assert line["sealing_key_versions"] == [1]  # The tests' TOTP keys.
    assert "secret" not in json.dumps(line)


def test_the_engine_hides_sql_parameters() -> None:
    assert make_engine("postgresql+psycopg://u:p@localhost/x").hide_parameters


def test_shutdown_runs_when_the_app_stops(database_url: str) -> None:
    app = create_app(Settings(database_url=database_url))
    calls: list[str] = []
    flush_when_stopped(app, lambda: calls.append("flushed"))
    with TestClient(app):
        assert calls == []
    assert calls == ["flushed"]


class Recording:
    """Telemetry, standing in: what the server hands it, and spans on."""

    given: dict[str, object] = {}  # noqa: RUF012  Replaced per test.
    tracer_provider = object()

    @classmethod
    def create(cls, **options: object) -> Recording:
        cls.given = options
        return cls()

    def make_global(self) -> None: ...
    def attach_logs(self, _masker: object) -> None: ...
    def instrument(self, _app: object, _engine: object) -> None: ...
    def shutdown(self) -> None: ...


@pytest.fixture
def telemetry_on(monkeypatch: pytest.MonkeyPatch) -> type[Recording]:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:secret@db.example:5433/x")
    monkeypatch.setattr(server, "enabled", lambda: True)
    monkeypatch.setattr(server, "Telemetry", Recording)
    return Recording


@pytest.mark.usefixtures("restore_logging")
def test_traces_leave_through_the_redactor_with_the_settings(
    telemetry_on: type[Recording],
) -> None:
    """With telemetry on, the server hands Telemetry the redaction settings:
    in production, redacting now."""
    create_server()
    given = telemetry_on.given
    assert (given["development"], given["secrets"]) == (False, True)
    traces = given["traces"]
    assert isinstance(traces, Redaction)
    assert traces.redacting(development=False, now=datetime.now(UTC))


@pytest.mark.usefixtures("restore_logging", "telemetry_on")
def test_starting_warns_while_traces_leave_unredacted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LOG_FORMAT", "json")
    until = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    monkeypatch.setenv("TRACE_REDACTION_OFF_UNTIL", until)
    create_server()
    lines = [json.loads(out) for out in capsys.readouterr().out.splitlines()]
    (line,) = [line for line in lines if line["event"] == "starting"]
    assert line["level"] == "warning"
    assert line["trace_redaction_off_until"]
