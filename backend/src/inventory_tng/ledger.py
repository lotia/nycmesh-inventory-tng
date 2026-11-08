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
from collections.abc import Collection, Mapping
from decimal import Decimal
from typing import Annotated, Literal, NamedTuple

import structlog
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from opentelemetry import metrics
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import (
    Column,
    ColumnElement,
    Integer,
    MetaData,
    Select,
    Table,
    any_,
    func,
    insert,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from inventory_tng.db import DbSession, exists_where, get_locked, locked, set_actor
from inventory_tng.devices import DeviceRefusedReply, RegisteredDevice
from inventory_tng.models import (
    INTEGER_MAX,
    QUANTITY,
    STORE_KINDS,
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


# Less than nothing at a place: the records are wrong there until it is
# counted (docs/decisions/0003).
SHORT = balance.c.quantity < 0


def in_stores(items: Select[int]) -> Select[int, Decimal, bool]:
    """Each of `items`' stock across the current stores and their areas,
    and whether any of it is SHORT there. The ids go to the view as arrays,
    as in retired_in_stock, so only those balances are summed."""
    places = func.array(
        select(Location.id)
        .where(Location.kind.in_(STORE_KINDS), Location.retired_at.is_(None))
        .scalar_subquery()
    )
    return (
        select(
            balance.c.item_id,
            func.sum(balance.c.quantity).label("total"),
            func.bool_or(SHORT).label("short"),
        )
        .where(
            balance.c.item_id == any_(func.array(items.scalar_subquery())),
            balance.c.location_id == any_(places),
        )
        .group_by(balance.c.item_id)
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


def holds_stock(session: Session, location_id: int) -> bool:
    """Whether the records say anything is at a place, of any item, more or
    less than nothing."""
    return exists_where(
        session, balance.c.location_id == location_id, balance.c.quantity != 0
    )


def takeable() -> ColumnElement[bool]:
    """Items volunteers may still pick: current ones, and retired ones with
    some left (retired_in_stock). What the scan page's catalogue holds."""
    return or_(Item.retired_at.is_(None), Item.id.in_(retired_in_stock()))


def whole_enough(unit: str, amount: Decimal) -> bool:
    """An item counted in each moves in whole units (migration 0004's
    movement_whole_units, said where a person can fix it)."""
    return unit != "each" or amount == amount.to_integral_value()


def on_order_after(on_order: Decimal, received: Decimal) -> Decimal:
    """An item's on_order once `received` of it arrives: down by that much,
    never below zero."""
    return max(on_order - received, Decimal(0))


def expected(
    session: Session,
    location_id: int,
    *,
    as_of: int | None = None,
    items: Collection[int] | None = None,
) -> dict[int, Decimal]:
    """What the records say is at a place, by item: only what is not zero,
    as the balance view says now; with as_of, what they said at that moment
    (see moment()), of `items` only if given."""
    if as_of is not None:
        (flows,) = _flows(
            session, location_id, LedgerTransaction.id <= as_of, items=items
        )
        return {
            item: came - went for item, (came, went) in flows.items() if came != went
        }
    query = select(balance.c.item_id, balance.c.quantity).where(
        balance.c.location_id == location_id, balance.c.quantity != 0
    )
    return {item_id: amount for item_id, amount in session.execute(query)}


def lock_place(session: Session, location_id: int) -> None:
    """Wait for every save under way at a place, and hold up new ones until
    this transaction ends, so its ledger reads as one moment: every save
    takes FOR KEY SHARE on its places before it draws an id
    (_hold_places), and this takes FOR UPDATE."""
    get_locked(session, Location, location_id)


def moment(session: Session, location_id: int) -> int:
    """Now, at a place, as a count form shows it: its latest ledger
    transaction, with no save there under way, so none can commit later
    with a lower id. The lock is held only for the read."""
    with session.begin_nested() as reading:
        lock_place(session, location_id)
        latest = session.scalar(
            select(func.max(LedgerTransaction.id)).where(_at(location_id))
        )
        # Rolling back to the savepoint releases the lock.
        reading.rollback()
    return latest or 0


def _at(location_id: int) -> ColumnElement[bool]:
    """A ledger transaction into or out of a place."""
    return or_(
        LedgerTransaction.to_location_id == location_id,
        LedgerTransaction.from_location_id == location_id,
    )


def _flows(
    session: Session,
    location_id: int,
    window: ColumnElement[bool],
    *more: ColumnElement[bool],
    items: Collection[int] | None = None,
) -> list[dict[int, tuple[Decimal, Decimal]]]:
    """For each window (which ledger transactions it takes), what came into
    a place and what went out of it, by item: each item that moved there in
    that window, of `items` only, if given. One query, however many."""
    into = LedgerTransaction.to_location_id == location_id
    out = LedgerTransaction.from_location_id == location_id
    windows = (window, *more)
    where = [_at(location_id), or_(*windows)]
    if items is not None:
        where.append(Movement.item_id.in_(items))
    rows = session.execute(
        select(
            Movement.item_id,
            *(
                func.sum(Movement.quantity).filter(each, side)
                for each in windows
                for side in (into, out)
            ),
        )
        .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
        .where(*where)
        .group_by(Movement.item_id)
    )
    flows: list[dict[int, tuple[Decimal, Decimal]]] = [{} for _ in windows]
    for item_id, *sums in rows:
        # A sum over no movements is NULL: the item did not move in that one.
        for flow, came, went in zip(flows, sums[::2], sums[1::2], strict=True):
            if came is not None or went is not None:
                flow[item_id] = (came or Decimal(0), went or Decimal(0))
    return flows


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
Number = Annotated[int, Field(gt=0, le=INTEGER_MAX)]


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
    header = {"device_id": device_id, **_header(batch)}
    problems = _problems(session, batch, _hold_places(session, header))
    if problems:
        refused.add(1, {"reason": "invalid"})
        log.info("batch refused", problems=sorted({kind for _, kind in problems}))
        raise BatchRefused(problems)
    transaction = insert_transaction(
        session,
        batch.key,
        header,
        {line.item_id: line.quantity for line in batch.lines},
    )
    if transaction is None:
        written = _replay(session, batch)
        assert written is not None  # The conflict was on this key.
        return written, False
    return transaction, True


def _hold_places(session: Session, header: Mapping[str, object]) -> set[int]:
    """A save's places, held FOR KEY SHARE until it ends, before it has an
    id: a count (lock_place) then waits for it whole, so none can draw an
    id, wait, and commit after a count's moment that it falls inside. The
    ids of those that exist."""
    return set(
        session.scalars(
            locked(
                select(Location.id).where(
                    Location.id.in_(
                        {header.get("from_location_id"), header.get("to_location_id")}
                        - {None}
                    )
                ),
                key_share=True,
                shared=True,
            )
        )
    )


def insert_transaction(
    session: Session,
    key: uuid.UUID,
    header: Mapping[str, object],
    lines: Mapping[int, Decimal],
) -> LedgerTransaction | None:
    """A transaction and its movements, unless its key is written already:
    None then. A concurrent request with the same key waits here for that
    one to commit, then writes nothing. Its places are held first
    (_hold_places), even when the caller holds them already: one more
    indexed query, which keeps every caller safe. A receipt lowers what is
    on order of each item it brings (on_order_after)."""
    _hold_places(session, header)
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
        if transaction.kind == "receipt":
            _receive(session, lines)
    return transaction


def _receive(session: Session, lines: Mapping[int, Decimal]) -> None:
    """Through the ORM, so it is history. The items locked by id, so two
    receipts of the same items take them in one order."""
    items = session.scalars(
        locked(
            select(Item).where(Item.id.in_(lines), Item.on_order > 0).order_by(Item.id),
            key_share=True,
        )
    )
    for item in items:
        assert item.on_order is not None  # The query asked for more than 0.
        item.on_order = on_order_after(item.on_order, lines[item.id])


def _problems(session: Session, batch: Batch, places: set[int]) -> list[Problem]:
    """What is wrong with a batch, whose `places` are those of its places
    that exist."""
    problems: list[Problem] = []
    if session.get(Volunteer, batch.volunteer_id) is None:
        problems.append((("volunteer_id",), "not_found"))
    for field in ("from_location_id", "to_location_id"):
        location_id = getattr(batch, field)
        if location_id is not None and location_id not in places:
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
withheld = meter.create_counter(
    "inventory.count.lines.withheld",
    unit="{line}",
    description="Count lines not written because the item moved at the place "
    "while it was counted, so it is counted again",
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


class Counted(NamedTuple):
    differed: bool
    # Items counted that moved at the place since the form was shown, with
    # what came in and what went out since. Not written: count them again.
    moved: dict[int, tuple[Decimal, Decimal]]


def record_count(
    session: Session,
    key: uuid.UUID,
    admin_id: int,
    location_id: int,
    counted_at: Mapping[int, Decimal],
    as_of: int,
) -> Counted:
    """Put the records right for what was counted at a place, as they stood
    when the form was shown: as of `as_of`, the place's latest ledger
    transaction then, from moment() (docs/decisions/0003). Items not
    counted are left as they are.

    An item that moved there since may have been counted before the move or
    after it, so its line is not written: it comes back to count again. The
    rest are written against the records as of `as_of`, so a move saved
    meanwhile still applies on top.

    Found more, or nothing differed, is a count into the place under the key;
    found less, a count out of it under a key made from it, so sending it
    twice writes it once, and finds what moved as the first send did. A
    count whose every line moved writes nothing."""
    lock_place(session, location_id)
    lost_key = uuid.uuid5(key, "lost")
    sent = session.scalar(
        select(func.min(LedgerTransaction.id)).where(
            LedgerTransaction.idempotency_key.in_((key, lost_key))
        )
    )
    after = LedgerTransaction.id > as_of
    if sent is not None:
        after &= LedgerTransaction.id < sent
    moved, flows = _flows(
        session, location_id, after, LedgerTransaction.id <= as_of, items=counted_at
    )
    then = {item_id: came - went for item_id, (came, went) in flows.items()}
    kept = counted_at.keys() - moved.keys()
    found: dict[int, Decimal] = {}
    lost: dict[int, Decimal] = {}
    for item_id in kept:
        difference = counted_at[item_id] - then.get(item_id, Decimal(0))
        if difference > 0:
            found[item_id] = difference
        elif difference < 0:
            lost[item_id] = -difference
    if moved and sent is None:
        withheld.add(len(moved))
        log.info(
            "count lines withheld",
            location_id=location_id,
            admin_id=admin_id,
            lines=len(moved),
        )
    # A count of nothing at all still says when the place was counted.
    if found or ((kept or not counted_at) and not lost):
        header = {"admin_id": admin_id, "to_location_id": location_id}
        _correct(session, key, "count", header, found)
    if lost:
        header = {"admin_id": admin_id, "from_location_id": location_id}
        _correct(session, lost_key, "count", header, lost)
    return Counted(bool(found or lost), moved)


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
