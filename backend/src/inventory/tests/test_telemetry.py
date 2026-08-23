"""Traces and metrics: whether the SDK starts, and where.

The two things worth holding are the two ways this arrangement fails silently.
A checkout with no collector must pay nothing, or every developer carries the
cost of a feature nobody there uses. And an SDK started before gunicorn forks
exports nothing at all, with no error anywhere -- so the hook that avoids that
is asserted to exist and to be the thing that starts it.
"""

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
from django.conf import settings
from django.db import connection

from inventory.tests.conftest import COLLECTOR
from inventory_tng import telemetry


def test_no_endpoint_means_no_sdk() -> None:
    """The ordinary state of a checkout, and it has to cost nothing.

    Not an SDK started and left idle: no exporter thread, no sampler, no
    instrumented cursor, and not even the import, which is the largest part of
    the cost.
    """
    assert telemetry.start() is False


def test_an_endpoint_set_to_nothing_is_no_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read like every other setting here, so a cleared line in `.env` means
    what it says rather than a collector at the empty address.
    """
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")

    assert telemetry.endpoint("traces") == ""


def test_the_general_endpoint_gains_the_signal_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The two variables are not interchangeable; `endpoint` says how they differ."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", COLLECTOR + "/")

    assert telemetry.endpoint("traces") == f"{COLLECTOR}/v1/traces"
    assert telemetry.endpoint("metrics") == f"{COLLECTOR}/v1/metrics"


def test_a_per_signal_endpoint_is_taken_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    """Which is what lets a deployment send its two signals to two places."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", COLLECTOR)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://elsewhere:4318/v1/traces")

    assert telemetry.endpoint("traces") == "http://elsewhere:4318/v1/traces"
    assert telemetry.endpoint("metrics") == f"{COLLECTOR}/v1/metrics"


def test_metrics_alone_are_enough_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """A collector configured for one signal and not the other must not leave
    both off, which is what asking only about traces did.
    """
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "http://collector:4318/v1/metrics")

    assert telemetry.endpoint("traces") == ""
    assert telemetry.endpoint("metrics") != ""


def test_the_specifications_own_off_switch_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    """`disabled` says why the specification's own switch has to be honoured."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", COLLECTOR)
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")

    assert telemetry.disabled() is True
    assert telemetry.start() is False


def test_a_deployment_that_configures_nothing_samples_conservatively(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rate that is safe when ignored beats one correct when attended to.

    Decision 0021 is where that trade is argued.
    """
    monkeypatch.delenv("OTEL_TRACES_SAMPLER_ARG", raising=False)

    assert telemetry.sampling_ratio() == 0.1


def test_and_every_configuration_this_repository_ships_raises_it() -> None:
    """The other half of the same decision: what is wanted is written down."""
    root = Path(settings.BASE_DIR).parent.parent

    assert "OTEL_TRACES_SAMPLER_ARG: ${OTEL_TRACES_SAMPLER_ARG:-1.0}" in (root / "compose.yaml").read_text()
    assert 'tracesSamplerArg: "1.0"' in (root / "infra/helm/inventory-tng/values.yaml").read_text()


@pytest.mark.parametrize("ratio", ["1.0", "0", "0.25"])
def test_a_rate_that_is_a_fraction_is_read(ratio: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", ratio)

    assert telemetry.sampling_ratio() == float(ratio)


@pytest.mark.parametrize("ratio", ["all", "1.5", "-0.1", "1,0"])
def test_a_rate_that_is_not_stops_the_process(ratio: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rather than being quietly given a rate nobody asked for."""
    monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", ratio)

    with pytest.raises(ValueError, match="OTEL_TRACES_SAMPLER_ARG"):
        telemetry.sampling_ratio()


def test_a_sampler_name_that_is_not_understood_stops_it_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shipping the variable in four files and reading none of them is worse
    than not offering it: an operator sets `always_off` during an incident and
    watches the collector keep filling.
    """
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "traceidratio")

    with pytest.raises(ValueError, match="parentbased_traceidratio"):
        telemetry.sampler_name()


def test_the_settings_are_read_whether_or_not_anything_uses_them(monkeypatch: pytest.MonkeyPatch) -> None:
    """`validate` is called from the settings module, and `validate` says why."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", "1,0")

    with pytest.raises(ValueError, match="OTEL_TRACES_SAMPLER_ARG"):
        telemetry.validate()


def test_a_caller_cannot_raise_the_rate_this_backend_records_at() -> None:
    """The finding this test exists for: `ParentBased` alone does not do it.

    Its `remote_parent_sampled` defaults to ALWAYS_ON, so a bare one records
    every request arriving with the W3C sampled bit set -- one header on an
    unauthenticated request. `telemetry.sampler` says what that would cost.
    """
    from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ParentBased, TraceIdRatioBased

    # Through the wrapper that honours a signed debug token, which sits on top
    # of whichever sampler the configuration chose -- inventory_tng.debugging.
    built = telemetry.sampler("parentbased_traceidratio", 0.25).chosen

    assert isinstance(built, ParentBased)
    assert isinstance(built._root, TraceIdRatioBased)
    assert isinstance(built._remote_parent_sampled, TraceIdRatioBased), (
        "a remote caller claiming sampled must go through the ratio, not around it"
    )
    assert built._remote_parent_not_sampled is ALWAYS_OFF


def test_and_the_refusal_is_measured_rather_than_asserted() -> None:
    """A thousand requests, each carrying `sampled`, against a rate of 0.001.

    The shape assertions above would pass on the bare `ParentBased` that
    shipped, because it is a `ParentBased` too. This is the one that would not.
    """
    from opentelemetry.trace import SpanContext, TraceFlags, set_span_in_context
    from opentelemetry.trace.span import NonRecordingSpan

    built = telemetry.sampler("parentbased_traceidratio", 0.001)
    recorded = 0
    for index in range(1, 1001):
        # `TraceIdRatioBased` reads the low 64 bits, so a counter would put
        # every id below the bound and prove nothing. Spread them the way real
        # ids are spread.
        trace_id = (index * 0x9E3779B97F4A7C15) & ((1 << 128) - 1)
        parent = NonRecordingSpan(
            SpanContext(trace_id=trace_id, span_id=index, is_remote=True, trace_flags=TraceFlags(0x01))
        )
        decision = built.should_sample(set_span_in_context(parent), trace_id, "GET /api/items")
        recorded += bool(decision.decision.is_sampled())

    assert recorded < 50, f"{recorded} of 1000 caller-marked requests recorded at a rate of 0.001"


def test_always_on_is_what_it_says() -> None:
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON

    assert telemetry.sampler("always_on", 0.5).chosen is ALWAYS_ON


def test_and_always_off_is_off_even_to_a_signed_token() -> None:
    """The one sampler the debug wrapper does not sit on top of, and
    `telemetry.sampler` is where that exception is argued.
    """
    from opentelemetry.sdk.trace.sampling import ALWAYS_OFF

    assert telemetry.sampler("always_off", 0.5) is ALWAYS_OFF


def test_the_sdk_is_started_from_the_worker_and_not_the_master() -> None:
    """A shape assertion, deliberately labelled as one: it reads the file.

    What it is worth is that the hook exists and is what calls `start`. The
    behaviour it used to claim to protect against is measured below, and no
    longer holds.
    """
    config = (Path(settings.BASE_DIR) / "gunicorn.conf.py").read_text()

    assert "def post_fork(" in config
    assert config.index("def post_fork(") < config.index("start(django=False)")
    assert "preload_app" not in config, "preloading would import the application before the fork"


def test_a_span_made_after_a_fork_is_exported_by_a_processor_made_before_it() -> None:
    """The correction. This is the claim the epic and decision 0021 were built
    on, measured rather than repeated.

    Decision 0021 carries the correction and the reasoning; this is the
    measurement it rests on.

    If this ever fails, the old reasoning has come back and `post_fork` is
    load-bearing again -- which is worth knowing either way, and is why this
    is a test rather than a paragraph.
    """
    proof = """
import os, sys, tempfile
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExportResult

path = sys.argv[1]

class ToFile:
    def export(self, spans):
        with open(path, "a") as out:
            for span in spans:
                print(span.name, file=out)
        return SpanExportResult.SUCCESS

    def shutdown(self): return None
    def force_flush(self, timeout_millis=30000): return True

provider = TracerProvider()
provider.add_span_processor(BatchSpanProcessor(ToFile()))

if os.fork() == 0:
    with provider.get_tracer("child").start_as_current_span("made-in-child"):
        pass
    provider.force_flush()
    os._exit(0)
os.wait()
"""
    with tempfile.NamedTemporaryFile(suffix=".txt") as exported:
        finished = subprocess.run(
            [sys.executable, "-c", proof, exported.name],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

        assert finished.returncode == 0, finished.stderr
        assert "made-in-child" in Path(exported.name).read_text(), (
            "the SDK no longer rebuilds its batch processor after a fork; "
            "post_fork is load-bearing again and the documents saying it is not are wrong"
        )


def test_the_environment_is_the_standard_one() -> None:
    """No second set of names to learn: every knob is an OTEL_ variable that
    the specification already defines, so documentation elsewhere applies.
    """
    sample = (Path(settings.BASE_DIR).parent.parent / ".env.sample").read_text()

    for name in ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_SERVICE_NAME", "OTEL_RESOURCE_ATTRIBUTES"):
        assert name in sample, f"{name} is not in .env.sample"


def test_nothing_in_env_sample_defeats_the_compose_defaults() -> None:
    """compose reads that file for interpolation, so a value set there is not
    a default compose can fall back from -- the trap `DJANGO_LOG_FORMAT`
    already carries six lines about, and which the OTEL_ block walked into.
    """
    root = Path(settings.BASE_DIR).parent.parent
    sample = (root / ".env.sample").read_text().splitlines()

    live = [line for line in sample if line.startswith(("OTEL_SERVICE_NAME=", "OTEL_RESOURCE_ATTRIBUTES="))]

    assert not live, f".env.sample must not set what compose defaults: {live}"


# --------------------------------------------------------------------------
# What a request actually produces.
# --------------------------------------------------------------------------


def test_a_request_produces_a_server_span_named_for_its_route(recorded: Any, db: Any, client: Any) -> None:
    """Named for the route rather than the concrete path, which is what makes
    a hundred item lookups one line on a dashboard instead of a hundred.
    """
    client.get("/api/healthz")

    spans = recorded.get_finished_spans()
    server = [span for span in spans if span.kind.name == "SERVER"]

    assert server, f"no server span among {[span.name for span in spans]}"
    assert "healthz" in server[0].name
    assert server[0].attributes["http.route"] == "api/healthz"
    assert server[0].attributes["http.status_code"] == 200


def test_the_handler_a_server_builds_has_the_middleware_too(substituted: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The arrangement above is not the one a deployment runs, and for a while
    the difference was the whole of the instrumentation.

    Django's test client builds a handler per request, so it picks up a
    middleware inserted at any moment, and every assertion above passed for
    months against a server that had none. `wsgi.py` says what the order has to
    be; decision 0021 says what getting it wrong looked like.

    So this drives a request through the module a server imports, built once
    and in that module's own order, and asserts the span that was missing.

    A route that touches no database, because a real handler disconnects the
    test's connection on `request_started` the way it disconnects a worn-out
    one in a deployment. The test client suppresses that; nothing here should.
    """
    import importlib

    from django.test import RequestFactory
    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

    # Every trace, and before the module is imported: importing it is what
    # starts the SDK, and a sampler built at a tenth would make this assertion
    # about the dice rather than about the middleware.
    monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", "1.0")

    from inventory_tng import wsgi

    try:
        served = importlib.reload(wsgi)
        served.application(RequestFactory().get("/api").environ, lambda status, headers: None)
    finally:
        PsycopgInstrumentor().uninstrument()
        DjangoInstrumentor().uninstrument()
        connection.close()

    spans = substituted.get_finished_spans()
    server = [span for span in spans if span.kind.name == "SERVER"]

    assert server, f"no server span among {[span.name for span in spans]}"
    assert server[0].attributes["http.route"] == "api"


def test_and_the_queries_it_ran_sit_beneath_it(recorded: Any, db: Any, client: Any) -> None:
    """The whole point of instrumenting the driver as well as the framework:
    one request is one tree, so a slow endpoint can be read down to the query
    that made it slow rather than guessed at.

    `/api/healthz` runs a trivial query for exactly this reason -- it is the
    one endpoint whose job includes touching the database.
    """
    client.get("/api/healthz")

    spans = recorded.get_finished_spans()
    server = next(span for span in spans if span.kind.name == "SERVER")
    client_spans = [span for span in spans if span.kind.name == "CLIENT"]

    assert client_spans, f"no database span among {[span.name for span in spans]}"
    assert any(span.parent and span.parent.span_id == server.context.span_id for span in client_spans), (
        "a query ran outside the request that caused it"
    )


def test_a_log_record_carries_the_trace_it_was_written_inside(recorded: Any, db: Any) -> None:
    """Which is what makes a log line findable from a trace and the other way
    round. nb8.1 put the keys in the contract; this is them stopping being
    empty, without the shape of a line changing on the day it happened.
    """
    import json

    import structlog
    from opentelemetry import trace

    from inventory.tests.test_logging import applied
    from inventory_tng.logs import logging_config

    assert recorded is not None, "the fixture has installed a recording provider"

    with (
        applied(logging_config("INFO", "json")) as stream,
        trace.get_tracer(__name__).start_as_current_span("a unit of work"),
    ):
        structlog.get_logger("inventory.ledger").info("something happened")

    record = json.loads(stream.getvalue())

    assert len(record["trace_id"]) == 32
    assert len(record["span_id"]) == 16
    assert int(record["trace_id"], 16) != 0


def test_starting_wires_the_provider_the_sampler_and_both_instrumentations(
    substituted: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`start` itself, with only the exporters substituted."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    instrumented: list[str] = []
    monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", "0.5")
    monkeypatch.setattr(
        "opentelemetry.instrumentation.django.DjangoInstrumentor.instrument",
        lambda self, **k: instrumented.append("django"),
    )
    monkeypatch.setattr(
        "opentelemetry.instrumentation.psycopg.PsycopgInstrumentor.instrument",
        lambda self, **k: instrumented.append("psycopg"),
    )

    started = telemetry.start()
    provider = trace.get_tracer_provider()

    assert started is True
    assert instrumented == ["psycopg", "django"], "the driver first: it is patched at connect"
    assert isinstance(provider, TracerProvider), "the global provider is the SDK's, not the API's no-op"
    assert "0.5" in provider.sampler.get_description()


def test_the_hook_that_runs_before_the_application_says_so_itself(
    substituted: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`start(django=False)` is gunicorn's hook saying what it knows.

    Left to `settings.configured`, the deferral would be a property of global
    Django state three files away that a reader of `post_fork` could not
    check -- and it would silently stop holding the day somebody imports a
    Django setting into the gunicorn configuration.
    """
    instrumented: list[str] = []
    monkeypatch.setattr(
        "opentelemetry.instrumentation.django.DjangoInstrumentor.instrument",
        lambda self, **k: instrumented.append("django"),
    )
    monkeypatch.setattr("opentelemetry.instrumentation.psycopg.PsycopgInstrumentor.instrument", lambda self, **k: None)
    config = (Path(settings.BASE_DIR) / "gunicorn.conf.py").read_text()

    assert telemetry.start(django=False) is True
    assert instrumented == [], "the framework is not instrumented before it is imported"
    assert "start(django=False)" in config, "and the hook is what says so"


def test_metrics_can_be_configured_without_traces(substituted: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """One signal on and the other off has to work in both directions."""
    installed: list[str] = []
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "http://collector:4318/v1/metrics")
    monkeypatch.setattr("opentelemetry.metrics.set_meter_provider", lambda provider: installed.append("metrics"))
    monkeypatch.setattr("opentelemetry.instrumentation.psycopg.PsycopgInstrumentor.instrument", lambda self, **k: None)
    monkeypatch.setattr(telemetry, "_instrumented_django", True)

    assert telemetry.start() is True
    assert installed == ["metrics"]


def test_a_sampler_name_that_is_understood_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", " ALWAYS_OFF ")

    assert telemetry.sampler_name() == "always_off"


def test_django_is_not_instrumented_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard that lets `post_fork` and `wsgi.py` both call `start`."""
    monkeypatch.setattr(telemetry, "_instrumented_django", True)

    assert telemetry.instrument_django() is False


def test_and_not_at_all_before_there_are_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The safety net behind `start(django=False)`.

    The flag at the call site is what a reader checks; this is what holds if
    somebody calls `start()` from somewhere Django is not ready.
    """
    from django.conf import settings as django_settings
    from django.utils.functional import empty

    # `configured` is a property over `_wrapped`, so that is what has to move.
    # monkeypatch puts it back, and nothing between here and there reads it.
    monkeypatch.setattr(django_settings, "_wrapped", empty)

    assert telemetry.instrument_django() is False
