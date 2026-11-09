"""Recording stock moving, a whole batch at a time (docs/decisions/0003).

The scan page sends a batch with a key it made; a batch sent again with the
same key, after a dropped connection or from the outbox, is written once.
insert_transaction is the one function that writes the ledger: saves,
counts and adjustments all reach it, and it refuses what breaks the
ledger's rules (db.RuleBroken), as hold() finds them. A batch's are said
line by line, so a volunteer is told which line to fix.
The app's login may only read and add to the ledger (DEPLOYMENT.md,
Database roles), and nothing here adds movements to a transaction already
written, so a transaction never changes once written.

Two choices that keep a volunteer's work from being lost:

- Retired items, locations and volunteers are still accepted. A batch can
  wait in the outbox while an administrator retires or merges something, and
  readers follow a merged volunteer forward. The one exception: a retired
  item is never received (docs/decisions/0003), so a receipt of one is
  refused, and the outbox says so. A save at a retired place is logged and
  counted, and the place shows as holding stock (retired_holding_stock)
  until an administrator counts it.
- The server's clock decides when a batch happened, not the phone's: a batch
  resent from the outbox is recorded when it arrives. created_at is the
  database's now(), and nothing sent sets it.
"""

import uuid
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Annotated, Literal, NamedTuple

import structlog
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from opentelemetry import metrics
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import (
    ColumnElement,
    Select,
    Subquery,
    any_,
    func,
    insert,
    or_,
    select,
    union_all,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from inventory_tng.db import (
    DbSession,
    by_name,
    enforce,
    exists_where,
    get_locked,
    locked,
    set_actor,
)
from inventory_tng.devices import DeviceRefusedReply, RegisteredDevice
from inventory_tng.models import (
    INTEGER_MAX,
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


def _balance() -> Subquery:
    """In minus out, for every item and place stock has been at: a place
    whose stock all left keeps its row, at zero. Each side of a transaction
    is a select of its own, joined by UNION ALL, so a filter on a place or
    an item reaches the indexes on each side, instead of summing the whole
    ledger first."""
    moved = select(Movement.item_id).join(
        LedgerTransaction, LedgerTransaction.id == Movement.transaction_id
    )
    into = moved.add_columns(
        LedgerTransaction.to_location_id.label("location_id"),
        Movement.quantity.label("quantity"),
    ).where(LedgerTransaction.to_location_id.is_not(None))
    out = moved.add_columns(
        LedgerTransaction.from_location_id, -Movement.quantity
    ).where(LedgerTransaction.from_location_id.is_not(None))
    side = union_all(into, out).subquery("side")
    return (
        select(
            side.c.item_id,
            side.c.location_id,
            func.sum(side.c.quantity).label("quantity"),
        )
        .group_by(side.c.item_id, side.c.location_id)
        .subquery("balance")
    )


# Read as a table: select(balance.c.quantity).where(balance.c.item_id == ...).
balance = _balance()


# Less than nothing at a place: the records are wrong there until it is
# counted (docs/decisions/0003).
SHORT = balance.c.quantity < 0


def in_stores(items: Select[int]) -> Select[int, Decimal, bool]:
    """Each of `items`' stock across the current stores and their areas,
    and whether any of it is SHORT there. The ids go to balance as arrays,
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


def _retired(model: type[Item | Location]) -> ColumnElement[list[int]]:
    """The ids of a model's retired rows, as an array for balance to take:
    PostgreSQL pushes it down to the ledger's indexes, so only their
    balances are summed, however long the ledger."""
    return func.array(
        select(model.id).where(model.retired_at.is_not(None)).scalar_subquery()
    )


def retired_in_stock() -> Select[tuple[int]]:
    """The ids of retired items with some left at a current place: what
    volunteers may still take, and administrators write off."""
    return (
        select(balance.c.item_id)
        .join(Location, Location.id == balance.c.location_id)
        .where(
            Location.retired_at.is_(None),
            balance.c.quantity > 0,
            balance.c.item_id == any_(_retired(Item)),
        )
        .distinct()
    )


def retired_holding_stock() -> Select[int]:
    """The ids of retired places the records say hold something, more or
    less than nothing: what a save brought or took after the place was
    retired, for an administrator to count."""
    return (
        select(balance.c.location_id)
        .where(
            balance.c.location_id == any_(_retired(Location)),
            balance.c.quantity != 0,
        )
        .distinct()
    )


def retired_places_holding_stock(session: Session) -> list[Location]:
    """retired_holding_stock's places, by name as people sort them."""
    return by_name(session, Location, Location.id.in_(retired_holding_stock()))


def retired_holding_stock_count(session: Session) -> int:
    """How many retired places hold stock (retired_holding_stock), counted
    without loading them, as the navigation's badge on every page needs."""
    places = retired_holding_stock().subquery()
    return session.scalar(select(func.count()).select_from(places)) or 0


def countable() -> ColumnElement[bool]:
    """Places an administrator may count or adjust: current ones, and
    retired ones that hold stock (retired_holding_stock), to put right."""
    return or_(Location.retired_at.is_(None), Location.id.in_(retired_holding_stock()))


def _require_countable(session: Session, location_id: int) -> None:
    """RuleBroken unless the place may be counted or adjusted, read with
    it locked (lock_place): a retire waits for that lock, and it for a
    retire, so a place retired since the page checked is refused."""
    allowed = exists_where(session, Location.id == location_id, countable())
    enforce("location_countable", when=not allowed)


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
    """An item counted in each moves in whole units."""
    return unit != "each" or amount == amount.to_integral_value()


def line_breaks(kind: str, item: Item, amount: Decimal) -> list[str]:
    """The ledger's rules a line of a transaction of this kind breaks,
    moving `amount` of `item`: whole units for an item counted in each, and
    a retired item never received (docs/decisions/0003). Empty if it
    breaks none."""
    broken = []
    if not whole_enough(item.unit, amount):
        broken.append("movement_whole_units")
    if item.retired_at is not None and kind == "receipt":
        broken.append("receipt_retired_item")
    return broken


def lines_break(
    kind: str, items: Mapping[int, Item], lines: Iterable[tuple[int, Decimal]]
) -> list[list[str] | None]:
    """For each line, of an item id and an amount, the rules it breaks
    (line_breaks), or None if its item is not in `items`."""
    return [
        None
        if (item := items.get(item_id)) is None
        else line_breaks(kind, item, amount)
        for item_id, amount in lines
    ]


# A transaction's lines, each an item id and an amount, an item on one line
# only (repeated), as its movements' unique index keeps them.
Lines = Sequence[tuple[int, Decimal]]


def repeated(item_ids: Sequence[int | None]) -> set[int]:
    """The positions of the lines whose item another line names too (None
    names no item, so repeats none). A batch or a count names each item on
    one line: the ledger refuses a second (movement_item_once), so a page
    or the scan API says which lines, before it would."""
    times = Counter(item_ids)
    return {
        number
        for number, item_id in enumerate(item_ids)
        if item_id is not None and times[item_id] > 1
    }


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
    as balance says now; with as_of, what they said at that moment
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
    (_hold_places), and this takes FOR UPDATE, as a retire of the place
    does (locations.retire), so the two go one at a time."""
    get_locked(session, Location, location_id)


def moment(session: Session, location_id: int) -> int:
    """Now, at a place, as a count form shows it: its latest ledger
    transaction, with no save there under way, so none can commit later
    with a lower id. The lock is held only for the read."""
    with session.begin_nested() as reading:
        lock_place(session, location_id)
        latest = session.scalar(
            select(func.max(LedgerTransaction.id)).where(_at_place(location_id))
        )
        # Rolling back to the savepoint releases the lock.
        reading.rollback()
    return latest or 0


def _at_place(location_id: int) -> ColumnElement[bool]:
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
    where = [_at_place(location_id), or_(*windows)]
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
at_retired_place = meter.create_counter(
    "inventory.transactions.at_retired_place",
    unit="{transaction}",
    description="Batches written to or from a retired place, by side",
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
    lines: Annotated[
        list[Line],
        Field(
            min_length=1,
            max_length=MAX_LINES,
            description="One line per item: a batch naming an item on more than "
            "one line is refused, 422 with type repeated on each of those lines.",
        ),
    ]

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
Problem = tuple[
    tuple[str | int, ...], Literal["not_found", "not_whole", "retired", "repeated"]
]
MESSAGES = {
    "not_found": "There is no such row",
    "repeated": "This item is on another line too: put it on one line",
    "not_whole": "This item is counted in whole units",
    "retired": "This item is retired: what is left may be taken, never received",
}


class BatchRefused(Exception):
    def __init__(self, problems: list[Problem]) -> None:
        super().__init__(f"{len(problems)} problems")
        self.problems = problems


class KeyReused(Exception):
    """The key was already used, for a different batch."""


Side = Literal["from", "to"]


def retired_sides(
    from_id: int | None, to_id: int | None, places: Mapping[int, bool]
) -> list[Side]:
    """The sides of a batch whose place `places` (by id, whether retired)
    says is retired."""
    return [
        side
        for side, location_id in (("from", from_id), ("to", to_id))
        if location_id is not None and places.get(location_id, False)
    ]


class Recorded(NamedTuple):
    transaction: LedgerTransaction
    # Whether this call wrote it, and if so, the sides at a retired place.
    written: bool
    retired: Sequence[Side] = ()


def record(session: Session, batch: Batch, *, device_id: int | None) -> Recorded:
    """Write the batch, or find the one already written with its key. The
    caller commits.

    A batch already written is not checked again: the catalogue may have
    changed since, and the volunteer must not be asked to fix what is saved."""
    if (written := _replay(session, batch)) is not None:
        return Recorded(written, False)
    header = {"device_id": device_id, **_header(batch)}
    lines = [(line.item_id, line.quantity) for line in batch.lines]
    held = hold(session, header, [item_id for item_id, _ in lines])
    problems = _problems(session, batch, held, lines)
    if problems:
        refused.add(1, {"reason": "invalid"})
        log.info("batch refused", problems=sorted({kind for _, kind in problems}))
        raise BatchRefused(problems)
    transaction = insert_transaction(session, batch.key, header, lines)
    if transaction is None:
        written = _replay(session, batch)
        assert written is not None  # The conflict was on this key.
        return Recorded(written, False)
    retired = retired_sides(batch.from_location_id, batch.to_location_id, held.places)
    return Recorded(transaction, True, retired)


# A save, count or adjustment holds the rows it refers to until it ends, so
# what it read of them holds when it is written, in this order, as every
# change that locks rows a save refers to must (db.locked_row):
#
# 1. Its places, FOR KEY SHARE (_hold_places), before it has an id.
# 2. Its items, FOR KEY SHARE, by id (hold): what the ledger's rules read
#    of them, a unit and whether retired. Changing either takes FOR UPDATE
#    (items.save, items.retire), which waits for it, and it for that.
# 3. Its volunteer, device and administrator, FOR KEY SHARE, as the
#    transaction's foreign keys take them.
# 4. A receipt's items, FOR NO KEY UPDATE, by id (_receive), to lower what
#    is on order, which KEY SHARE does not wait for.


def _hold_places(session: Session, header: Mapping[str, object]) -> dict[int, bool]:
    """A save's places, held before it has an id: a count (lock_place) then
    waits for it whole, so none can draw an id, wait, and commit after a
    count's moment that it falls inside. Those that exist, by id, and
    whether each is retired."""
    rows = session.execute(
        locked(
            select(Location.id, Location.retired_at.is_not(None)).where(
                Location.id.in_(
                    {header.get("from_location_id"), header.get("to_location_id")}
                    - {None}
                )
            ),
            key_share=True,
            shared=True,
        )
    )
    return {location_id: retired for location_id, retired in rows}


class Held(NamedTuple):
    """The rows hold() holds that exist: places by id, whether retired, and
    items."""

    places: dict[int, bool]
    items: dict[int, Item]


def hold(
    session: Session, header: Mapping[str, object], item_ids: Collection[int]
) -> Held:
    """Hold a transaction's places, then its items."""
    places = _hold_places(session, header)
    if not item_ids:
        return Held(places, {})
    query = select(Item).where(Item.id.in_(list(item_ids))).order_by(Item.id)
    held = session.scalars(locked(query, key_share=True, shared=True))
    return Held(places, {item.id: item for item in held})


def insert_transaction(
    session: Session,
    key: uuid.UUID,
    header: Mapping[str, object],
    lines: Lines,
) -> LedgerTransaction | None:
    """A transaction and its movements, unless its key is written already:
    None then, once a request writing it under way has committed. The one
    writer of the ledger. RuleBroken, before anything is written, for a
    transaction with no lines but a count, or a line that breaks a rule.
    Its rows are held here (hold), so every caller's lines are checked as
    locked; a caller that held them already only takes its own locks
    again. created_at is the database's now(), whatever the header says. A
    receipt lowers what is on order of each item it brings. RuleBroken
    (movement_item_once) for an item on more than one line."""
    kind = str(header["kind"])
    enforce("ledger_transaction_lines", when=not lines and kind != "count")
    items = hold(session, header, _each_once(lines)).items
    for broken in lines_break(kind, items, lines):
        # An item that does not exist is the foreign key's to refuse.
        for rule in broken or ():
            enforce(rule)
    transaction = session.scalar(
        pg_insert(LedgerTransaction)
        .values({**header, "idempotency_key": key, "created_at": func.now()})
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
                for item_id, amount in lines
            ],
        )
        if transaction.kind == "receipt":
            _receive(session, lines)
    return transaction


def _each_once(lines: Lines) -> list[int]:
    """The lines' items, each on one line, or RuleBroken
    (movement_item_once)."""
    item_ids = [item_id for item_id, _ in lines]
    enforce("movement_item_once", when=bool(repeated(item_ids)))
    return item_ids


def _receive(session: Session, lines: Lines) -> None:
    """Through the ORM, so it is history. The items locked by id, so two
    receipts of the same items take them in one order."""
    received = dict(lines)
    items = session.scalars(
        locked(
            select(Item)
            .where(Item.id.in_(received), Item.on_order > 0)
            .order_by(Item.id),
            key_share=True,
        )
    )
    for item in items:
        assert item.on_order is not None  # The query asked for more than 0.
        item.on_order = on_order_after(item.on_order, received[item.id])


# Each of line_breaks' rules, as a batch's problem: which field, and what.
LINE_PROBLEMS: dict[str, tuple[str, Literal["not_whole", "retired"]]] = {
    "movement_whole_units": ("quantity", "not_whole"),
    "receipt_retired_item": ("item_id", "retired"),
}


def _problems(
    session: Session, batch: Batch, held: Held, lines: Lines
) -> list[Problem]:
    """What is wrong with a batch, line by line (`lines`, its own), with its
    rows held (hold)."""
    problems: list[Problem] = []
    if session.get(Volunteer, batch.volunteer_id) is None:
        problems.append((("volunteer_id",), "not_found"))
    for field in ("from_location_id", "to_location_id"):
        location_id = getattr(batch, field)
        if location_id is not None and location_id not in held.places:
            problems.append(((field,), "not_found"))
    twice = repeated([item_id for item_id, _ in lines])
    for number, broken in enumerate(lines_break(batch.kind, held.items, lines)):
        if number in twice:
            problems.append((("lines", number, "item_id"), "repeated"))
        if broken is None:
            problems.append((("lines", number, "item_id"), "not_found"))
            continue
        for rule in broken:
            field, kind = LINE_PROBLEMS[rule]
            problems.append((("lines", number, field), kind))
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


class Written(NamedTuple):
    """A count's or an adjustment's transaction, as written."""

    id: int
    kind: str
    lines: int


def _correct(
    session: Session,
    key: uuid.UUID,
    kind: Literal["count", "adjustment"],
    header: Mapping[str, object],
    lines: Lines,
) -> list[Written]:
    """A count's or an adjustment's transaction, as written: none if its
    key is written already."""
    transaction = insert_transaction(session, key, {"kind": kind, **header}, lines)
    return [] if transaction is None else [Written(transaction.id, kind, len(lines))]


class Counted(NamedTuple):
    differed: bool
    # Items counted that moved at the place since the form was shown, with
    # what came in and what went out since. Not written: count them again.
    moved: dict[int, tuple[Decimal, Decimal]]
    # What this send wrote: nothing if an earlier send of it did.
    written: list[Written]
    # How many lines it withheld that no earlier send of it did.
    withheld: int


def corrected(
    admin_id: int, location_id: int, written: Iterable[Written], withheld_lines: int = 0
) -> None:
    """Measure and log a count or an adjustment once committed, as
    save_batch does, so a failed commit and its retry count once."""
    for transaction in written:
        counted.add(1, {"kind": transaction.kind})
        log.info(
            f"{transaction.kind} recorded",
            ledger_transaction_id=transaction.id,
            admin_id=admin_id,
            lines=transaction.lines,
        )
    if withheld_lines:
        withheld.add(withheld_lines)
        log.info(
            "count lines withheld",
            location_id=location_id,
            admin_id=admin_id,
            lines=withheld_lines,
        )


def _first_sent(session: Session, location_id: int, *keys: uuid.UUID) -> int | None:
    """The first transaction at the place written under any of `keys`: a
    form sent before."""
    return session.scalar(
        select(func.min(LedgerTransaction.id)).where(
            LedgerTransaction.idempotency_key.in_(keys), _at_place(location_id)
        )
    )


def record_count(
    session: Session,
    key: uuid.UUID,
    admin_id: int,
    location_id: int,
    counted: Lines,
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
    count whose every line moved writes nothing.

    RuleBroken (location_countable) at a place that may not be counted,
    unless the form was sent there before, so it gets its first answer;
    RuleBroken (movement_item_once) for an item counted on two lines."""
    _each_once(counted)
    counted_at = dict(counted)
    lock_place(session, location_id)
    lost_key = uuid.uuid5(key, "lost")
    sent = _first_sent(session, location_id, key, lost_key)
    after = LedgerTransaction.id > as_of
    if sent is None:
        _require_countable(session, location_id)
    else:
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
    written: list[Written] = []
    # A count of nothing at all still says when the place was counted.
    if found or ((kept or not counted_at) and not lost):
        header = {"admin_id": admin_id, "to_location_id": location_id}
        written += _correct(session, key, "count", header, list(found.items()))
    if lost:
        header = {"admin_id": admin_id, "from_location_id": location_id}
        written += _correct(session, lost_key, "count", header, list(lost.items()))
    return Counted(
        bool(found or lost), moved, written, len(moved) if sent is None else 0
    )


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
) -> list[Written]:
    """Stock found at a place, or written off from it, for a reason. What
    it wrote: nothing if its key was written already. RuleBroken
    (location_countable), as record_count."""
    lock_place(session, location_id)
    if _first_sent(session, location_id, key) is not None:
        return []
    _require_countable(session, location_id)
    side = "to_location_id" if found else "from_location_id"
    header = {"admin_id": admin_id, "reason_id": reason_id, side: location_id}
    return _correct(session, key, "adjustment", header, [(item_id, amount)])


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
    """Write a whole batch, or none of it. A 422 names each bad line, as
    FastAPI's own do: an item, location or volunteer that does not exist, a
    fraction of an item counted in each, a retired item received, or an item
    another line names too (repeated): send each item on one line."""
    # So the history of anything the batch changes, such as on_order, says who.
    set_actor(session, f"volunteer:{batch.volunteer_id}")
    transaction, written, retired = record(session, batch, device_id=device.id)
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
    if retired:
        for side in retired:
            at_retired_place.add(1, {"side": side})
        # Accepted, as a volunteer's work is never lost; the place shows as
        # holding stock until it is counted (docs/decisions/0003).
        log.warning(
            "transaction at a retired place",
            transaction_id=transaction_id,
            from_location_id=batch.from_location_id,
            to_location_id=batch.to_location_id,
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
