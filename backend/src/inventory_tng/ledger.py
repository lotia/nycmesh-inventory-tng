"""Recording stock moving, a whole batch at a time (docs/decisions/0003).

The scan page sends a batch with a key it made; a batch sent again with the
same key, after a dropped connection or from the outbox, is written once.
The tables' rules are in PostgreSQL (migration 0004), for every writer; the
checks here are the ones that can name what is wrong, line by line, so a
volunteer can fix it.

Two choices that keep a volunteer's work from being lost:

- Retired items, locations and volunteers are still accepted. A batch can
  wait in the outbox while an administrator retires or merges something, and
  readers follow a merged volunteer forward.
- The server's clock decides when a batch happened, not the phone's: a batch
  resent from the outbox is recorded when it arrives.
"""

import uuid
from decimal import Decimal
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from opentelemetry import metrics
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Column, Integer, MetaData, Table, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from inventory_tng.db import DbSession, set_actor
from inventory_tng.devices import Refused, RegisteredDevice
from inventory_tng.models import (
    QUANTITY,
    Item,
    LedgerTransaction,
    Location,
    Movement,
    Volunteer,
)

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

router = APIRouter()

# The view migration 0004 creates, in a MetaData of its own so autogenerate
# does not try to make it a table.
balance = Table(
    "balance",
    MetaData(),
    Column("item_id", Integer),
    Column("location_id", Integer),
    Column("quantity", QUANTITY),
)

# What a volunteer records. Counts and adjustments are an administrator's
# (inventory-tng-8i4.14). Whether each kind has a from side and a to side; the
# database's ck_ledger_transaction_sides says the same.
VolunteerKind = Literal["move", "receipt", "install"]
SIDES: dict[str, tuple[bool, bool]] = {
    "move": (True, True),
    "receipt": (False, True),
    "install": (True, False),
}
MAX_LINES = 500

recorded = meter.create_counter(
    "inventory.transactions.recorded",
    unit="{transaction}",
    description="Batches written to the ledger, by kind",
)
lines_per_batch = meter.create_histogram(
    "inventory.transaction.lines",
    unit="{line}",
    description="Lines in each batch written, by kind",
)
replayed = meter.create_counter(
    "inventory.transactions.replayed",
    unit="{transaction}",
    description="Batches sent again with a key already written, by kind",
)
refused = meter.create_counter(
    "inventory.transactions.refused",
    unit="{transaction}",
    # FastAPI's own 422s, for a batch that cannot be at all, are a client's
    # bug and are not counted here.
    description="Batches refused for naming rows that do not exist, or for "
    "reusing a key, by reason",
)

# An id or install number: positive, and fits PostgreSQL's integer.
Number = Annotated[int, Field(gt=0, le=2**31 - 1)]


class Line(BaseModel):
    item_id: Number
    # In the item's unit: the scan page turns packages into units first.
    quantity: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=3)]


class Batch(BaseModel):
    key: uuid.UUID
    kind: VolunteerKind
    volunteer_id: Number
    from_location_id: Number | None = None
    to_location_id: Number | None = None
    install_number: Number | None = None
    lines: Annotated[list[Line], Field(min_length=1, max_length=MAX_LINES)]

    @model_validator(mode="after")
    def _sides(self) -> Batch:
        has_from, has_to = SIDES[self.kind]
        if (self.from_location_id is not None) != has_from or (
            self.to_location_id is not None
        ) != has_to:
            sides = " and ".join(
                side for side, wanted in (("from", has_from), ("to", has_to)) if wanted
            )
            raise ValueError(f"a {self.kind} has only a {sides} location")
        if self.from_location_id == self.to_location_id:
            raise ValueError("from and to must be different locations")
        if self.install_number is not None and self.kind != "install":
            raise ValueError("only an install has an install number")
        return self


# Where in the batch, as pydantic gives it ("lines", 2, "item_id"), and what.
Problem = tuple[tuple[str | int, ...], Literal["not_found", "not_whole"]]
MESSAGES = {
    "not_found": "There is no such row",
    "not_whole": "This item is counted in whole units",
}


class BatchRefused(Exception):
    def __init__(self, problems: list[Problem]) -> None:
        super().__init__(f"{len(problems)} problems")
        self.problems = problems


class KeyReused(Exception):
    """The key was already used, for a different batch."""


def record(
    session: Session, batch: Batch, *, device_id: int | None
) -> tuple[LedgerTransaction, bool]:
    """Write the batch, or find the one already written with its key. True if
    this call wrote it. The caller commits.

    A batch already written is not checked again: the catalogue may have
    changed since, and the volunteer must not be asked to fix what is saved."""
    if (written := _replay(session, batch)) is not None:
        return written, False
    problems = _problems(session, batch)
    if problems:
        refused.add(1, {"reason": "invalid"})
        log.info("batch refused", problems=sorted({kind for _, kind in problems}))
        raise BatchRefused(problems)
    # A concurrent request with the same key waits here for that one to
    # commit, then finds its row.
    transaction = session.scalar(
        pg_insert(LedgerTransaction)
        .values(idempotency_key=batch.key, device_id=device_id, **_header(batch))
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(LedgerTransaction)
    )
    if transaction is None:
        written = _replay(session, batch)
        assert written is not None  # The conflict was on this key.
        return written, False
    session.execute(
        insert(Movement),
        [
            {"transaction_id": transaction.id, **line.model_dump()}
            for line in batch.lines
        ],
    )
    return transaction, True


def _problems(session: Session, batch: Batch) -> list[Problem]:
    problems: list[Problem] = []
    if session.get(Volunteer, batch.volunteer_id) is None:
        problems.append((("volunteer_id",), "not_found"))
    for field in ("from_location_id", "to_location_id"):
        location_id = getattr(batch, field)
        if location_id is not None and session.get(Location, location_id) is None:
            problems.append(((field,), "not_found"))
    units = dict(
        session.execute(
            select(Item.id, Item.unit).where(
                Item.id.in_({line.item_id for line in batch.lines})
            )
        ).all()
    )
    for number, line in enumerate(batch.lines):
        unit = units.get(line.item_id)
        if unit is None:
            problems.append((("lines", number, "item_id"), "not_found"))
        elif unit == "each" and line.quantity != line.quantity.to_integral_value():
            problems.append((("lines", number, "quantity"), "not_whole"))
    return problems


def _header(batch: Batch) -> dict[str, object]:
    """The batch's own columns: everything but its key and lines."""
    return batch.model_dump(exclude={"key", "lines"})


def _replay(session: Session, batch: Batch) -> LedgerTransaction | None:
    """The transaction already written with the batch's key, if any, and
    KeyReused if it is not this batch."""
    transaction = session.scalar(
        select(LedgerTransaction).where(LedgerTransaction.idempotency_key == batch.key)
    )
    if transaction is None:
        return None
    written = sorted(
        session.execute(
            select(Movement.item_id, Movement.quantity).where(
                Movement.transaction_id == transaction.id
            )
        )
    )
    sent = sorted((line.item_id, line.quantity) for line in batch.lines)
    same = written == sent and all(
        getattr(transaction, field) == value for field, value in _header(batch).items()
    )
    if not same:
        refused.add(1, {"reason": "key_reused"})
        log.warning("idempotency key reused", transaction_id=transaction.id)
        raise KeyReused
    replayed.add(1, {"kind": batch.kind})
    log.info("transaction replayed", transaction_id=transaction.id)
    return transaction


class Saved(BaseModel):
    transaction_id: int


PATH = "/transactions"


@router.post(
    PATH,
    status_code=201,
    responses={
        200: {"model": Saved, "description": "Already written with this key"},
        403: {"model": Refused, "description": "This device is not registered"},
        409: {"model": Refused, "description": "The key was used for another batch"},
    },
)
def save_batch(
    batch: Batch, device: RegisteredDevice, session: DbSession, response: Response
) -> Saved:
    """Write a whole batch, or none of it. A 422 names each bad line."""
    # So the history of anything the batch changes, such as on_order, says who.
    set_actor(session, f"volunteer:{batch.volunteer_id}")
    transaction, written = record(session, batch, device_id=device.id)
    # Read before the commit, which expires it.
    transaction_id = transaction.id
    session.commit()
    if not written:
        response.status_code = 200
        return Saved(transaction_id=transaction_id)
    # Counted once committed, so a failed commit and its retry count once.
    recorded.add(1, {"kind": batch.kind})
    lines_per_batch.record(len(batch.lines), {"kind": batch.kind})
    log.info(
        "transaction recorded",
        transaction_id=transaction_id,
        kind=batch.kind,
        lines=len(batch.lines),
        volunteer_id=batch.volunteer_id,
    )
    return Saved(transaction_id=transaction_id)


def install(app: FastAPI) -> None:
    """The responses for a refused batch: a 422 shaped like FastAPI's own, so
    the scan page reads one kind, and a 409 for a reused key."""

    @app.exception_handler(BatchRefused)
    def batch_refused(_request: Request, error: BatchRefused) -> JSONResponse:
        detail = [
            {"type": kind, "loc": ["body", *loc], "msg": MESSAGES[kind]}
            for loc, kind in error.problems
        ]
        return JSONResponse({"detail": detail}, status_code=422)

    @app.exception_handler(KeyReused)
    def key_reused(_request: Request, _error: KeyReused) -> JSONResponse:
        return JSONResponse({"reason": "key_reused"}, status_code=409)
