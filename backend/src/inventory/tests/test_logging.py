"""That a record written in production reaches somewhere a person can read it.

The regression these hold is not hypothetical. Django's shipped configuration
routes every record to one of two handlers that a deployment cannot have, so
until `inventory_tng.logs` existed an unhandled exception produced a 500 with
its traceback written nowhere at all. The whole of that failure is invisible
from inside the application, which is why it survived a release.

`inventory/tests/test_hosts.py` says why a pure function is held on its own
terms; the same argument applies to `log_level`. What cannot be held that way
is whether Django's own machinery actually reaches the handler, so the first
test drives a real request through a real exception.
"""

import copy
import io
import json
import logging
import logging.config
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import structlog
from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.test import Client
from django.urls import path
from pytest_django.fixtures import Settings

from inventory_tng import console, logs, refusals
from inventory_tng.logs import (
    configure,
    from_environment,
    log_format,
    log_level,
    logging_config,
    per_logger_levels,
)
from inventory_tng.options import setting


def explode(request: HttpRequest) -> HttpResponse:
    """A view whose only job is to fail the way a real one eventually will."""
    raise RuntimeError("the failure a deployment has to be able to see")


# `override_settings(ROOT_URLCONF=__name__)` reads these, so the raising view is
# reachable without adding a route the application would then have to carry.
urlpatterns = [path("explode/", explode)]


@contextmanager
def applied(config: dict[str, Any]) -> Iterator[io.StringIO]:
    """Apply a configuration, with its one handler pointed at a buffer.

    The buffer stands in for standard output because pytest has already
    replaced that by the time a test runs. Substituting the destination and
    nothing else keeps every other part of the configuration under test --
    which handler, at which level, attached to which loggers. That the
    destination itself is right is a separate assertion below, so the
    substitution cannot hide a handler writing to the wrong place.

    Restores the real configuration afterwards, because `dictConfig` is
    global: a test that left its own arrangement in place would be heard from
    much later, in whichever test happened to run next.
    """
    buffer = io.StringIO()
    substituted = copy.deepcopy(config)
    substituted["handlers"]["stdout"]["stream"] = buffer
    logging.config.dictConfig(substituted)
    try:
        yield buffer
    finally:
        logging.config.dictConfig(settings.LOGGING)


@pytest.fixture(autouse=True)
def _restore_the_processes_own_arrangement() -> Iterator[None]:
    """`configure` writes process-global state, and tests call it.

    structlog's configuration and this module's `_layout`, `_colour` and
    `_context` outlive the test that set them, so a test asking for the `full`
    layout leaves every later test drawing that way. Nothing bites today only
    because of the order the files happen to sort in, which is a property of
    their names rather than of anything anybody decided.
    """
    try:
        yield
    finally:
        # Rebuilt from the environment rather than from a saved copy, because
        # that is what settings.py did at import and is therefore what the
        # rest of the suite is entitled to find.
        logs.from_environment()
        logging.config.dictConfig(settings.LOGGING)


def test_an_unhandled_exception_reaches_a_handler_with_debug_false(settings: Settings) -> None:
    """The bug, end to end: a view raises and the traceback has to be readable.

    `raise_request_exception=False` lets Django handle the exception the way it
    would in a deployment instead of re-raising it into the test, which is the
    only arrangement in which the logging is exercised at all.
    """
    settings.DEBUG = False
    settings.ROOT_URLCONF = __name__

    # Held as JSON because that is what a deployment writes, and because which
    # of these fields a terminal drawing would have room for is a question
    # about the reader's window rather than about the record.
    with applied(logging_config("INFO", "json")) as stream:
        response = Client(raise_request_exception=False).get("/explode/")

    assert response.status_code == 500
    record = json.loads(stream.getvalue())

    assert record["logger"] == "django.request"
    assert record["level"] == "error"
    assert "the failure a deployment has to be able to see" in record["exception"]
    # Not merely the exception's own text: the stack is the part that says
    # which line raised, and it is what an `AdminEmailHandler` with no
    # recipients was swallowing.
    assert "Traceback (most recent call last)" in record["exception"]


def test_a_refused_host_reaches_a_handler() -> None:
    """`django.security.*` is the other family that had nowhere to go.

    A rejected `Host` header is answered with a bare 400 and no body, so the
    log line is the only account of why. That silence is what made
    inventory-tng-adj present as a crash loop against a healthy database.
    """
    with applied(settings.LOGGING) as stream:
        logging.getLogger("django.security.DisallowedHost").error("Invalid HTTP_HOST header: '10.42.0.17'")

    assert "10.42.0.17" in stream.getvalue()


def test_this_applications_own_loggers_reach_the_same_handler() -> None:
    """`inventory.*` is not under `django`, so the root logger has to carry it.

    Nothing logs yet -- inventory-tng-nb8.9 is where the application starts --
    but the arrangement has to be waiting when it does, or the first
    `getLogger` call ships into a deployment that discards it.
    """
    with applied(logging_config("INFO", "json")) as stream:
        logging.getLogger("inventory.ledger").warning("an append was refused")

    record = json.loads(stream.getvalue())

    assert record["event"] == "an append was refused"
    assert record["logger"] == "inventory.ledger"


def test_the_handler_writes_to_standard_output() -> None:
    """Held separately because every test above substitutes that destination.

    Standard output is the whole point of the change: it is what a container
    runtime collects and what `kubectl logs` reads.
    """
    assert settings.LOGGING["handlers"]["stdout"]["stream"] == "ext://sys.stdout"


def test_nothing_is_filtered_by_whether_debug_is_on() -> None:
    """The precise defect. Django's own handlers each carry one of these.

    Not "no filters at all", which is what this asserted until
    `inventory_tng.refusals` arrived: there is one filter now, and it rations a
    single logger family rather than selecting an environment. What has to stay
    true is the thing that made Django's arrangement useless in a deployment --
    that no record here is admitted or dropped on account of `DEBUG`.
    """
    declared = settings.LOGGING["filters"]

    for handler in settings.LOGGING["handlers"].values():
        for named in handler.get("filters", []):
            assert declared[named]["()"] is refusals.Bounded, "a filter this test has not been told about"


def test_a_lower_level_admits_less() -> None:
    """The level reaches both the root logger and Django's own."""
    config = logging_config("WARNING")

    assert config["root"]["level"] == "WARNING"
    assert config["loggers"]["django"]["level"] == "WARNING"


def test_django_records_are_handled_once() -> None:
    """`django` has a handler of its own, so it must not propagate as well.

    Otherwise every warning Django emits is printed twice, which is the usual
    result of naming a logger in `dictConfig` without thinking about the root.
    """
    assert logging_config("INFO")["loggers"]["django"]["propagate"] is False


@pytest.mark.parametrize("requested", ["debug", " DEBUG ", "Debug"])
def test_a_level_is_read_regardless_of_how_it_was_typed(requested: str) -> None:
    """It arrives from a `.env` file or a ConfigMap, where both happen."""
    assert log_level(requested) == "DEBUG"


def test_a_level_python_does_not_know_stops_the_process() -> None:
    """Rather than falling back and leaving the developer to wonder."""
    with pytest.raises(ValueError, match="VERBOSE"):
        log_level("VERBOSE")


def test_the_refusal_says_what_would_have_worked() -> None:
    """A message naming the mistake and not the alternatives is half a message."""
    with pytest.raises(ValueError, match="INFO"):
        log_level("VERBOSE")


# --------------------------------------------------------------------------
# The record's shape, and the two drawings of it.
# --------------------------------------------------------------------------


def test_a_json_line_parses_and_carries_the_whole_contract() -> None:
    """What a collector is promised, asserted as a collector would see it.

    `trace_id` and `span_id` are in the contract before anything fills them,
    so switching tracing on later does not change the shape of a line and a
    saved stream stays parseable by whatever was already parsing it.
    """
    with applied(logging_config("INFO", "json")) as stream:
        structlog.get_logger("inventory.ledger").warning("an append was refused", reason="closed")

    record = json.loads(stream.getvalue())

    assert record["event"] == "an append was refused"
    assert record["level"] == "warning"
    assert record["logger"] == "inventory.ledger"
    assert record["reason"] == "closed", "a key the caller supplied, and this application declared, survives"
    assert record["trace_id"] == ""
    assert record["span_id"] == ""
    assert record["timestamp"].startswith("20")


def test_the_timestamp_carries_the_date_and_the_offset() -> None:
    """A container logs UTC and the terminal reading it does not, so a bare
    time is misleading rather than merely terse. `fromisoformat` refuses
    anything that is not what it claims to be, which is the assertion.
    """
    with applied(logging_config("INFO", "json")) as stream:
        structlog.get_logger("inventory.ledger").info("something happened")

    when = datetime.fromisoformat(json.loads(stream.getvalue())["timestamp"])

    assert when.tzinfo is not None, "no offset means the reader has to guess"


def test_a_library_record_carries_the_same_keys_as_ours() -> None:
    """The reason for the dependency, in one assertion.

    `django.request` and `django.security` know nothing about any of this, and
    a collector should not meet two shapes in one stream because of that.
    """
    with applied(logging_config("INFO", "json")) as stream:
        logging.getLogger("django.security.DisallowedHost").error("Invalid HTTP_HOST header")

    record = json.loads(stream.getvalue())

    assert record["logger"] == "django.security.DisallowedHost"
    assert record["level"] == "error"
    assert "timestamp" in record
    assert record["trace_id"] == ""


def test_a_traceback_survives_into_the_json() -> None:
    """The whole point of inventory-tng-zya, now that the drawing has changed."""
    with applied(logging_config("INFO", "json")) as stream:
        try:
            raise RuntimeError("what a deployment has to be able to see")
        except RuntimeError:
            logging.getLogger("django.request").exception("Internal Server Error: /api/items/")

    record = json.loads(stream.getvalue())

    assert "Traceback (most recent call last)" in record["exception"]
    assert "what a deployment has to be able to see" in record["exception"]


def test_the_console_drawing_holds_the_same_record() -> None:
    """Only the drawing differs. That is the property worth the dependency."""
    with applied(logging_config("INFO", "console")) as stream:
        structlog.get_logger("inventory.ledger").warning("an append was refused", reason="closed")

    drawn = stream.getvalue()

    assert "an append was refused" in drawn
    assert "WARNING" in drawn
    assert "reason='closed'" in drawn
    assert not drawn.lstrip().startswith("{"), "the console drawing is not JSON"


def test_a_format_that_is_neither_stops_the_process() -> None:
    with pytest.raises(ValueError, match="console"):
        log_format("pretty")


def test_a_format_is_read_regardless_of_how_it_was_typed() -> None:
    assert log_format(" JSON ") == "json"


# --------------------------------------------------------------------------
# Turning one subsystem up.
# --------------------------------------------------------------------------


def test_one_logger_can_be_raised_without_raising_everything() -> None:
    """The difference between a usable log and a merely present one.

    A single level for the whole process means turning up the importer also
    turns up every query Django runs, and the line you were reading is now one
    in a thousand.
    """
    config = logging_config("WARNING", "json", {"inventory.sheet": "DEBUG"})

    with applied(config) as stream:
        logging.getLogger("inventory.sheet").debug("row 41 staged")
        logging.getLogger("inventory.ledger").debug("not this one")

    written = stream.getvalue()

    assert "row 41 staged" in written
    assert "not this one" not in written


def test_a_named_logger_does_not_get_a_second_copy_of_every_record() -> None:
    """It propagates to the root's handler rather than carrying one of its own."""
    config = logging_config("INFO", "json", {"inventory.sheet": "DEBUG"})

    assert "handlers" not in config["loggers"]["inventory.sheet"]
    assert config["loggers"]["inventory.sheet"]["propagate"] is True


def test_the_levels_are_read_from_one_string() -> None:
    assert per_logger_levels("inventory.sheet=DEBUG,django.db.backends=debug") == {
        "inventory.sheet": "DEBUG",
        "django.db.backends": "DEBUG",
    }


def test_an_empty_setting_names_no_loggers() -> None:
    assert per_logger_levels("") == {}
    assert per_logger_levels(" , ") == {}


def test_an_entry_that_is_not_a_pair_stops_the_process() -> None:
    """`inventory.sheet:DEBUG` is the plausible typo, and it means nothing."""
    with pytest.raises(ValueError, match=r"inventory\.sheet:DEBUG"):
        per_logger_levels("inventory.sheet:DEBUG")


def test_an_entry_naming_a_level_python_does_not_know_stops_it_too() -> None:
    with pytest.raises(ValueError, match="VERBOSE"):
        per_logger_levels("inventory.sheet=VERBOSE")


# --------------------------------------------------------------------------
# gunicorn, and the announcement.
# --------------------------------------------------------------------------


def test_gunicorn_is_in_the_same_arrangement_as_everything_else() -> None:
    """It logs an access line per request through handlers of its own.

    Left alone that is plain text beside our JSON, so one stream carries two
    formats and whatever parses it meets a line it was not written for.
    """
    config = logging_config("INFO", "json")

    for logger in ("gunicorn.error", "gunicorn.access"):
        assert config["loggers"][logger]["handlers"] == ["stdout"]
        assert config["loggers"][logger]["propagate"] is False


def test_a_json_stream_is_never_told_about_a_layout() -> None:
    """A collector has no terminal and should not have to skip a line of prose."""
    assert configure("json", "minimal") == ""


def test_a_terminal_drawing_announces_what_it_settled_on() -> None:
    assert "minimal" in configure("console", "minimal")


def test_the_widest_drawing_announces_nothing() -> None:
    """The rule is `announcement`'s; this holds that `configure` defers to it."""
    assert configure("console", "full") == ""


def test_notset_is_not_a_level_and_is_refused_like_any_other_word() -> None:
    """It is in Python's mapping and is not a level; `log_level` says why.

    Held because the check and its refusal message disagreed about it: the
    message left it out of the suggestions while the check let it through.
    """
    with pytest.raises(ValueError, match="NOTSET"):
        log_level("NOTSET")

    with pytest.raises(ValueError, match="NOTSET"):
        per_logger_levels("inventory.sheet=NOTSET")


def test_the_refusal_does_not_advertise_notset_either() -> None:
    """A message listing a value the check rejects is worse than no message."""
    with pytest.raises(ValueError) as refused:
        log_level("VERBOSE")

    assert "NOTSET" not in str(refused.value).split("Use one of:")[1]


# --------------------------------------------------------------------------
# What the two entry points read, and what a library's record carries.
# --------------------------------------------------------------------------


def test_a_setting_cleared_rather_than_deleted_means_the_default() -> None:
    """django-environ applies a default only when a variable is ABSENT.

    So clearing the value in `.env` rather than deleting the line -- which
    that file all but invites, shipping two of these empty -- made the whole
    backend unbootable, with a refusal that never mentioned the file. The same
    shape blanks a chart value into a CrashLoopBackOff.
    """
    assert setting("DJANGO_LOG_LEVEL", {"DJANGO_LOG_LEVEL": ""}) == "INFO"
    assert setting("DJANGO_LOG_LEVEL", {"DJANGO_LOG_LEVEL": "   "}) == "INFO"
    assert setting("DJANGO_LOG_FORMAT", {}) == "console"
    assert setting("DJANGO_LOG_LEVEL", {"DJANGO_LOG_LEVEL": "debug"}) == "debug"


def test_both_entry_points_read_the_same_five_variables() -> None:
    """gunicorn's config file and Django's settings module each used to read
    them with defaults of their own, so a master drawing columns while its
    workers drew JSON was one stream in two formats -- the exact thing the
    gunicorn configuration exists to prevent.
    """
    config, said = from_environment({"DJANGO_LOG_FORMAT": "json", "DJANGO_LOG_LEVEL": "WARNING"})

    assert config["root"]["level"] == "WARNING"
    assert said == "", "a JSON stream is never told about a layout"


def test_a_layout_is_validated_even_when_nothing_will_draw_columns() -> None:
    """Or a typo stops a laptop and starts a cluster, which is the wrong way
    round for a mistake to be found.
    """
    with pytest.raises(ValueError, match="tiny"):
        from_environment({"DJANGO_LOG_FORMAT": "json", "DJANGO_LOG_LAYOUT": "tiny"})


def test_the_context_setting_is_refused_like_every_other() -> None:
    """It was the one with no validator, and `show` silently meant `hidden`.

    It lives beside the layout in `console`, because both steer that module
    and both are read twice -- once by the process drawing its own records and
    once by `pretty-logs` drawing somebody else's.
    """
    assert console.log_context("shown") is True
    assert console.log_context("HIDDEN") is False

    with pytest.raises(ValueError, match="show"):
        console.log_context("show")


def test_the_status_code_of_a_django_error_reaches_the_record() -> None:
    """Django attaches it through `extra=`, which the chain dropped entirely.

    `log_response` handles every 4xx and 5xx -- DisallowedHost, CSRF failures,
    404s, unhandled 500s -- so no record in the system carried a status code.
    """
    with applied(logging_config("INFO", "json")) as stream:
        logging.getLogger("django.request").warning("Not Found: /nope", extra={"status_code": 404})

    assert json.loads(stream.getvalue())["status_code"] == 404


def test_but_not_the_request_object_django_puts_beside_it() -> None:
    """`log_response` also passes the HttpRequest, which is not a log field.

    It is large, it does not serialise, and it holds the personal data the
    epic's redaction issue exists to keep out of telemetry entirely.
    """
    with applied(logging_config("INFO", "json")) as stream:
        logging.getLogger("django.request").warning(
            "Not Found: /nope", extra={"status_code": 404, "request": object(), "secret": "no"}
        )

    record = json.loads(stream.getvalue())

    assert "request" not in record
    assert "secret" not in record, "the allowlist is deny-by-default, like the rest of this epic"


def test_a_percent_style_call_interpolates_the_way_the_stdlib_one_does() -> None:
    """`%s` is what whoever writes the first getLogger call will reach for.

    Without the formatter, structlog kept the literal `%d` and carried the
    argument off in a stray `positional_args` key, so the two paths this
    module promises are identical diverged on the most habitual idiom there is.
    """
    with applied(logging_config("INFO", "json")) as stream:
        structlog.get_logger("inventory.sheet").info("imported %d rows", 41)

    record = json.loads(stream.getvalue())

    assert record["event"] == "imported 41 rows"
    assert "positional_args" not in record


def test_a_python_warning_arrives_as_a_record_like_anything_else() -> None:
    """Otherwise every deprecation and every naive datetime goes to standard
    error as two lines of unparseable text -- contradicting both "one format"
    and "standard output and nowhere else" at once.

    The hook is called directly rather than by raising a warning, because
    pytest wraps every test in `catch_warnings`: a real `warnings.warn` here
    would be intercepted by the harness and never reach the arrangement under
    test. `configure` is what installs the hook, so calling it is the assertion.
    """
    # `captureWarnings` is guarded against installing twice, and something
    # earlier in the suite has already installed it -- while pytest has since
    # put its own recorder back. Releasing it first is what lets `configure`
    # do the thing being tested rather than return quietly.
    logging.captureWarnings(False)
    configure("json")

    with applied(logging_config("INFO", "json")) as stream:
        warnings.showwarning("a naive datetime reached the ledger", RuntimeWarning, "ledger.py", 9)

    record = json.loads(stream.getvalue())

    assert record["logger"] == "py.warnings"
    assert "a naive datetime reached the ledger" in record["event"]


def test_the_console_hides_context_and_keeps_what_the_caller_passed() -> None:
    """The property decision 0021 says the dependency was taken to buy: the
    two drawings differ only in drawing. Hiding by key NAME broke it, because
    `status` is an ordinary word for a caller to use.
    """
    with (
        structlog.contextvars.bound_contextvars(request_id="9f2c1a"),
        applied(logging_config("INFO", "console")) as stream,
    ):
        structlog.get_logger("inventory.ledger").info("append refused", status=500)

    drawn = stream.getvalue()

    assert "status=500" in drawn
    assert "9f2c1a" not in drawn


def test_and_the_json_keeps_both_and_none_of_the_bookkeeping() -> None:
    with (
        structlog.contextvars.bound_contextvars(request_id="9f2c1a"),
        applied(logging_config("INFO", "json")) as stream,
    ):
        structlog.get_logger("inventory.ledger").info("append refused", status=500)

    record = json.loads(stream.getvalue())

    assert record["status"] == 500
    assert record["request_id"] == "9f2c1a"
    assert record["bound"] == ["request_id", "span_id", "trace_id"], (
        "provenance travels with the record, so the reader hides what the writer would have"
    )


def test_the_layout_is_settled_once_and_not_remeasured_per_record() -> None:
    """Otherwise a resize changes the shape of the output after the process
    announced what it would be -- which is the one thing this is not to do.
    """
    configure("console", "full")
    before = logs._layout

    with applied(logging_config("INFO", "console")) as stream:
        structlog.get_logger("inventory.ledger").info("first")

    assert logs._layout is before
    assert "2026-" in stream.getvalue() or "20" in stream.getvalue()


# --------------------------------------------------------------------------
# The two files that have to agree about what compose runs.
# --------------------------------------------------------------------------

REPO_ROOT = Path(settings.BASE_DIR).parent.parent


def test_the_compose_stack_really_does_write_json() -> None:
    """`.env.sample` and `compose.yaml` decide this between them, and neither
    says so on its own.

    docker compose reads `.env` for `${...}` substitution, and
    `scripts/bootstrap-dev.sh` copies the sample to it -- so a value set in the
    sample is not a default compose can fall back from. Setting the format
    there once turned off the JSON that stack exists to demonstrate, in a way
    that no test and no reader of either file would notice.
    """
    sample = (REPO_ROOT / ".env.sample").read_text().splitlines()
    compose = (REPO_ROOT / "compose.yaml").read_text()

    live = [line for line in sample if line.startswith("DJANGO_LOG_FORMAT=")]

    assert not live, f".env.sample must not set the format compose falls back from: {live}"
    assert "DJANGO_LOG_FORMAT: ${DJANGO_LOG_FORMAT:-json}" in compose
