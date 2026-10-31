import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from inventory_tng.app import create_app
from inventory_tng.db import make_engine
from inventory_tng.server import create_server, flush_when_stopped
from inventory_tng.settings import Settings


@pytest.mark.usefixtures("restore_logging")
def test_sets_up_logging_then_builds_the_app(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:secret@db.example:5433/x")
    monkeypatch.setenv("LOG_FORMAT", "json")

    assert isinstance(create_server(), FastAPI)

    (line,) = (json.loads(out) for out in capsys.readouterr().out.splitlines())
    assert line["event"] == "starting"
    assert line["database_host"] == "db.example"
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
