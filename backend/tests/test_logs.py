import json
import logging
import os

import pytest
import structlog

from inventory_tng.logs import configure_logging
from inventory_tng.settings import LogConfig, SettingsError

from helpers.settings import from_environ


def configure(**environ: str) -> None:
    configure_logging(from_environ({"DATABASE_URL": "postgresql://db/x", **environ}))


def test_defaults_to_colour_at_info() -> None:
    assert LogConfig.from_environ({}) == LogConfig("INFO", {}, "console")


def test_reads_level_levels_and_format() -> None:
    config = LogConfig.from_environ(
        {
            "LOG_LEVEL": "debug",
            "LOG_LEVELS": "sqlalchemy.engine=INFO, uvicorn = warning",
            "LOG_FORMAT": "JSON",
        }
    )
    assert config == LogConfig(
        "DEBUG", {"sqlalchemy.engine": "INFO", "uvicorn": "WARNING"}, "json"
    )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LOG_LEVEL", "LOUD"),
        ("LOG_FORMAT", "pretty"),
        ("LOG_LEVELS", "sqlalchemy.engine"),
        ("LOG_LEVELS", "=INFO"),
        ("LOG_LEVELS", "uvicorn=LOUD"),
    ],
)
def test_refuses_unusable_settings(name: str, value: str) -> None:
    with pytest.raises(SettingsError, match=name):
        LogConfig.from_environ({name: value})


@pytest.mark.usefixtures("restore_logging")
def test_json_puts_our_lines_and_the_libraries_in_one_shape(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(LOG_FORMAT="json")
    structlog.get_logger("inventory_tng.test").info("ours", volunteer_id=7)
    logging.getLogger("uvicorn.error").info("theirs")

    ours, theirs = (json.loads(line) for line in capsys.readouterr().out.splitlines())
    assert ours["event"] == "ours"
    assert ours["volunteer_id"] == 7
    assert theirs["event"] == "theirs"
    for line in (ours, theirs):
        assert {"level", "logger", "timestamp"} <= line.keys()


@pytest.mark.usefixtures("restore_logging")
def test_console_is_not_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure()
    structlog.get_logger("inventory_tng.test").info("hello")
    out = capsys.readouterr().out
    assert "hello" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


@pytest.mark.usefixtures("restore_logging")
def test_levels_reach_every_logger_and_sql_is_quiet_unless_asked(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure(LOG_LEVEL="WARNING", LOG_FORMAT="json")
    structlog.get_logger("inventory_tng.test").info("dropped")
    logging.getLogger("uvicorn.error").info("dropped too")
    assert capsys.readouterr().out == ""
    assert logging.getLogger("sqlalchemy.engine").level == logging.WARNING

    configure(LOG_LEVELS="sqlalchemy.engine=INFO,uvicorn=DEBUG", LOG_FORMAT="json")
    assert logging.getLogger("sqlalchemy.engine").level == logging.INFO
    logging.getLogger("uvicorn.error").debug("shown")
    assert json.loads(capsys.readouterr().out)["event"] == "shown"


@pytest.mark.usefixtures("restore_logging")
def test_json_tracebacks_are_text(capsys: pytest.CaptureFixture[str]) -> None:
    configure(LOG_FORMAT="json")
    try:
        raise ValueError("boom")
    except ValueError:
        structlog.get_logger("inventory_tng.test").exception("failed")
    line = json.loads(capsys.readouterr().out)
    assert isinstance(line["exception"], str)
    assert "ValueError: boom" in line["exception"]


@pytest.mark.usefixtures("restore_logging")
def test_no_colour_codes_when_not_a_terminal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure()
    structlog.get_logger("inventory_tng.test").info("plain")
    assert "\x1b[" not in capsys.readouterr().out


@pytest.mark.usefixtures("restore_logging")
def test_every_line_names_its_process(capsys: pytest.CaptureFixture[str]) -> None:
    configure(LOG_FORMAT="json")
    logging.getLogger("some.library").warning("which process?")
    assert json.loads(capsys.readouterr().out)["process"] == os.getpid()
