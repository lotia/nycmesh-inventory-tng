"""Metrics and logs over OTLP, and traces when asked for (docs/decisions/0006).

Started by the server entry point in each process, never by `create_app()`
(docs/decisions/0007). Nothing starts unless an OTLP endpoint is set; the
settings that apply are in CONTRIBUTING.md, Logging.
"""

import copy
import logging
import os
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import TracebackType
from typing import Self

import structlog
from fastapi import FastAPI
from opentelemetry import metrics
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.dependencies import get_dependency_conflicts
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, LogRecordExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import DEFAULT_OFF
from opentelemetry.util.types import AnyValue
from sqlalchemy import Engine

from inventory_tng.logs import ADDED_FIELDS

SERVICE_NAME = "inventory-tng"


# Fields OpenTelemetry records in fields of its own: the body, the exception,
# and what logs.SHARED adds (level, logger, timestamp, process; the resource
# tells processes apart).
NOT_ATTRIBUTES = {"event", "exc_info", *ADDED_FIELDS}
# The exporter's own loggers. Exporting their lines would make each export
# produce more to export.
EXPORTER_LOGGERS = ("opentelemetry", "urllib3", "requests")

ExcInfo = tuple[type[BaseException], BaseException, TracebackType | None]


SIGNALS = {"METRICS": "otlp", "LOGS": "otlp", "TRACES": "none"}


def enabled(environ: Mapping[str, str] = os.environ) -> bool:
    """Whether any signal would start: the same rule create() applies."""
    disabled = environ.get("OTEL_SDK_DISABLED", "").strip().lower() == "true"
    return not disabled and any(
        _wanted(environ, signal, default=default) for signal, default in SIGNALS.items()
    )


def _wanted(environ: Mapping[str, str], signal: str, *, default: str) -> bool:
    """OTEL_{signal}_EXPORTER names otlp ("otlp", "none" or a list such as
    "otlp,console"), and the signal has an endpoint: its own, or the shared
    OTEL_EXPORTER_OTLP_ENDPOINT."""
    names = environ.get(f"OTEL_{signal}_EXPORTER", default).split(",")
    endpoint = environ.get(f"OTEL_EXPORTER_OTLP_{signal}_ENDPOINT") or environ.get(
        "OTEL_EXPORTER_OTLP_ENDPOINT"
    )
    return "otlp" in {name.strip().lower() for name in names} and bool(endpoint)


@dataclass
class Telemetry:
    meter_provider: MeterProvider
    logger_provider: LoggerProvider | None = None
    tracer_provider: TracerProvider | None = None
    _handler: logging.Handler | None = field(default=None, init=False)

    @classmethod
    def create(
        cls,
        environ: Mapping[str, str] = os.environ,
        *,
        metric_reader: MetricReader | None = None,
        log_exporter: LogRecordExporter | None = None,
    ) -> Self:
        """Providers exporting over OTLP, or to the reader and exporter given
        (the tests pass in-memory ones). A signal starts only if it has an
        endpoint, its own or the shared one. The server flushes them when the
        app stops (server.flush_when_stopped)."""
        resource = Resource.create()
        name = resource.attributes.get("service.name")
        if not isinstance(name, str) or name.startswith("unknown_service"):
            # Ours only when OTEL_SERVICE_NAME and OTEL_RESOURCE_ATTRIBUTES
            # name none. The SDK gives each process its own instance id.
            resource = resource.merge(Resource({"service.name": SERVICE_NAME}))

        readers: list[MetricReader] = []
        if metric_reader:
            readers.append(metric_reader)
        elif _wanted(environ, "METRICS", default=SIGNALS["METRICS"]):
            readers.append(PeriodicExportingMetricReader(OTLPMetricExporter()))

        logger_provider = None
        if log_exporter or _wanted(environ, "LOGS", default=SIGNALS["LOGS"]):
            logger_provider = LoggerProvider(resource=resource)
            logger_provider.add_log_record_processor(
                BatchLogRecordProcessor(log_exporter or OTLPLogExporter())
            )

        tracer_provider = None
        if _wanted(environ, "TRACES", default=SIGNALS["TRACES"]):
            # Trace only what the ingress chose to trace, unless a sampler is
            # set in OTEL_TRACES_SAMPLER.
            sampler = None if environ.get("OTEL_TRACES_SAMPLER") else DEFAULT_OFF
            tracer_provider = TracerProvider(resource=resource, sampler=sampler)
            tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))

        return cls(
            meter_provider=MeterProvider(resource=resource, metric_readers=readers),
            logger_provider=logger_provider,
            tracer_provider=tracer_provider,
        )

    def instrument(self, app: FastAPI, engine: Engine) -> None:
        """Requests, queries and the connection pool. Call make_global()
        first: psycopg's instrumentation records to the global provider."""
        _opt_in_to_stable_names()
        if conflict := get_dependency_conflicts(
            FastAPIInstrumentor().instrumentation_dependencies()
        ):
            raise RuntimeError(f"FastAPI instrumentation cannot run: {conflict}")
        FastAPIInstrumentor.instrument_app(
            app,
            meter_provider=self.meter_provider,
            tracer_provider=self.tracer_provider,
            exclude_spans=["receive", "send"],
        )
        PsycopgInstrumentor().instrument(
            tracer_provider=self.tracer_provider,
            raise_exception_on_conflict=True,
        )
        observe_pool(self.meter_provider, engine)

    def attach_logs(self) -> None:
        """Send every log line, ours and the libraries', over OTLP as well."""
        if self.logger_provider:
            self._handler = StructlogBridge(logger_provider=self.logger_provider)
            logging.getLogger().addHandler(self._handler)

    def make_global(self) -> None:
        """So that `metrics.get_meter()` in our own modules records here."""
        metrics.set_meter_provider(self.meter_provider)
        if self.logger_provider:
            set_logger_provider(self.logger_provider)

    def shutdown(self) -> None:
        """Undo attach_logs() and instrument(), flush, and stop. Called when
        the app stops, and by tests."""
        if self._handler:
            logging.getLogger().removeHandler(self._handler)
        PsycopgInstrumentor().uninstrument()
        for provider in (
            self.meter_provider,
            self.logger_provider,
            self.tracer_provider,
        ):
            if provider:
                provider.shutdown()


def _opt_in_to_stable_names() -> None:
    # The stable metric names (http.server.request.duration,
    # db.client.operation.duration) need both, whatever else the operator set.
    wanted = {"http", "database"}
    given = {
        p.strip()
        for p in os.environ.get("OTEL_SEMCONV_STABILITY_OPT_IN", "").split(",")
    }
    os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] = ",".join(
        sorted((given | wanted) - {""})
    )


def observe_pool(provider: MeterProvider, engine: Engine) -> None:
    # TODO(inventory-tng-8i4.34): remove once opentelemetry-instrumentation-sqlalchemy
    # supports SQLAlchemy 2.1.
    def connections(_options: CallbackOptions) -> Iterable[Observation]:
        pool = engine.pool  # Read each time: dispose() replaces it.
        yield Observation(
            getattr(pool, "checkedout", lambda: 0)(),
            {"db.client.connection.state": "used"},
        )
        yield Observation(
            getattr(pool, "checkedin", lambda: 0)(),
            {"db.client.connection.state": "idle"},
        )

    provider.get_meter(__name__).create_observable_gauge(
        "db.client.connection.count",
        callbacks=[connections],
        unit="{connection}",
        description="Connections in the pool, by state",
    )


class StructlogBridge(LoggingHandler):
    """OpenTelemetry's handler, taught structlog's records.

    A structlog record's message is a dict of fields: the event becomes the
    body and the rest become attributes. Fields are never copied onto the
    record, where a name like `args` or `msg` would break it. The request id
    and other context variables reach the standard library's records too.
    Like any handler, it never raises into the code that logged.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith(EXPORTER_LOGGERS) and bool(
            super().filter(record)
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(self._prepared(record))
        except Exception:  # noqa: BLE001 - handlers swallow their own errors, as the stdlib's do.
            self.handleError(record)

    def _prepared(self, record: logging.LogRecord) -> logging.LogRecord:
        record = copy.copy(record)  # The stdout handler reads the same record.
        message: object = record.msg  # A str by its type, a dict from structlog.
        if isinstance(message, dict):
            record.msg, record.args = str(message.get("event", "")), ()
            if exc_info := _exc_info(message.get("exc_info")):
                record.exc_info = exc_info
            fields = message
        else:
            fields = structlog.contextvars.get_contextvars()
        record.__dict__["_fields"] = {
            str(key): value
            for key, value in fields.items()
            if key not in NOT_ATTRIBUTES
        }
        return record

    def _get_attributes(
        self, record: logging.LogRecord
    ) -> tuple[dict[str, AnyValue], str | None]:
        attributes, event_name = super()._get_attributes(record)
        fields = attributes.pop("_fields", None)
        # Plumbing, not information: structlog's private extras, and uvicorn's
        # copy of its message with terminal colours.
        for name in [name for name in attributes if name.startswith("_")]:
            del attributes[name]
        attributes.pop("color_message", None)
        if isinstance(fields, dict):
            attributes.update({key: _attribute(value) for key, value in fields.items()})
        return attributes, event_name


def _exc_info(value: object) -> ExcInfo | None:
    """The forms structlog accepts: True, an exception, or a sys.exc_info() tuple."""
    if value is True:
        value = sys.exc_info()[1]
    elif isinstance(value, tuple) and len(value) == 3:
        value = value[1]
    if isinstance(value, BaseException):
        return type(value), value, value.__traceback__
    return None


def _attribute(value: object) -> AnyValue:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)
