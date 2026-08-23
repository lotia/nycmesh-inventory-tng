"""Where a log record goes, and what one contains.

Here rather than inline in `settings.py` for the same reason as `hosts.py`: it
is a function of its arguments, so a test can hold it directly instead of
reaching it through a configured Django, and there is one answer rather than
one per reader.

Django's own default is the thing being replaced. Its two handlers are selected
by mutually exclusive filters -- a console handler behind `require_debug_true`
and `mail_admins` behind `require_debug_false` -- so a deployment, which must
run with `DEBUG` false and has no `ADMINS`, has no handler at all. See
docs/deployment.md#reading-the-logs for what that cost.

ONE CHAIN, TWO DRAWINGS. Every record -- this application's, Django's, DRF's,
gunicorn's -- passes through the same list of structlog processors and is then
drawn either as JSON for a collector or in columns for a person. The property
worth the dependency is that the two differ only in drawing: what a developer
debugs against is what a deployment emits, field for field. `console.py` is the
second drawing and knows nothing about Django, so the same code redraws a
stream after the fact.

The reasoning behind the arrangement is
docs/decisions/0021-telemetry-over-otlp.md.
"""

import logging
import sys
from datetime import datetime
from typing import Any

import structlog
from opentelemetry import trace

from inventory_tng import console, refusals
from inventory_tng.options import DEFAULTS, setting

# What a record is drawn as when it is written. `console` is the default
# because an unconfigured checkout has no collector and a person is the only
# reader; compose and the chart both set `json`, where something is parsing.
FORMATS = ("console", "json")


def log_level(requested: str) -> str:
    """Normalise a level name, refusing one Python does not know.

    Refusing rather than falling back to a default: a process that was asked
    for `DEBUG`, quietly gave `INFO`, and said nothing is the kind of silent
    adaptation that costs an afternoon. A typo in a ConfigMap should stop the
    pod, where it is visible, and not the next investigation.
    """
    level = requested.strip().upper()
    # NOTSET is in Python's mapping and is not a level. On the root logger it
    # means "no threshold", so a ConfigMap saying NOTSET -- written by somebody
    # reading it as "do not set one" -- turns a pod into a DEBUG firehose
    # rather than being refused the way a misspelling is.
    known = {name for name in logging.getLevelNamesMapping() if name != "NOTSET"}
    if level not in known:
        raise ValueError(
            f"DJANGO_LOG_LEVEL={requested!r} is not a logging level. Use one of: {', '.join(sorted(known))}."
        )
    return level


def log_format(requested: str) -> str:
    """Which drawing, refused the same way a level is."""
    chosen = requested.strip().lower()
    if chosen not in FORMATS:
        raise ValueError(f"DJANGO_LOG_FORMAT={requested!r} is not a format. Use one of: {', '.join(FORMATS)}.")
    return chosen


def per_logger_levels(spec: str) -> dict[str, str]:
    """`inventory.sheet=DEBUG,django.db.backends=DEBUG`, parsed.

    This is what separates a usable log from a merely present one. A single
    level for everything means turning up the subsystem you are working on
    turns up every query Django runs and every call the tracer sees, so the
    thing you were reading is now one line in a thousand. Naming the logger
    lets one subsystem be loud while the rest stays at the ordinary level.
    """
    levels: dict[str, str] = {}
    for entry in spec.split(","):
        if not entry.strip():
            continue
        logger, separator, level = entry.partition("=")
        if not separator or not logger.strip():
            raise ValueError(f"DJANGO_LOG_LEVELS entry {entry!r} is not `logger=LEVEL`.")
        levels[logger.strip()] = log_level(level)
    return levels


def stamp(logger: Any, name: str, event: dict[str, Any]) -> dict[str, Any]:
    """The instant, in full ISO-8601 with this machine's offset.

    Full rather than time-only for the reasons decision 0021 gives. A layout
    narrow enough to need the seventeen columns back drops them when drawing;
    the record keeps them either way, so what a collector receives never
    depends on the window somebody happened to have open.
    """
    event["timestamp"] = datetime.now().astimezone().isoformat(timespec="milliseconds")
    return event


def trace_context(logger: Any, name: str, event: dict[str, Any]) -> dict[str, Any]:
    """Bind `trace_id` and `span_id`: the record's place in a trace.

    Both keys are on every record whether or not anything is tracing, which is
    the field contract nb8.1 established -- a collector's parsing, a
    dashboard's query and a saved stream do not change shape on the day the
    SDK is switched on, they simply stop being empty.

    Read from the current span rather than from a bound variable, because that
    is what makes a log line findable FROM a trace and a trace findable from a
    log line. `get_current_span` costs a contextvar lookup and returns an
    invalid span when nothing is recording, so the no-collector case pays
    nothing beyond that.

    Empty unless the trace was actually SAMPLED, which is the part that is
    easy to get wrong. A span exists and carries a real id whether or not it
    will be exported, so gating on the id being non-zero puts a plausible
    thirty-two-character id on every record of every dropped trace -- at the
    default ratio, nine records in ten pointing at something no collector was
    ever sent. An id that cannot be looked up is worse than no id, because a
    reader spends the search before concluding it.
    """
    span = trace.get_current_span().get_span_context()
    recorded = span.trace_id and span.trace_flags.sampled
    event.setdefault("trace_id", f"{span.trace_id:032x}" if recorded else "")
    event.setdefault("span_id", f"{span.span_id:016x}" if recorded else "")
    return event


def context(logger: Any, name: str, event: dict[str, Any]) -> dict[str, Any]:
    """Merge the bound context, and record WHICH keys came from it.

    Both jobs from one scan. structlog's own `merge_contextvars` walks every
    context variable in the process, and asking it separately which keys it
    merged walks them all again -- a cost that grows as Django, DRF and the
    OpenTelemetry SDK add context variables of their own, none of which are
    ours.

    Recording the provenance is what keeps the two drawings honest. Deciding
    what to hide from the NAME of a key is wrong: `status`, `path` and `user`
    are ordinary words, so a developer writing log.info("refused", status=500)
    watched the console silently drop the one field they had gone to the
    trouble of passing, while the JSON kept it.
    """
    inherited = structlog.contextvars.get_contextvars()
    for key, value in inherited.items():
        event.setdefault(key, value)
    # trace_id and span_id are inherited too -- `trace_context` binds them on
    # every record -- so they are named here rather than drawn as two empty
    # keys on every line for as long as there is no tracer.
    event[console.BOUND_KEYS] = sorted({*inherited, "trace_id", "span_id"})
    return event


# Run for every record, whichever drawing follows and whether the record came
# from structlog or from a library using stdlib `logging`. That last part is
# the reason for the dependency: `django.request`, `django.security` and
# gunicorn's access log are drawn exactly like ours without their authors
# having agreed to anything.
SHARED: list[Any] = [
    context,
    structlog.stdlib.add_logger_name,
    structlog.stdlib.add_log_level,
    # Without this a `%`-style call -- log.info("imported %d rows", 41) --
    # keeps the literal %d and carries the argument off in a stray key, while
    # the identical stdlib call interpolates. Two paths this module promises
    # are the same must not differ on the most habitual idiom there is.
    structlog.stdlib.PositionalArgumentsFormatter(),
    stamp,
    trace_context,
    structlog.processors.format_exc_info,
    structlog.processors.UnicodeDecoder(),
]

# For records from stdlib loggers, which never saw the chain above.
#
# `ExtraAdder` is the reason this list exists separately. Django attaches the
# status code of every 4xx and 5xx through `extra=`, and without it not one
# record in the system carries one. It is given an allowlist because
# `log_response` also puts the `HttpRequest` in there, and a request object is
# not a log field: it is large, it is not serialisable, and it holds exactly
# the personal data the epic's redaction issue exists to keep out.
#
# `StackInfoRenderer` is deliberately absent: `ProcessorFormatter` has already
# formatted `record.stack_info`, and running it again re-captures the stack
# from inside the logging machinery, so the frames a reader wants arrive under
# seven frames of `logging/__init__.py`.
FROM_LIBRARIES: list[Any] = [
    structlog.stdlib.ExtraAdder(
        allow={"status_code", "duration", "params", "sql", "alias", "suppressed", "suppressed_since"}
    ),
    *SHARED,
]


def draw_for_a_terminal(logger: Any, name: str, event: dict[str, Any]) -> str:
    """structlog's last processor, when the reader is a person at a terminal.

    The layout was measured and announced by `configure`, and is not measured
    again here. Re-measuring per record would mean a resize silently changing
    the shape of the output after the process had announced otherwise -- which
    is the one thing this module says it will not do -- and it puts an ioctl on
    the write path of every access line for the privilege.
    """
    return console.render(event, _layout, colour=_colour, context=_context)


# Set by `configure`, read by the processor above. Module state because a
# structlog processor takes no configuration of its own, and because every one
# of these is a property of the process rather than of a record.
_layout = console.FULL
_colour = False
_context = False


def logging_config(
    level: str,
    drawn_as: str = "console",
    levels: dict[str, str] | None = None,
    security_rate: str = DEFAULTS["DJANGO_SECURITY_LOG_RATE"],
) -> dict[str, Any]:
    """A `dictConfig` sending everything at `level` and above to standard output.

    Standard output because a container runtime collects it there, and because
    a stream the process writes to survives the process dying in a way that a
    telemetry pipeline in the request path does not.

    It does not branch on `DEBUG`. One destination in every environment is the
    point: an arrangement that only exists in production is one nobody has read.

    `security_rate` bounds one family of records rather than the stream:
    `refusals` says which, and why that family alone is rationed.
    """
    count, window = refusals.rate(security_rate)

    formatter = {
        "()": structlog.stdlib.ProcessorFormatter,
        "processors": [
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer() if drawn_as == "json" else draw_for_a_terminal,
        ],
        # Records from libraries never saw the chain, so they are put through
        # it here. Without this a Django warning would arrive with none of the
        # keys ours carry and a collector would have two shapes to parse.
        "foreign_pre_chain": FROM_LIBRARIES,
    }

    loggers: dict[str, Any] = {
        # Named separately from the root and not propagating, so a Django
        # record is handled once. Naming a logger in `dictConfig` and leaving
        # it to propagate as well is the usual way to get everything twice.
        "django": {"handlers": ["stdout"], "level": level, "propagate": False},
        # gunicorn writes an access line per request and its own errors, both
        # to handlers of its own. Left alone they arrive as plain text beside
        # our JSON and a collector's parser meets two formats in one stream.
        "gunicorn.error": {"handlers": ["stdout"], "level": level, "propagate": False},
        "gunicorn.access": {"handlers": ["stdout"], "level": level, "propagate": False},
    }
    for logger, wanted in (levels or {}).items():
        # Propagating, so a named logger keeps the one handler rather than
        # gaining a second copy of every record.
        loggers[logger] = {"level": wanted, "propagate": True}

    return {
        "version": 1,
        # Django, DRF and allauth all hold logger references from import time.
        # Disabling them here would silence exactly the libraries whose
        # warnings are worth having.
        "disable_existing_loggers": False,
        "formatters": {"structured": formatter},
        # On the handler and not on a logger, because Django logs a refusal to
        # a CHILD of `django.security` and Python consults filters only on the
        # logger the call was made on. `refusals.Bounded` says the rest.
        "filters": {"refusals": {"()": refusals.Bounded, "count": count, "window": window}},
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "structured",
                "filters": ["refusals"],
            },
        },
        # The root logger catches this application's own loggers and anything a
        # dependency emits.
        "root": {"handlers": ["stdout"], "level": level},
        "loggers": loggers,
    }


def configure(drawn_as: str, forced_layout: str = "", colour: bool = False, context: bool = False) -> str:
    """Point structlog at the stdlib, and settle how a terminal drawing looks.

    Returns the line the process should print about its layout, or an empty
    string when there is nothing to announce -- a JSON stream has no layout,
    and neither does the widest one, which drops nothing. Returned rather than
    printed so that the caller decides where it goes and a test can read it.
    """
    global _layout, _colour, _context

    structlog.configure(
        processors=[*SHARED, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Python's own warnings -- a naive datetime, a deprecation from Django or
    # allauth, the missing-staticfiles one on boot -- otherwise go to standard
    # error as two lines of unparseable text, which contradicts both "one
    # format" and "standard output and nowhere else" in the same breath.
    logging.captureWarnings(True)

    # Validated whichever drawing is in use, so a typo in a ConfigMap cannot
    # stop a laptop and start a cluster.
    width = console.terminal_width()
    layout = console.choose(width, forced_layout)
    _layout, _colour, _context = layout, colour, context

    return "" if drawn_as != "console" else console.announcement(layout, width, forced_layout)


def from_environment(environment: dict[str, str] | None = None) -> tuple[dict[str, Any], str]:
    """The configuration and its announcement, read from the environment once.

    Both entry points call this -- Django's settings module and gunicorn's
    config file -- because they were each reading the same handful of variables
    with their own defaults, and a master process drawing columns while its
    workers drew JSON is one stream in two formats, which is the exact defect
    the gunicorn configuration exists to prevent.
    """
    drawn_as = log_format(setting("DJANGO_LOG_FORMAT", environment))
    # Before `logging_config`, not after: the terminal drawing reads the
    # layout this settles, and building the configuration first only worked
    # because `dictConfig` is applied later still.
    said = configure(
        drawn_as,
        setting("DJANGO_LOG_LAYOUT", environment),
        colour=console.in_colour(sys.stdout),
        context=console.log_context(setting("DJANGO_LOG_CONTEXT", environment)),
    )
    config = logging_config(
        log_level(setting("DJANGO_LOG_LEVEL", environment)),
        drawn_as,
        per_logger_levels(setting("DJANGO_LOG_LEVELS", environment)),
        setting("DJANGO_SECURITY_LOG_RATE", environment),
    )
    return config, said
