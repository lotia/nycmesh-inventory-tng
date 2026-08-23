"""Where a log record goes.

Here rather than inline in `settings.py` for the same reason as `hosts.py`: it
is a pure function of its argument, so a test can hold it directly instead of
reaching it through a configured Django, and there is one answer rather than
one per reader.

Django's own default is the thing being replaced. Its two handlers are selected
by mutually exclusive filters -- a console handler behind `require_debug_true`
and `mail_admins` behind `require_debug_false` -- so a deployment, which must
run with `DEBUG` false and has no `ADMINS`, has no handler at all. See
docs/deployment.md#reading-the-logs for what that cost.

The arrangement below does not branch on `DEBUG`. One destination in every
environment is the point: what a developer debugs against is what a deployment
emits.
"""

import logging
from typing import Any

# Replaced wholesale by structlog in inventory-tng-nb8.1, which renders the
# same record as JSON or for a terminal. Until then a record is legible and
# says which logger emitted it, which is the part that was missing.
LINE = "%(asctime)s %(levelname)-8s %(name)s %(message)s"


def log_level(requested: str) -> str:
    """Normalise a level name, refusing one Python does not know.

    Refusing rather than falling back to a default: a process that was asked
    for `DEBUG`, quietly gave `INFO`, and said nothing is the kind of silent
    adaptation that costs an afternoon. A typo in a ConfigMap should stop the
    pod, where it is visible, and not the next investigation.
    """
    level = requested.strip().upper()
    known = logging.getLevelNamesMapping()
    if level not in known:
        names = ", ".join(sorted(name for name in known if name != "NOTSET"))
        raise ValueError(f"DJANGO_LOG_LEVEL={requested!r} is not a logging level. Use one of: {names}.")
    return level


def logging_config(level: str) -> dict[str, Any]:
    """A `dictConfig` sending everything at `level` and above to standard output.

    Standard output because a container runtime collects it there, and because
    a stream the process writes to survives the process dying in a way that a
    telemetry pipeline in the request path does not.
    """
    return {
        "version": 1,
        # Django, DRF and allauth all hold logger references from import time.
        # Disabling them here would silence exactly the libraries whose
        # warnings are worth having.
        "disable_existing_loggers": False,
        "formatters": {"line": {"format": LINE}},
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "line",
            },
        },
        # The root logger catches this application's own loggers and anything a
        # dependency emits. `django` is named separately and does not propagate
        # so that its records are handled once rather than twice.
        "root": {"handlers": ["stdout"], "level": level},
        "loggers": {
            "django": {"handlers": ["stdout"], "level": level, "propagate": False},
        },
    }
