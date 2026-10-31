"""Logging, set up the same way wherever a process starts (docs/decisions/0005).

`configure_logging()` takes over the root logger, so only the places a process
starts call it: the server entry point and `migrations/env.py`. `create_app()`
never does, because tests build apps there and rely on pytest's `caplog`
(docs/decisions/0007).
"""

import logging.config

import structlog
from structlog.typing import Processor

from inventory_tng.settings import LogConfig

# Loggers that are too chatty at the overall level. LOG_LEVELS overrides them.
QUIET = {"sqlalchemy.engine": "WARNING"}

# Applied to our lines and to the standard library's alike.
SHARED: list[Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
]


def configure_structlog() -> None:
    """Send structlog's lines through the standard library's logging. Safe to
    call anywhere, tests included: it touches no handlers. Loggers are not
    cached, so a reconfiguration, or capture_logs() in a test, reaches loggers
    already in use."""
    structlog.configure(
        processors=[
            # Lines below the level are dropped before any work is done on them.
            structlog.stdlib.filter_by_level,
            *SHARED,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


def configure_logging(config: LogConfig) -> None:
    """Make every logger in the process write one format to stdout."""
    renderer: Processor
    if config.format == "json":
        # Tracebacks as plain text: structured ones carry local variables,
        # which can hold personal data.
        renderer = structlog.processors.JSONRenderer()
        pre: list[Processor] = [structlog.processors.format_exc_info]
    else:
        renderer = structlog.dev.ConsoleRenderer()
        pre = []
    # uvicorn installs handlers and levels of its own. Ours replace them, so
    # its lines take our format and LOG_LEVEL. Its access log is off: our
    # request line (request_logging.py) replaces it.
    loggers: dict[str, dict[str, object]] = {
        "uvicorn": {"handlers": [], "propagate": True, "level": "NOTSET"},
        "uvicorn.error": {"handlers": [], "propagate": True, "level": "NOTSET"},
        "uvicorn.access": {"handlers": [], "propagate": False},
    }
    for name, level in {**QUIET, **config.levels}.items():
        loggers.setdefault(name, {})["level"] = level
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "structlog": {
                    "()": structlog.stdlib.ProcessorFormatter,
                    "foreign_pre_chain": SHARED,
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
    configure_structlog()
