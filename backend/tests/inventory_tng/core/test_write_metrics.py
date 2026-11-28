import structlog
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    HistogramDataPoint,
    InMemoryMetricReader,
    NumberDataPoint,
)
from sqlalchemy import text
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng.core.write_metrics import WriteMetric

log = structlog.get_logger(__name__)


def measured(reader: InMemoryMetricReader) -> dict[str, list[tuple[object, dict]]]:
    """Each metric's points: a counter's sum, a histogram's values' sum."""
    data = reader.get_metrics_data()
    found: dict[str, list[tuple[object, dict]]] = {}
    for resource in data.resource_metrics if data else []:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                for point in metric.data.data_points:
                    assert isinstance(point, NumberDataPoint | HistogramDataPoint)
                    value = (
                        point.value if isinstance(point, NumberDataPoint) else point.sum
                    )
                    found.setdefault(metric.name, []).append(
                        (value, dict(point.attributes or {}))
                    )
    return found


def test_measures_and_logs_a_write_once_committed(session: Session) -> None:
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter(__name__)
    saved = WriteMetric(log, meter.create_counter("saved"))
    took = WriteMetric(log, meter.create_histogram("took"), level="warning")
    session.execute(text("SELECT 1"))
    with capture_logs() as logs:
        saved.on_commit(session, "thing saved", attributes={"how": "one"}, thing_id=4)
        took.on_commit(session, amount=3)
        took.on_commit(session, "thing took", amount=2)
        assert (logs, measured(reader)) == ([], {})
        session.commit()
    assert measured(reader) == {"saved": [(1, {"how": "one"})], "took": [(5, {})]}
    assert logs == [
        {"event": "thing saved", "thing_id": 4, "log_level": "info"},
        {"event": "thing took", "log_level": "warning"},
    ]


def test_a_write_rolled_back_is_neither_measured_nor_logged(session: Session) -> None:
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter(__name__)
    saved = WriteMetric(log, meter.create_counter("saved"))
    session.execute(text("SELECT 1"))
    with capture_logs() as logs:
        saved.on_commit(session, "thing saved", thing_id=4)
        session.rollback()
        session.execute(text("SELECT 1"))
        session.commit()
    assert (logs, measured(reader)) == ([], {})
