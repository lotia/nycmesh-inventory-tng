"""Log redaction turned off for a while, or in development
(docs/decisions/0008; masking.Masker, settings.Redaction)."""

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
import structlog
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from structlog.testing import capture_logs

from inventory_tng.logs import configure_logging
from inventory_tng.masking import MASK, REDACTION_FIELD, Masker
from inventory_tng.server import create_server
from inventory_tng.settings import Redaction, Settings, SettingsError
from inventory_tng.telemetry import StructlogBridge, Telemetry

from helpers.settings import from_environ

NOW = datetime(2026, 11, 9, 12, tzinfo=UTC)
LINE = {"event": "x", "email": "jane@example.org", "password": "hunter2"}


def settings(**environ: str) -> Settings:
    return from_environ({"DATABASE_URL": "postgresql://db/x", **environ})


def masker_at(
    redaction: Redaction, clock: list[datetime], *, development: bool = False
) -> Masker:
    return Masker(redaction=redaction, development=development, now=lambda: clock[0])


def test_outside_development_it_is_off_only_until_a_time_within_the_cap() -> None:
    with pytest.raises(SettingsError, match="LOG_REDACTION=off works only"):
        settings(LOG_REDACTION="off")
    with pytest.raises(SettingsError, match="LOG_REDACTION_OFF_UNTIL may be at most 6"):
        Redaction.from_environ(
            "LOG",
            {"LOG_REDACTION_OFF_UNTIL": (NOW + timedelta(hours=7)).isoformat()},
            NOW,
        )
    longer = {
        "LOG_REDACTION_OFF_UNTIL": (NOW + timedelta(hours=7)).isoformat(),
        "REDACTION_OFF_MAX_HOURS": "8",
    }
    assert Redaction.from_environ("LOG", longer, NOW).off_until == NOW + timedelta(
        hours=7
    )
    with pytest.raises(SettingsError, match="LOG_REDACTION must be on or off"):
        settings(LOG_REDACTION="maybe")


def test_while_off_personal_data_stays_but_secrets_never_and_each_line_says_so() -> (
    None
):
    clock = [NOW]
    masker = masker_at(Redaction(off_until=NOW + timedelta(hours=2)), clock)
    line = masker(None, "info", dict(LINE))
    assert line == {**LINE, "password": MASK, REDACTION_FIELD: "off"}


def test_redaction_comes_back_on_by_itself() -> None:
    clock = [NOW]
    masker = masker_at(Redaction(off_until=NOW + timedelta(hours=1)), clock)
    clock[0] = NOW + timedelta(hours=1)
    assert masker(None, "info", dict(LINE)) == {
        **LINE,
        "email": MASK,
        "password": MASK,
    }


def test_development_keeps_personal_data_unmarked_unless_switched_on() -> None:
    clock = [NOW]
    development = masker_at(Redaction(), clock, development=True)
    assert development(None, "info", dict(LINE)) == {**LINE, "password": MASK}
    on = masker_at(Redaction(switch=True), clock, development=True)
    assert on(None, "info", dict(LINE))["email"] == MASK


def test_a_warning_hourly_while_off() -> None:
    """The server's starting line is the first."""
    clock = [NOW]
    masker = masker_at(Redaction(off_until=NOW + timedelta(hours=3)), clock)
    with capture_logs() as logs:
        masker(None, "info", {"event": "x"})
        clock[0] = NOW + timedelta(minutes=59)
        masker(None, "info", {"event": "x"})
        clock[0] = NOW + timedelta(hours=1)
        masker(None, "info", {"event": "x"})
        masker(None, "info", {"event": "x"})
    assert logs == [
        {
            "event": "log redaction is off",
            "until": NOW + timedelta(hours=3),
            "log_level": "warning",
        }
    ]


@pytest.mark.usefixtures("restore_logging")
def test_starting_warns_while_logs_leave_unredacted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LOG_FORMAT", "json")
    until = datetime.now(UTC) + timedelta(hours=1)
    monkeypatch.setenv("LOG_REDACTION_OFF_UNTIL", until.isoformat())
    create_server()
    lines = [json.loads(out) for out in capsys.readouterr().out.splitlines()]
    (line,) = [line for line in lines if line["event"] == "starting"]
    assert line["level"] == "warning"
    assert line["log_redaction"] is False
    assert line["log_redaction_off_until"]
    assert line[REDACTION_FIELD] == "off"


@pytest.mark.usefixtures("restore_logging")
def test_the_settings_reach_the_masker(capsys: pytest.CaptureFixture[str]) -> None:
    until = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    configure_logging(settings(LOG_FORMAT="json", LOG_REDACTION_OFF_UNTIL=until))
    structlog.get_logger("inventory_tng.test").info("x", email="jane@example.org")
    (line,) = [json.loads(out) for out in capsys.readouterr().out.splitlines()]
    assert line["email"] == "jane@example.org"
    assert line[REDACTION_FIELD] == "off"


def test_library_lines_over_otlp_say_so_too() -> None:
    """The standard library's lines reach OTLP without structlog's
    processors: the bridge marks them, as stdout's formatter does."""
    exporter = InMemoryLogRecordExporter()
    telemetry = Telemetry.create(
        {}, metric_reader=InMemoryMetricReader(), log_exporter=exporter
    )
    assert telemetry.logger_provider is not None
    masker = masker_at(Redaction(off_until=NOW + timedelta(hours=2)), [NOW])
    bridge = StructlogBridge(logger_provider=telemetry.logger_provider, masker=masker)
    try:
        bridge.emit(
            logging.LogRecord(
                "some.library", logging.WARNING, __file__, 1, "theirs", (), None
            )
        )
        telemetry.logger_provider.force_flush()
    finally:
        telemetry.logger_provider.shutdown()
    (entry,) = exporter.get_finished_logs()
    assert entry.log_record.attributes is not None
    assert entry.log_record.attributes[REDACTION_FIELD] == "off"
