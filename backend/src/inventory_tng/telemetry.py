"""Traces and metrics, over OTLP, when there is somewhere to send them.

Logs do not come through here. They are written to standard output as JSON and
collected from there, for the reasons decision 0021 gives; this is the half
that has no equivalent of "the runtime already collects your output" and so
has to export.

NOTHING REACHES AN EXPORTER UNREDACTED. A span is stripped to an allowlist by
a processor registered before the exporting one, and a metric by a `View` over
every instrument. `redaction` holds both lists, and the argument for them.

NO ENDPOINT MEANS NO SDK. `OTEL_EXPORTER_OTLP_ENDPOINT` unset is the ordinary
state of a checkout with nothing to send to, and it must cost nothing at all --
not a background thread, not a sampler, not an instrumented cursor. So the
whole of this is skipped rather than started and left idle.

STARTED IN THE WORKER, from gunicorn's `post_fork` hook and from `wsgi.py`.
Decision 0021 holds the argument, including a correction worth knowing: the
famous reason for `post_fork` -- an exporter thread not surviving `fork()` --
is not true of the SDK version pinned here, and the hook stays for a different
reason.

STARTED ONLY WHERE SOMETHING IS SERVED, which is what `wsgi.py` and `asgi.py`
being the callers buys: Django imports them when it is about to serve and never
for a management command. The pre-upgrade `migrate` Job gets the same
environment as the web pods, and an SDK there would trace every statement and
then hold up the release flushing them to a collector that may not answer.

READ FROM THE PROCESS ENVIRONMENT, not from an argument. The exporters and the
resource read `os.environ` themselves, so a function taking some other mapping
would honour it in the parts written here and ignore it in the parts that
matter -- which is worse than not offering the choice.
"""

import os
from dataclasses import dataclass
from typing import Any

from inventory_tng import redaction
from inventory_tng.options import missing

# What a deployment that says nothing gets. Low on purpose: an unconfigured
# release pointed at a collector somebody else sized should not be the thing
# that overwhelms it. docs/deployment.md#telemetry says how to change it.
DEFAULT_SAMPLING_RATIO = 0.1

# The sampler names this understands. Anything else is refused rather than
# silently replaced, because a sampler that is not the one asked for is a
# collector still filling during the incident you turned it down for.
SAMPLERS = ("parentbased_traceidratio", "always_off", "always_on")
DEFAULT_SAMPLER = "parentbased_traceidratio"

# What `start` has already done. Django's is separate because its precondition
# is Django's rather than the fork's -- the rest of the work needs only to
# happen in the worker, while instrumenting the framework needs its settings to
# exist. Both suppress the SDK's own "already instrumented" warning, which in a
# project this careful about one log stream is worth not emitting.
_started = False
_instrumented_django = False


def disabled() -> bool:
    """`OTEL_SDK_DISABLED`, the specification's own off switch.

    Honoured because the shipped configuration names standard variables, and
    an operator who reaches for the standard way to turn telemetry off during
    an incident has to have it work.
    """
    return os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() == "true"


def endpoint(signal: str) -> str:
    """Where one signal goes, as a complete URL, or nothing at all.

    The specification has a per-signal variable and a general one, and they
    differ in shape: the per-signal one is the full path, the general one is a
    base that each signal appends its own path to. Resolved here rather than
    left to the exporters, because handing an exporter the general value as if
    it were a signal URL is how metrics came to be posted to `/v1/metrics`
    with no host in front of it.
    """
    specific = os.environ.get(f"OTEL_EXPORTER_OTLP_{signal.upper()}_ENDPOINT", "")
    if not missing(specific):
        return specific.strip()
    general = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if missing(general):
        return ""
    return f"{general.strip().rstrip('/')}/v1/{signal}"


def sampling_ratio() -> float:
    """The fraction of traces recorded, from `OTEL_TRACES_SAMPLER_ARG`.

    Refused rather than defaulted when it is not a fraction, for the reason
    every other setting here is: being quietly given a rate other than the one
    you asked for is how a collector fills up on a Sunday.
    """
    raw = os.environ.get("OTEL_TRACES_SAMPLER_ARG", "")
    if missing(raw):
        return DEFAULT_SAMPLING_RATIO
    try:
        ratio = float(raw.strip())
    except ValueError:
        raise ValueError(f"OTEL_TRACES_SAMPLER_ARG={raw!r} is not a number between 0 and 1.") from None
    if not 0.0 <= ratio <= 1.0:
        raise ValueError(f"OTEL_TRACES_SAMPLER_ARG={raw!r} is not between 0 and 1.")
    return ratio


def sampler_name() -> str:
    """Which sampler, from `OTEL_TRACES_SAMPLER`."""
    raw = os.environ.get("OTEL_TRACES_SAMPLER", "")
    if missing(raw):
        return DEFAULT_SAMPLER
    name = raw.strip().lower()
    if name not in SAMPLERS:
        raise ValueError(f"OTEL_TRACES_SAMPLER={raw!r} is not one of: {', '.join(SAMPLERS)}.")
    return name


@dataclass(frozen=True)
class Configuration:
    """Every telemetry setting, resolved once and refused where it is wrong."""

    traces: str
    metrics: str
    disabled: bool
    sampler: str
    ratio: float
    personal_data: bool

    @property
    def wanted(self) -> bool:
        """Whether anything should be started at all."""
        return not self.disabled and bool(self.traces or self.metrics)


def configuration() -> Configuration:
    """Read the environment, raising on anything it cannot make sense of.

    One function rather than a list of the calls that can refuse, because a
    list has to be remembered: the next setting that can be wrong would be
    read by `start` and not by the validation, and would ship green until the
    release that turns telemetry on stopped every replica at once.
    """
    return Configuration(
        traces=endpoint("traces"),
        metrics=endpoint("metrics"),
        disabled=disabled(),
        sampler=sampler_name(),
        ratio=sampling_ratio(),
        personal_data=redaction.recording(),
    )


def validate() -> None:
    """Resolve the configuration for the refusals rather than the values.

    Called from the settings module, so it runs on every command whether or
    not a collector is configured -- a Django system check would not, because
    nothing runs those in a gunicorn worker.
    """
    configuration()


def sampler(name: str, ratio: float) -> Any:
    """The sampler, built so a caller cannot ask for more than the ratio.

    `ParentBased`'s `remote_parent_sampled` defaults to ALWAYS_ON, which makes
    a bare `ParentBased` record every request arriving with the W3C sampled
    bit set -- and anybody can set that bit, since it is one header on an
    unauthenticated request. With function-level tracing behind it that is a
    way to make this server do arbitrary work, and decision 0021 is where the
    refusal is argued.

    So a remote parent claiming "sampled" is put through the same ratio. That
    keeps a browser-to-backend trace in one piece anyway, because
    `TraceIdRatioBased` decides from the trace id: two ends running the same
    ratio reach the same answer about the same trace without either trusting
    the other. What it does not allow is a caller raising the rate.

    A LOCAL parent is followed, because a span inside a request this server
    has already decided to record belongs to that recording.

    WHAT CAN ASK FOR MORE is a signed, expiring token, checked before Django
    sees the request. `inventory_tng.debugging` is what it is and why it is not
    the sampled bit; here it means one wrapper on top of whichever sampler the
    configuration chose, so a request that proved itself is recorded whole and
    every other request is sampled exactly as it was.

    EXCEPT `always_off`, which is not wrapped. It is documented as the switch
    an operator reaches for when telemetry itself is the problem, and a switch
    that leaves every outstanding token still recording is not off. There is no
    corresponding case for the ratio: `0.0` is a rate, not a refusal, and a
    deployment that wanted none of it says `always_off` or
    `OTEL_SDK_DISABLED`. A token beats a ratio and does not beat an operator.
    """
    from opentelemetry.sdk.trace.sampling import (
        ALWAYS_OFF,
        ALWAYS_ON,
        ParentBased,
        Sampler,
        SamplingResult,
        TraceIdRatioBased,
    )

    from inventory_tng.debugging import debugging

    if name == "always_off":
        return ALWAYS_OFF
    if name == "always_on":
        chosen: Any = ALWAYS_ON
    else:
        ratio_sampler = TraceIdRatioBased(ratio)
        chosen = ParentBased(
            root=ratio_sampler,
            remote_parent_sampled=ratio_sampler,
            remote_parent_not_sampled=ALWAYS_OFF,
        )

    class Debuggable(Sampler):
        """`chosen`, unless this request proved it may be recorded in full.

        `chosen` is kept as an attribute rather than closed over silently: it
        is what the configuration asked for, and a test asserting that a caller
        cannot raise the rate has to be able to see it.
        """

        def __init__(self, wrapping: Any) -> None:
            self.chosen = wrapping

        def should_sample(self, parent_context: Any = None, *arguments: Any, **named: Any) -> SamplingResult:
            asked = ALWAYS_ON if debugging() else self.chosen
            return asked.should_sample(parent_context, *arguments, **named)

        def get_description(self) -> str:
            return f"Debuggable({self.chosen.get_description()})"

    return Debuggable(chosen)


def instrument_django() -> bool:
    """Instrument Django, once, and only once its settings exist.

    Skipped rather than done wherever `start` is called, because the
    instrumentation configures empty settings for the whole process if it
    finds none -- decision 0021 says what that costs. `wsgi.py` is where
    Django has its settings in hand.
    """
    global _instrumented_django
    from django.conf import settings

    if _instrumented_django or not settings.configured:
        return False

    from opentelemetry.instrumentation.django import DjangoInstrumentor

    DjangoInstrumentor().instrument()
    _instrumented_django = True
    return True


def start(django: bool = True) -> bool:
    """Start the SDK if there is a collector, and say whether it did.

    `django=False` is gunicorn's `post_fork` hook saying it knows the
    application has not been imported yet. Said at the call site rather than
    inferred from `settings.configured` three files away, so that the day
    somebody imports a Django setting into the gunicorn configuration -- to
    take the worker count from it, say -- the framework does not quietly get
    instrumented before the fork.

    Everything is imported inside, not at module scope: a checkout with no
    endpoint configured should not pay for importing the SDK, and the import
    is the largest part of that cost.
    """
    global _started

    wanted = configuration()
    if not wanted.wanted:
        return False

    if not _started:
        from opentelemetry.sdk.resources import Resource

        # The marker on the resource as well as on every span, because a metric
        # cannot carry one: `redaction.views` says why adding an attribute to
        # every series is the wrong place for a constant.
        resource = Resource.create({redaction.MARKER: True} if wanted.personal_data else None)

        if wanted.traces:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider = TracerProvider(resource=resource, sampler=sampler(wanted.sampler, wanted.ratio))
            # First, so that a span has been stripped by the time the exporting
            # processor is handed it. Nothing reaches an exporter unredacted.
            provider.add_span_processor(redaction.processor(wanted.personal_data))
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=wanted.traces)))
            trace.set_tracer_provider(provider)

        if wanted.metrics:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            from opentelemetry.metrics import set_meter_provider
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

            reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=wanted.metrics))
            set_meter_provider(
                MeterProvider(
                    resource=resource,
                    metric_readers=[reader],
                    views=redaction.views(wanted.personal_data),
                )
            )

        # The driver is patched at `connect`, so this has to happen before
        # anything opens a connection -- which, after a fork, it has not. What
        # it gives, with Django's instrumentation, is each request's queries as
        # spans beneath the request's own. The deliberate measurements are
        # inventory-tng-nb8.9.
        from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

        PsycopgInstrumentor().instrument(enable_commenter=False)
        _started = True

    if django:
        instrument_django()
    return True
