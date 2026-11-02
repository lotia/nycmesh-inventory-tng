"""Logging, set up the same way wherever a process starts (docs/decisions/0005).

`configure_logging()` takes over the root logger, so only the places a process
starts call it: the run command, the server factory and `migrations/env.py`.
`create_app()` never does, because tests build apps there and rely on
pytest's `caplog` (docs/decisions/0007).
"""

import logging.config
import os
import sys

import structlog
from structlog.typing import EventDict, Processor

from inventory_tng import request_logging
from inventory_tng.masking import Masker
from inventory_tng.settings import Settings

log = structlog.get_logger(__name__)

# Loggers that are too chatty at the overall level. LOG_LEVELS overrides them.
QUIET = {"sqlalchemy.engine": "WARNING"}


def add_process(_logger: object, _method: str, event: EventDict) -> EventDict:
    """The process id, to tell workers apart."""
    event["process"] = os.getpid()
    return event


# Applied to our lines and to the standard library's alike, followed by the
# masker; ADDED_FIELDS are the fields they add.
SHARED: list[Processor] = [
    structlog.contextvars.merge_contextvars,
    request_logging.merge_request_fields,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    add_process,
]
ADDED_FIELDS = frozenset({"level", "logger", "timestamp", "process"})


def configure_structlog(masker: Masker | None = None) -> None:
    """Send structlog's lines through the standard library's logging. Safe to
    call anywhere, tests included: it touches no handlers. Loggers are not
    cached, so a reconfiguration, or capture_logs() in a test, reaches loggers
    already in use. Without a masker, everything is masked."""
    structlog.configure(
        processors=[
            # Lines below the level are dropped before any work is done on them.
            structlog.stdlib.filter_by_level,
            *SHARED,
            masker or Masker(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


def configure_logging(settings: Settings) -> Masker:
    """Make every logger in the process write one format to stdout. Takes all
    the settings, not only the logging ones, because what is masked depends on
    APP_ENV. Returns the masker, for the OTLP bridge to mask the same way."""
    config = settings.logging
    masker = Masker.from_settings(settings)
    renderer: Processor
    if config.format == "json":
        # Tracebacks as plain text: structured ones carry local variables,
        # which can hold personal data.
        renderer = structlog.processors.JSONRenderer()
        pre: list[Processor] = [
            structlog.processors.ExceptionRenderer(masker.format_exception)
        ]
    else:
        # Colour only on a terminal, so `compose logs > file` stays plain.
        renderer = structlog.dev.ConsoleRenderer(
            colors=sys.stdout.isatty(), exception_formatter=masker.write_exception
        )
        pre = []
    loggers = {
        name: {"level": level} for name, level in {**QUIET, **config.levels}.items()
    }
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "structlog": {
                    "()": structlog.stdlib.ProcessorFormatter,
                    "foreign_pre_chain": [*SHARED, masker],
                    "processors": [
                        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                        *pre,
                        renderer,
                    ],
                }
            },
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "formatter": "structlog",
                    "stream": "ext://sys.stdout",
                }
            },
            "root": {"handlers": ["stdout"], "level": config.level},
            "loggers": loggers,
        }
    )
    configure_structlog(masker)
    if config.unmask_secrets:
        log.warning("secrets are not masked", log_unmasked=True)
    return masker
