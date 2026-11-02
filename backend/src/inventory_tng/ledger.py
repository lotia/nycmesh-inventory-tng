"""Recording stock moving, a whole batch at a time (docs/decisions/0003).

The scan page sends a batch with a key it made; a batch sent again with the
same key, after a dropped connection or from the outbox, is written once.
The tables' rules are in PostgreSQL (migration 0004), for every writer; the
checks here are the ones that can name what is wrong, line by line, so a
volunteer can fix it.

Two choices that keep a volunteer's work from being lost:

- Retired items, locations and volunteers are still accepted. A batch can
  wait in the outbox while an administrator retires or merges something, and
  readers follow a merged volunteer forward. The one exception: a retired
  item is never received (docs/decisions/0003), so a receipt of one is
  refused, and the outbox says so.
- The server's clock decides when a batch happened, not the phone's: a batch
  resent from the outbox is recorded when it arrives.
"""

import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from opentelemetry import metrics
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    Select,
    Table,
    any_,
    func,
    insert,
    select,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from inventory_tng.db import DbSession, set_actor
from inventory_tng.devices import DeviceRefusedReply, RegisteredDevice
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


def retired_in_stock() -> Select[tuple[int]]:
    """The ids of retired items with some left at a current place: what
    volunteers may still take, and administrators write off. The retired ids
    go to the view as an array, which PostgreSQL pushes down to the movement
    index, so only their balances are summed, however long the ledger."""
    retired = func.array(
        select(Item.id).where(Item.retired_at.is_not(None)).scalar_subquery()
    )
    return (
        select(balance.c.item_id)
        .join(Location, Location.id == balance.c.location_id)
        .where(
            Location.retired_at.is_(None),
            balance.c.quantity > 0,
            balance.c.item_id == any_(retired),
        )
        .distinct()
    )


def whole_enough(unit: str, amount: Decimal) -> bool:
    """An item counted in each moves in whole units (migration 0004's
    movement_whole_units, said where a person can fix it)."""
    return unit != "each" or amount == amount.to_integral_value()


def expected(session: Session, location_id: int) -> dict[int, Decimal]:
    """What the records say is at a place, by item: only what is not zero."""
    return {
        item_id: amount
        for item_id, amount in session.execute(
            select(balance.c.item_id, balance.c.quantity).where(
                balance.c.location_id == location_id, balance.c.quantity != 0
            )
        )
    }


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
Problem = tuple[tuple[str | int, ...], Literal["not_found", "not_whole", "retired"]]
MESSAGES = {
    "not_found": "There is no such row",
    "not_whole": "This item is counted in whole units",
    "retired": "This item is retired: what is left may be taken, never received",
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
    transaction = insert_transaction(
        session,
        batch.key,
        {"device_id": device_id, **_header(batch)},
        {line.item_id: line.quantity for line in batch.lines},
    )
    if transaction is None:
        written = _replay(session, batch)
        assert written is not None  # The conflict was on this key.
        return written, False
    return transaction, True


def insert_transaction(
    session: Session,
    key: uuid.UUID,
    header: Mapping[str, object],
    lines: Mapping[int, Decimal],
) -> LedgerTransaction | None:
    """A transaction and its movements, unless its key is written already:
    None then. A concurrent request with the same key waits here for that
    one to commit, then writes nothing."""
    transaction = session.scalar(
        pg_insert(LedgerTransaction)
        .values(idempotency_key=key, **header)
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(LedgerTransaction)
    )
    if transaction is not None and lines:
        session.execute(
            insert(Movement),
            [
                {
                    "transaction_id": transaction.id,
                    "item_id": item_id,
                    "quantity": amount,
                }
                for item_id, amount in lines.items()
            ],
        )
    return transaction


def _problems(session: Session, batch: Batch) -> list[Problem]:
    problems: list[Problem] = []
    if session.get(Volunteer, batch.volunteer_id) is None:
        problems.append((("volunteer_id",), "not_found"))
    for field in ("from_location_id", "to_location_id"):
        location_id = getattr(batch, field)
        if location_id is not None and session.get(Location, location_id) is None:
            problems.append(((field,), "not_found"))
    items = {
        item.id: item
        for item in session.scalars(
            select(Item).where(Item.id.in_({line.item_id for line in batch.lines}))
        )
    }
    for number, line in enumerate(batch.lines):
        item = items.get(line.item_id)
        if item is None:
            problems.append((("lines", number, "item_id"), "not_found"))
            continue
        if not whole_enough(item.unit, line.quantity):
            problems.append((("lines", number, "quantity"), "not_whole"))
        # Retired: what is left goes, nothing more comes in.
        if item.retired_at is not None and batch.kind == "receipt":
            problems.append((("lines", number, "item_id"), "retired"))
    return problems


# Counts and adjustments: an administrator's, from admin/counts.py

counted = meter.create_counter(
    "inventory.counts.recorded",
    unit="{transaction}",
    description="Counts and adjustments written, by kind",
)


def _correct(
    session: Session,
    key: uuid.UUID,
    kind: Literal["count", "adjustment"],
    header: Mapping[str, object],
    lines: Mapping[int, Decimal],
) -> None:
    transaction = insert_transaction(session, key, {"kind": kind, **header}, lines)
    if transaction is None:
        return
    counted.add(1, {"kind": kind})
    log.info(
        f"{kind} recorded",
        ledger_transaction_id=transaction.id,
        admin_id=header["admin_id"],
        lines=len(lines),
    )


def record_count(
    session: Session,
    key: uuid.UUID,
    admin_id: int,
    location_id: int,
    counted_at: Mapping[int, Decimal],
    was: Mapping[int, Decimal],
) -> bool:
    """Put the records right for what was counted at a place, given what
    they said (`was`, from expected()): True if anything differed. Items not
    counted are left as they are. Found more, or nothing differed, is a count
    into the place under the key; found less, a count out of it under a key
    made from it, so sending it twice writes it once."""
    found: dict[int, Decimal] = {}
    lost: dict[int, Decimal] = {}
    for item_id, amount in counted_at.items():
        difference = amount - was.get(item_id, Decimal(0))
        if difference > 0:
            found[item_id] = difference
        elif difference < 0:
            lost[item_id] = -difference
    if found or not lost:
        header = {"admin_id": admin_id, "to_location_id": location_id}
        _correct(session, key, "count", header, found)
    if lost:
        header = {"admin_id": admin_id, "from_location_id": location_id}
        _correct(session, uuid.uuid5(key, "lost"), "count", header, lost)
    return bool(found or lost)


def record_adjustment(
    session: Session,
    key: uuid.UUID,
    admin_id: int,
    location_id: int,
    item_id: int,
    amount: Decimal,
    *,
    found: bool,
    reason_id: int,
) -> None:
    """Stock found at a place, or written off from it, for a reason."""
    side = "to_location_id" if found else "from_location_id"
    header = {"admin_id": admin_id, "reason_id": reason_id, side: location_id}
    _correct(session, key, "adjustment", header, {item_id: amount})


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


class KeyReusedReply(BaseModel):
    reason: Literal["key_reused"]


PATH = "/transactions"


@router.post(
    PATH,
    status_code=201,
    responses={
        200: {"model": Saved, "description": "Already written with this key"},
        403: {
            "model": DeviceRefusedReply,
            "description": "This device is not registered",
        },
        409: {
            "model": KeyReusedReply,
            "description": "The key was used for another batch",
        },
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
        body = KeyReusedReply(reason="key_reused")
        return JSONResponse(body.model_dump(), status_code=409)
