"""A metric of what writes did, measured and logged only once their
transaction commits, so a write rolled back records nothing
(CONTRIBUTING.md#logging, docs/decisions/0006). It has no add() or
record(): `ty check` refuses a write measured by hand (Ali, 2026-11-27,
inventory-tng-8xs0). Made around the meter's instrument, made inline so
nothing else holds it:

    closed = WriteMetric(log, meter.create_counter("inventory.things.closed"))
    closed.on_commit(session, "thing closed", attributes={"how": how}, thing_id=1)

A counter that also counts refusals, such as a sign-in's outcome, is
declared shared (core/metrics_at_once.py), bound to a private name
(`_sign_ins`), and given to both the WriteMetric and refusals(); one that
also counts what writes nothing, such as a reminder shown, adds that by
hand."""

from collections.abc import Mapping
from typing import Literal, overload

from opentelemetry.metrics import Counter, Histogram
from sqlalchemy.orm import Session
from structlog.typing import FilteringBoundLogger

from inventory_tng.core.db_once_committed import once_committed


class WriteMetric:
    """A counter or histogram of a write, logged on `log`, the writing
    module's, at `level`."""

    def __init__(
        self,
        log: FilteringBoundLogger,
        instrument: Counter | Histogram,
        level: Literal["info", "warning"] = "info",
    ) -> None:
        self._log, self._level = log, level
        self._measure = (
            instrument.add if isinstance(instrument, Counter) else instrument.record
        )

    @overload
    def on_commit(
        self,
        session: Session,
        /,
        *,
        amount: int = 1,
        attributes: Mapping[str, str] | None = None,
    ) -> None: ...

    @overload
    def on_commit(
        self,
        session: Session,
        event: str,
        /,
        *,
        amount: int = 1,
        attributes: Mapping[str, str] | None = None,
        **fields: object,
    ) -> None: ...

    def on_commit(
        self,
        session: Session,
        event: str | None = None,
        /,
        *,
        amount: int = 1,
        attributes: Mapping[str, str] | None = None,
        **fields: object,
    ) -> None:
        """Measure `amount` by `attributes`, and log `event` with `fields`, once
        the session's transaction commits (once_committed). With no event,
        the write's line is another metric's, or logged once for several
        measured, as a batch's."""
        once_committed(session, self._measure, amount, attributes)
        if event is not None:
            once_committed(session, getattr(self._log, self._level), event, **fields)
