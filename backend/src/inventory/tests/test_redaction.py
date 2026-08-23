"""That telemetry carries what this application declared, and nothing else.

The failure being held against is quiet by construction. Nobody reads a span
attribute the way they read a template, so a field that should never have left
the process leaves it every second for months and the first anybody hears is
from whoever holds the collector. There is no error, no test goes red on its
own, and the data is already somewhere else.

So these assert both directions. The allowlist keeps what a dashboard needs --
a route, a status, a statement -- and drops what it does not, including an
attribute invented inside a request purely so that this suite can watch it not
arrive. And the toggle that re-admits one enumerated group is held to all four
of the conditions decision 0021 puts on it, because a toggle that is merely
present is one that gets left on.
"""

import logging
import logging.config
from pathlib import Path
from typing import Any

import pytest
import structlog
from django.conf import settings

from inventory.tests.helpers import applied
from inventory.tests.helpers import one_record as written
from inventory_tng import redaction, telemetry
from inventory_tng.logs import from_environment, logging_config

REPO_ROOT = Path(settings.REPO_ROOT)


def server_span(recorded: Any) -> Any:
    """The span for the request itself, rather than the queries beneath it."""
    return next(span for span in recorded.get_finished_spans() if span.kind.name == "SERVER")


# --------------------------------------------------------------------------
# Spans: what a real request leaves behind
# --------------------------------------------------------------------------


def test_a_request_leaves_the_route_and_not_the_caller(recorded: Any, db: Any, client: Any) -> None:
    """The measurement this issue exists to change.

    Django's instrumentation puts the caller's address on every server span
    and the concrete URL beside it. Neither reaches an exporter now; what a
    dashboard actually groups by does.
    """
    client.get("/api/healthz")
    attributes = server_span(recorded).attributes

    assert attributes["http.route"] == "api/healthz"
    assert attributes["http.method"] == "GET"
    assert attributes["http.status_code"] == 200

    assert "net.peer.ip" not in attributes, "an IP address is personal data"
    assert "http.url" not in attributes, "the concrete URL is where an identifier ends up"
    assert redaction.MARKER not in attributes, "nothing was re-admitted, so nothing is claimed"


def test_an_attribute_nobody_declared_never_reaches_an_exporter(recorded: Any, db: Any, client: Any) -> None:
    """Deny-by-default, proven by adding one rather than by reading the list.

    A denylist would pass this test by not having heard of `volunteer.email`
    yet, which is exactly the failure the arrangement is chosen against.
    """
    from opentelemetry import trace

    with trace.get_tracer(__name__).start_as_current_span("an append") as span:
        span.set_attribute("volunteer.email", "ada@example.net")
        span.set_attribute("http.route", "api/stock/transactions")

    exported = next(span for span in recorded.get_finished_spans() if span.name == "an append")

    assert "volunteer.email" not in exported.attributes
    assert exported.attributes["http.route"] == "api/stock/transactions", "the allowlist is not a blanket refusal"


def test_an_exception_recorded_on_a_span_keeps_its_type_and_loses_the_rest(recorded: Any) -> None:
    """A span's events are attributes too, and the SDK writes them itself."""
    from opentelemetry import trace

    with trace.get_tracer(__name__).start_as_current_span("failing") as span:
        span.record_exception(ValueError("no such volunteer"), attributes={"volunteer.name": "Ada"})

    event = next(event for event in recorded.get_finished_spans()[0].events if event.name == "exception")

    assert event.attributes["exception.type"] == "ValueError"
    assert "volunteer.name" not in event.attributes


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/accounts/login/", "get"),
        ("/api/stock/transactions", "get"),
        ("/api/labels", "get"),
        ("/api/volunteers", "get"),
    ],
)
def test_the_paths_that_touch_people_emit_nothing_about_them(
    recorded: Any, db: Any, client: Any, path: str, method: str
) -> None:
    """Sign-in, the append path and the labels, checked rather than assumed.

    These are the three places decision 0012's two populations meet the
    system, so they are the three worth naming one at a time.
    """
    getattr(client, method)(path)

    for span in recorded.get_finished_spans():
        forbidden = set(span.attributes) & set(redaction.PERSONAL_ATTRIBUTES)
        assert not forbidden, f"{span.name} carried {sorted(forbidden)}"


# --------------------------------------------------------------------------
# Spans, with the toggle on
# --------------------------------------------------------------------------


def test_with_the_toggle_on_the_address_is_recorded_and_said_to_be(recorded_openly: Any, db: Any, client: Any) -> None:
    """Off is the resting state, not the only one -- decision 0021's amendment."""
    client.get("/api/healthz")
    attributes = server_span(recorded_openly).attributes

    assert "net.peer.ip" in attributes
    assert "http.url" in attributes
    assert attributes[redaction.MARKER] is True, "so a collector holder can find it again"


def test_and_the_resource_is_marked_too(recorded_openly: Any, db: Any, client: Any) -> None:
    """Which is how a metric gets marked: it cannot carry the flag itself."""
    client.get("/api/healthz")

    assert server_span(recorded_openly).resource.attributes[redaction.MARKER] is True


def test_a_metric_view_keeps_the_named_attributes_and_no_others() -> None:
    """The metrics SDK's own way of saying deny-by-default."""
    (view,) = redaction.views(admitting=False)
    (widened,) = redaction.views(admitting=True)

    assert "http.route" in view._attribute_keys
    assert "net.peer.ip" not in view._attribute_keys
    assert "net.peer.ip" in widened._attribute_keys


# --------------------------------------------------------------------------
# Log records
# --------------------------------------------------------------------------


def test_a_key_nobody_declared_does_not_reach_a_collector() -> None:
    with applied(logging_config("INFO", "json")) as stream:
        structlog.get_logger("inventory.ledger").warning("an append was refused", volunteer_email="ada@example.net")

    record = written(stream)

    assert "volunteer_email" not in record
    assert record["event"] == "an append was refused", "the message is still the message"


def test_the_contract_every_record_carries_survives_it() -> None:
    """The allowlist must not eat the fields nb8.1 promised a collector."""
    with applied(logging_config("INFO", "json")) as stream:
        structlog.get_logger("inventory.ledger").warning("refused", reason="closed")

    record = written(stream)

    for key in ("timestamp", "level", "logger", "event", "trace_id", "span_id", "bound", "reason"):
        assert key in record, key


def test_django_s_own_query_keys_are_held_back_until_somebody_asks() -> None:
    """`sql` carries the parameters interpolated into it, which in this
    application means volunteers' names.
    """
    with applied(logging_config("INFO", "json")) as stream:
        logging.getLogger("django.db.backends").info(
            "(0.001) SELECT ...", extra={"duration": 0.001, "sql": "SELECT 'Ada Lovelace'", "params": ("Ada",)}
        )

    record = written(stream)

    assert record["duration"] == 0.001, "how long it took is not personal data"
    assert "sql" not in record
    assert "params" not in record


def test_and_are_admitted_by_the_same_toggle_the_addresses_are() -> None:
    """Which is what makes the toggle a thing a developer uses rather than a
    thing only a document mentions.
    """
    with applied(logging_config("INFO", "json"), admitting=True) as stream:
        logging.getLogger("django.db.backends").info("(0.001) SELECT ...", extra={"sql": "SELECT 'Ada Lovelace'"})

    record = written(stream)

    assert record["sql"] == "SELECT 'Ada Lovelace'"
    assert record[redaction.MARKER] is True


def test_the_toggle_admits_the_named_group_and_not_everything() -> None:
    """It is a second list, never a hole in the first.

    Two allowlists sit in the log chain -- `ExtraAdder` stops a record carrying
    an `HttpRequest` around, and the processor at the end decides -- and both
    are fed from the sets in `redaction`. This is the assertion that turning
    the toggle on does not turn either of them off.
    """
    with applied(logging_config("INFO", "json"), admitting=True) as stream:
        logging.getLogger("django.request").warning(
            "refused", extra={"sql": "SELECT 1", "volunteer_email": "ada@example.net", "request": object()}
        )

    record = written(stream)

    assert record["sql"] == "SELECT 1", "the enumerated group"
    assert "volunteer_email" not in record, "and nothing else"
    assert "request" not in record


def test_the_console_drawing_redacts_exactly_as_the_json_one_does() -> None:
    """One chain, two drawings: a field dropped for a collector must not
    survive in the drawing a developer reads, or the two stop being the same
    record and the whole arrangement stops meaning anything.
    """
    with applied(logging_config("INFO", "console")) as stream:
        structlog.get_logger("inventory.ledger").warning("refused", volunteer_email="ada@example.net")

    assert "ada@example.net" not in stream.getvalue()


# --------------------------------------------------------------------------
# The setting itself
# --------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["recorded", " RECORDED ", "Recorded"])
def test_the_toggle_is_read_however_it_was_typed(state: str) -> None:
    assert redaction.personal_data(state) is True


@pytest.mark.parametrize("state", ["redacted", " Redacted "])
def test_and_so_is_the_state_it_rests_in(state: str) -> None:
    assert redaction.personal_data(state) is False


@pytest.mark.parametrize("state", ["true", "false", "1", "0", "on", "off", "yes", "record"])
def test_a_value_it_does_not_recognise_stops_the_process(state: str) -> None:
    """The one setting where being misread in the permissive direction is a
    disclosure, so there is no spelling that quietly means `redacted`.
    """
    with pytest.raises(ValueError, match="TELEMETRY_PERSONAL_DATA"):
        redaction.personal_data(state)


def test_a_cleared_variable_is_the_state_it_rests_in() -> None:
    """Decision 0022, and the direction it has to fall in here."""
    assert redaction.recording({"TELEMETRY_PERSONAL_DATA": "   "}) is False
    assert redaction.recording({}) is False


def test_the_process_says_so_at_startup_and_only_when_it_is_on() -> None:
    assert redaction.announcement(admitting=False) == ""

    said = redaction.announcement(admitting=True)

    assert "TELEMETRY_PERSONAL_DATA" in said
    assert redaction.MARKER in said


@pytest.mark.parametrize("drawn_as", ["console", "json"])
def test_it_is_announced_in_every_drawing_unlike_the_layout(drawn_as: str) -> None:
    """`logs.configure` says why this one is not treated like the layout line
    beside it, which a JSON stream has no use for.
    """
    _, said = from_environment({"DJANGO_LOG_FORMAT": drawn_as, "TELEMETRY_PERSONAL_DATA": "recorded"})

    assert "TELEMETRY_PERSONAL_DATA" in said


def test_a_typo_stops_the_process_before_anything_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """`telemetry.validate` runs on every command, so the refusal is not
    something only a release that turns telemetry on discovers.
    """
    monkeypatch.setenv("TELEMETRY_PERSONAL_DATA", "yes")

    with pytest.raises(ValueError, match="TELEMETRY_PERSONAL_DATA"):
        telemetry.validate()


def test_no_configuration_this_repository_ships_turns_it_on() -> None:
    """The property that makes the toggle an act somebody performs."""
    assert "TELEMETRY_PERSONAL_DATA=redacted" in (REPO_ROOT / ".env.sample").read_text()
    assert "TELEMETRY_PERSONAL_DATA: ${TELEMETRY_PERSONAL_DATA:-redacted}" in (REPO_ROOT / "compose.yaml").read_text()
    assert 'personalData: "redacted"' in (REPO_ROOT / "infra/helm/inventory-tng/values.yaml").read_text()


def test_the_two_groups_do_not_overlap() -> None:
    """A name in both lists would make the toggle meaningless for it, and
    would be invisible: the field would simply always be there.
    """
    assert not redaction.ALLOWED_ATTRIBUTES & redaction.PERSONAL_ATTRIBUTES
    assert not redaction.ALLOWED_LOG_KEYS & redaction.PERSONAL_LOG_KEYS


# --------------------------------------------------------------------------
# The places an attribute hides that are not the span's own
# --------------------------------------------------------------------------


def test_the_callers_address_does_not_survive_under_the_databases_names(recorded: Any, db: Any, client: Any) -> None:
    """The old conventions call the far end of a connection the same thing
    whichever way it points, so the two names that mean a PostgreSQL host on a
    query span mean the caller on a server span.
    """
    client.get("/api/healthz")

    for span in recorded.get_finished_spans():
        if span.kind.name != "SERVER":
            continue
        assert "net.peer.name" not in span.attributes
        assert "net.peer.port" not in span.attributes


def test_the_reason_an_error_span_gives_is_not_carried_verbatim() -> None:
    """The SDK fills the status description from the exception's own text, and
    in this application that includes PostgreSQL quoting the value in a unique
    constraint back at you -- which is a volunteer's address, on a span.
    """
    from opentelemetry.trace import Status, StatusCode

    class Span:
        _attributes: Any = {}
        _status = Status(StatusCode.ERROR, "DETAIL:  Key (email)=(ada@example.net) already exists.")
        _events: Any = ()
        _links: Any = ()
        attributes: Any = {}

        @property
        def status(self) -> Any:
            return self._status

    span = Span()
    redaction.redact_span(span, admitting=False)

    assert "ada@example.net" not in (span._status.description or "")
    assert span._status.status_code is StatusCode.ERROR, "that it failed is not the part being withheld"


def test_a_link_is_stripped_like_everything_else() -> None:
    """Nothing here makes one today. The exporter would carry it if anything
    did, which is the whole reason a deny-by-default list is worth having.
    """
    from opentelemetry.trace import Link, SpanContext, TraceFlags

    link = Link(SpanContext(1, 2, False, TraceFlags(TraceFlags.SAMPLED)), {"volunteer.email": "ada@example.net"})

    class Span:
        _attributes: Any = {}
        _events: Any = ()
        _links = (link,)
        attributes: Any = {}
        status = None

    redaction.redact_span(Span(), admitting=False)

    assert "volunteer.email" not in dict(link.attributes or {})


# --------------------------------------------------------------------------
# The keys the allowlist has to be able to see
# --------------------------------------------------------------------------


def test_a_key_spelled_with_a_leading_underscore_is_not_exempt() -> None:
    """It was, which made the allowlist optional for anybody who typed one."""
    with applied(logging_config("INFO", "json")) as stream:
        structlog.get_logger("inventory.ledger").warning("refused", _volunteer_email="ada@example.net")

    assert "ada@example.net" not in stream.getvalue()


def test_but_structlogs_own_two_still_pass() -> None:
    """Removing them here would leave `remove_processors_meta` with nothing to
    remove and the record with a `_record` key nobody wants.
    """
    with applied(logging_config("INFO", "json")) as stream:
        logging.getLogger("django.request").warning("Not Found: /nope", extra={"status_code": 404})

    record = written(stream)

    assert record["status_code"] == 404, "a foreign record still arrives whole"
    assert "_record" not in record


def test_a_captured_stack_reaches_the_collector() -> None:
    """`stack` was named in the allowlist and `stack_info` is the key that
    exists, so every stack somebody deliberately captured was dropped.
    """
    with applied(logging_config("INFO", "json")) as stream:
        logging.getLogger("inventory.ledger").error("boom", stack_info=True)

    assert "Stack (most recent call last)" in written(stream)["stack_info"]


def test_a_key_nobody_set_is_not_written_as_a_null() -> None:
    """`ExtraAdder` reads an allowed name straight off the record, so every
    standard attribute the list names arrives as a null on every foreign
    record. `stack_info` is the one, and it was on every gunicorn access line.
    """
    with applied(logging_config("INFO", "json")) as stream:
        logging.getLogger("gunicorn.access").info("GET /api/healthz 200 15 4210")

    record = written(stream)

    assert "stack_info" not in record
    assert record["event"] == "GET /api/healthz 200 15 4210"


# --------------------------------------------------------------------------
# The one record no allowlist can reach
# --------------------------------------------------------------------------


def test_gunicorns_access_line_carries_no_address_query_or_user_agent() -> None:
    """It is a message gunicorn assembles, not a set of fields, so the format
    is the redaction. Its default carries all three, and this application has
    `/api/volunteers?search=Ada` -- a volunteer's name, on every access record.
    """
    written_as = redaction.access_log_format(admitting=False)

    assert "%(h)s" not in written_as, "the caller's address"
    assert "%(q)s" not in written_as and "%(r)s" not in written_as, "the query string"
    assert "%(a)s" not in written_as, "the user agent"


def test_but_still_says_what_was_asked_for_and_how_it_went() -> None:
    """A bound that left an access line saying nothing would be met by
    somebody putting the default back.
    """
    written_as = redaction.access_log_format(admitting=False)

    for atom in ("%(m)s", "%(U)s", "%(s)s", "%(b)s", "%(D)s"):
        assert atom in written_as, atom


def test_and_the_toggle_reaches_this_stream_like_every_other() -> None:
    """Otherwise it would be the one place that says "personal data" and
    means "except over there".
    """
    written_as = redaction.access_log_format(admitting=True)

    assert "%(h)s" in written_as
    assert "%(q)s" in written_as
    assert "%(a)s" in written_as


def test_the_configuration_gunicorn_reads_sets_it() -> None:
    """A format defined and never wired up is worse than none, because the
    default is what runs and nothing says so.
    """
    read = (Path(settings.BASE_DIR) / "gunicorn.conf.py").read_text()

    assert "access_log_format = redaction.access_log_format(" in read
