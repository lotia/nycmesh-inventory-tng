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
from types import MappingProxyType
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

from inventory_tng import open_packages
from inventory_tng.db import (
    DbSession,
    RuleBroken,
    by_name,
    enforce,
    exists_where,
    get_locked,
    locked,
    set_actor,
)
from inventory_tng.devices import DeviceRefusedReply, RegisteredDevice
from inventory_tng.local_time import zone_at
from inventory_tng.models import (
    INTEGER_MAX,
    STORE_KINDS,
    Item,
    ItemPackage,
    LedgerTransaction,
    Location,
    Movement,
    OpenPackage,
    Volunteer,
)

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

router = APIRouter()


class Form(NamedTuple):
    """The form stock is in on one side of a ledger line
    (docs/decisions/0020): sealed packages of `package_id`, the open package
    `open_package_id`, or neither: loose units."""

    package_id: int | None = None
    open_package_id: int | None = None


LOOSE = Form()


class Entry(NamedTuple):
    """A line of a transaction: so much of an item, in its unit, leaving in
    one form and arriving in another. A line given as just an item and an
    amount is loose on both sides."""

    item_id: int
    quantity: Decimal
    leaves: Form = LOOSE
    arrives: Form = LOOSE


def _sides() -> Subquery:
    """Each side of every line, as stock into a place (+) or out of it (-),
    in the form it is in there. Each side is a select of its own, joined by
    UNION ALL, so a filter on a place or an item reaches the indexes on each
    side, instead of summing the whole ledger first."""
    moved = select(Movement.item_id).join(
        LedgerTransaction, LedgerTransaction.id == Movement.transaction_id
    )
    into = moved.add_columns(
        LedgerTransaction.to_location_id.label("location_id"),
        Movement.to_package_id.label("package_id"),
        Movement.to_open_package_id.label("open_package_id"),
        Movement.quantity.label("quantity"),
    ).where(LedgerTransaction.to_location_id.is_not(None))
    out = moved.add_columns(
        LedgerTransaction.from_location_id,
        Movement.from_package_id,
        Movement.from_open_package_id,
        -Movement.quantity,
    ).where(LedgerTransaction.from_location_id.is_not(None))
    return union_all(into, out).subquery("side")


def _summed(name: str, *by: str) -> Subquery:
    side = _sides()
    columns = [side.c[column] for column in ("item_id", "location_id", *by)]
    return (
        select(*columns, func.sum(side.c.quantity).label("quantity"))
        .group_by(*columns)
        .subquery(name)
    )


# In minus out, for every item and place stock has been at, whatever its
# form: a place whose stock all left keeps its row, at zero. Read as a
# table: select(balance.c.quantity).where(balance.c.item_id == ...).
balance = _summed("balance")
# The same, by form too: package_id for sealed packages, open_package_id
# for an open one, neither for loose units (Form).
by_form = _summed("by_form", "package_id", "open_package_id")


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
    kind: str, items: Mapping[int, Item], lines: Lines
) -> list[list[str] | None]:
    """For each line the rules of its item it breaks (line_breaks), or None
    if its item is not in `items`."""
    return [
        None
        if (item := items.get(line.item_id)) is None
        else line_breaks(kind, item, line.quantity)
        for line in entries(lines)
    ]


# A transaction's lines: Entry, or just an item id and an amount, loose. An
# item in each form on one line only (repeated), as its movements' unique
# index keeps them.
Lines = Sequence[Entry | tuple[int, Decimal]]
# Lines of an item and an amount alone, loose: a count's.
ItemLines = Sequence[tuple[int, Decimal]]


def entries(lines: Lines) -> list[Entry]:
    return [Entry(*line) for line in lines]


def repeated[K](keys: Sequence[K | None]) -> set[int]:
    """The positions of the lines whose key, such as an item, another line
    has too (None is no key, so repeats none). A batch or a count names each
    item, in each form, on one line: the ledger refuses a second
    (movement_item_once), so a page or the scan API says which lines,
    before it would."""
    times = Counter(keys)
    return {
        number for number, key in enumerate(keys) if key is not None and times[key] > 1
    }


def on_order_after(on_order: Decimal, received: Decimal) -> Decimal:
    """An item's on_order once `received` of it arrives: down by that much,
    never below zero."""
    return max(on_order - received, Decimal(0))


class PackageFacts(NamedTuple):
    """What the ledger's rules read of a package: its item, its size,
    whether opening one makes an open package (tracks_opened), and whether
    it is retired."""

    item_id: int
    size: Decimal
    tracked: bool
    retired: bool = False


class OpenFacts(NamedTuple):
    """What the ledger's rules read of an open package: its item and
    package, where it is, whether closed, what is left on it there, and
    whether any line names it yet."""

    item_id: int
    package_id: int
    location_id: int
    closed: bool
    left: Decimal
    new: bool


def form_breaks(
    kind: str,
    entry: Entry,
    places: tuple[int | None, int | None],
    packages: Mapping[int, PackageFacts],
    opens: Mapping[int, OpenFacts],
) -> list[str]:
    """The rules of docs/decisions/0020 a line breaks, in a transaction of
    `kind` between `places` (from, to; None outside). Empty if none.

    - No form outside: what comes from or goes outside is loose.
    - A sealed side names a package of the line's item, and moves a whole
      number of them; a retired package is never received, as a retired
      item is not (0003).
    - An open side names an open package of the line's item, not closed,
      at that side's place, but for one moved whole: all that is left on it
      leaves one place and arrives at the other. One not yet filled is
      named only by its opening, or by a count that finds it.
    - Between two places, stock keeps its form, or leaves an open package
      loose (a cut); only opening changes it otherwise, a sealed package
      into an open one, made for it and full, if its item tracks opened
      packages, else into loose units."""
    broken: list[str] = []
    sides = list(zip(places, (entry.leaves, entry.arrives), strict=True))
    if any(place is None and form != LOOSE for place, form in sides):
        broken.append("movement_form_outside")
    for arriving, (place, form) in enumerate(sides):
        if form.package_id is not None:
            package = packages.get(form.package_id)
            if package is None or package.item_id != entry.item_id:
                broken.append("movement_package_of_item")
            elif entry.quantity % package.size != 0:
                broken.append("movement_whole_packages")
            if package is not None and package.retired and kind == "receipt":
                broken.append("receipt_retired_package")
        if form.open_package_id is not None:
            opened = opens.get(form.open_package_id)
            if opened is None or opened.item_id != entry.item_id:
                broken.append("movement_open_package_of_item")
                continue
            if opened.closed:
                broken.append("movement_open_package_closed")
            if opened.new and kind not in ("open", "count"):
                broken.append("movement_open_package_unopened")
            whole = arriving and entry.leaves == entry.arrives
            if not whole and place is not None and place != opened.location_id:
                broken.append("movement_open_package_place")
    leaves, arrives = entry.leaves, entry.arrives
    if kind == "open":
        broken += _opening_breaks(entry, packages, opens)
    elif None not in places:
        cut = leaves.open_package_id is not None and arrives == LOOSE
        if leaves != arrives and not cut:
            broken.append("movement_form_change")
        elif leaves.open_package_id is not None and leaves == arrives:
            opened = opens.get(leaves.open_package_id)
            if opened is not None and entry.quantity != opened.left:
                broken.append("movement_open_package_whole")
    return list(dict.fromkeys(broken))


def _opening_breaks(
    entry: Entry, packages: Mapping[int, PackageFacts], opens: Mapping[int, OpenFacts]
) -> list[str]:
    """form_breaks' rules of opening: a sealed package becomes an open one,
    made for it, unopened before, and full; or loose units, as its item
    says (opening_follows_setting)."""
    package = packages.get(entry.leaves.package_id or 0)
    if package is None or entry.leaves.open_package_id is not None:
        return ["opening_from_sealed"]
    if entry.arrives.package_id is not None:
        return ["opening_from_sealed"]
    opened_id = entry.arrives.open_package_id
    if (opened_id is not None) != package.tracked:
        return ["opening_follows_setting"]
    opened = opens.get(opened_id or 0)
    if opened is None:
        return []
    if not opened.new or opened.package_id != entry.leaves.package_id:
        return ["open_package_opened_once"]
    if entry.quantity != package.size:
        return ["open_package_full"]
    return []


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
    # An opening changes the form of stock at a place, not how much is there.
    where = [_at_place(location_id), or_(*windows), LedgerTransaction.kind != "open"]
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
VolunteerKind = Literal["move", "receipt", "install", "open"]
SIDES: dict[str, tuple[bool, bool]] = {
    "move": (True, True),
    "receipt": (False, True),
    "install": (True, False),
    # At one place: from and to are the same (docs/decisions/0020).
    "open": (True, True),
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
    description="Lines in each batch written, by kind: an item is on one "
    "line for each package and one loose",
)
packages_opened = meter.create_counter(
    "inventory.packages.opened",
    unit="{package}",
    description="Sealed packages opened, by what each became: an open package "
    "followed until used up, or loose units",
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
    package_id: Annotated[
        Number | None,
        Field(
            description="Sealed packages of this package of the item, "
            "kept sealed (docs/decisions/0020): `quantity` is a whole "
            "number of them, in units. None: loose units. An opening's "
            "lines name the package opened."
        ),
    ] = None
    open_package_key: Annotated[
        uuid.UUID | None,
        Field(
            description="An opening's: the key of the open package made, if "
            "its item tracks opened packages, made by the page so a later "
            "line can name it. One package a line, then. Any other line's: "
            "the open package it is taken from, cut loose, or moved whole."
        ),
    ] = None
    whole: Annotated[
        bool | None,
        Field(
            description="A move's line taken from an open package: all that "
            "is left on it, `quantity`, moves with it, and it stays open there."
        ),
    ] = None

    def entry(self, kind: str, opened: int | None = None) -> Entry:
        """As the ledger writes it: sealed on each side the kind has, loose
        outside. An opening's leaves sealed and arrives as the open package
        `opened`, or loose."""
        sealed = Form(package_id=self.package_id)
        if kind == "open":
            arrives = Form(open_package_id=opened) if opened else LOOSE
            return Entry(self.item_id, self.quantity, sealed, arrives)
        if opened is not None:
            # Taken from an open package: cut loose, or moved whole.
            taken = Form(open_package_id=opened)
            return Entry(
                self.item_id, self.quantity, taken, taken if self.whole else LOOSE
            )
        has_from, has_to = SIDES[kind]
        return Entry(
            self.item_id,
            self.quantity,
            sealed if has_from else LOOSE,
            sealed if has_to else LOOSE,
        )


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
            description="One line per item and package (or loose): a batch "
            "naming one on more than one line is refused, 422 with type repeated "
            "on each of those lines.",
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
        if self.kind == "open":
            if self.from_location_id != self.to_location_id:
                raise ValueError("an opening is at one place: from and to are it")
            if any(line.package_id is None for line in self.lines):
                raise ValueError("an opening's lines each name a package")
        elif self.from_location_id == self.to_location_id:
            raise ValueError("from and to must be different locations")
        for line in self.lines if self.kind != "open" else ():
            taken = line.open_package_key is not None
            if taken and (line.package_id is not None or self.kind == "receipt"):
                raise ValueError("a line is taken from an open package, or not")
            if line.whole and not (taken and self.kind == "move"):
                raise ValueError("only a move's open package moves whole")
        if self.install_number is not None and self.kind != "install":
            raise ValueError("only an install has an install number")
        return self


# Where in the batch, as pydantic gives it ("lines", 2, "item_id"), and what.
ProblemKind = Literal[
    "not_found",
    "not_whole",
    "not_whole_packages",
    "one_package",
    "open_package_closed",
    "open_package_elsewhere",
    "open_package_whole",
    "refused",
    "retired",
    "retired_package",
    "repeated",
]
Problem = tuple[tuple[str | int, ...], ProblemKind]
MESSAGES = {
    "one_package": "Open one package a line: it is followed until used up",
    "open_package_closed": "That open package was closed as used up: take from another",
    "open_package_elsewhere": "That open package is at another place now",
    "open_package_whole": "Moving an open package whole takes all that is "
    "left on it, which changed: load the page again",
    "refused": "The inventory's rules refuse this as sent: it changed since "
    "the page loaded. Load the page again, and do it again",
    "not_found": "There is no such row",
    "repeated": "This item, in this package or loose, is on another line too: "
    "put it on one line",
    "not_whole": "This item is counted in whole units",
    "not_whole_packages": "Whole packages only",
    "retired": "This item is retired: what is left may be taken, never received",
    "retired_package": "This package is retired: what is left may be taken, "
    "never received",
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
    # How many packages an opening opened, by what they became.
    opened: Mapping[Into, int] = MappingProxyType({})


def record(session: Session, batch: Batch, *, device_id: int | None) -> Recorded:
    """Write the batch, or find the one already written with its key. The
    caller commits.

    A batch already written is not checked again: the catalogue may have
    changed since, and the volunteer must not be asked to fix what is saved."""
    if (written := _replay(session, batch)) is not None:
        return Recorded(written, False)
    header = {"device_id": device_id, **_header(batch)}
    # An opening's are made later (_opening); another's are taken from.
    taken = {} if batch.kind == "open" else _open_packages_named(session, batch)
    lines = [
        line.entry(batch.kind, taken.get(number))
        for number, line in enumerate(batch.lines)
    ]
    held = hold(session, header, [line.item_id for line in lines])
    packages, opens, _ = _hold_forms(session, lines)
    problems = _problems(session, batch, held, lines, packages, opens)
    problems += [
        (("lines", number, "open_package_key"), "not_found")
        for number, line in enumerate(batch.lines)
        if batch.kind != "open"
        and line.open_package_key is not None
        and number not in taken
    ]
    if problems:
        refused.add(1, {"reason": "invalid"})
        log.info("batch refused", problems=sorted({kind for _, kind in problems}))
        raise BatchRefused(problems)
    try:
        lines, opened = _opening(session, batch, packages, lines)
        transaction = insert_transaction(session, batch.key, header, lines)
    except RuleBroken as broken:
        # A rule _problems could not see: the catalogue changed since the
        # page's, as a package's setting, or a key used elsewhere. Said, so
        # the outbox shows it rather than sending it again and again.
        refused.add(1, {"reason": "invalid"})
        log.info("batch refused", rule=broken.rule)
        raise BatchRefused([(("lines",), "refused")]) from broken
    if transaction is None:
        written = _replay(session, batch)
        assert written is not None  # The conflict was on this key.
        return Recorded(written, False)
    retired = retired_sides(batch.from_location_id, batch.to_location_id, held.places)
    return Recorded(transaction, True, retired, opened)


# What an opened package became: an open package, followed, or loose units.
Into = Literal["open", "loose"]


def _opening_key(batch: Batch, number: int) -> uuid.UUID:
    """The key an opening's line makes its open package under: the page's,
    or one made from the batch's, so it is the same however often sent."""
    line = batch.lines[number]
    return line.open_package_key or uuid.uuid5(batch.key, f"open {number}")


def _opening(
    session: Session,
    batch: Batch,
    packages: Mapping[int, PackageFacts],
    lines: list[Entry],
) -> tuple[list[Entry], dict[Into, int]]:
    """A batch's lines (`lines`, as Line.entry makes them) as the ledger
    writes them: an opening's packages its items track opened become open
    packages (open_packages.make), and the rest loose units, one line for
    each package however many lines the page sent, as it may have thought
    them followed. And how many packages it opened, by what each became."""
    if batch.kind != "open":
        return lines, {}
    opened: dict[Into, int] = {}
    made: dict[int, int] = {}
    for number, line in enumerate(batch.lines):
        assert line.package_id is not None  # Batch's validator.
        package = packages[line.package_id]
        into: Into = "open" if package.tracked else "loose"
        opened[into] = opened.get(into, 0) + int(line.quantity / package.size)
        if package.tracked:
            key = _opening_key(batch, number)
            where = batch.from_location_id or 0
            made[number] = open_packages.make(session, key, line.package_id, where).id
    return _as_opened(batch, made), opened


def _as_opened(batch: Batch, made: Mapping[int, int]) -> list[Entry]:
    """An opening's lines, the open package `made` for a line by its
    number, if any; the others loose, one line for each package."""
    written: list[Entry] = []
    loose: dict[int | None, Entry] = {}
    for number, line in enumerate(batch.lines):
        if number in made:
            written.append(line.entry(batch.kind, made[number]))
        elif (before := loose.get(line.package_id)) is None:
            loose[line.package_id] = line.entry(batch.kind)
        else:
            loose[line.package_id] = before._replace(
                quantity=before.quantity + line.quantity
            )
    return written + list(loose.values())


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
# 4. Its packages, FOR KEY SHARE, by id: their item, size and whether
#    tracked opened, which a change to one waits for (items.py).
# 5. Its open packages, FOR NO KEY UPDATE, by id: where each is and whether
#    closed, which a report or close waits for (open_packages.py), and what
#    is left on it, which another line on it waits for; and so a whole move
#    changes where it is.
# 6. A receipt's items, FOR NO KEY UPDATE, by id (_receive), to lower what
#    is on order, which KEY SHARE does not wait for.


def _hold_places(
    session: Session, header: Mapping[str, object]
) -> tuple[dict[int, bool], frozenset[int]]:
    """A save's places, held before it has an id: a count (lock_place) then
    waits for it whole, so none can draw an id, wait, and commit after a
    count's moment that it falls inside. Those that exist, by id, and
    whether each is retired; and which are volunteers' custody."""
    rows = session.execute(
        locked(
            select(Location.id, Location.retired_at.is_not(None), Location.kind).where(
                Location.id.in_(
                    {header.get("from_location_id"), header.get("to_location_id")}
                    - {None}
                )
            ),
            key_share=True,
            shared=True,
        )
    )
    found = rows.all()
    custody = frozenset(i for i, _, kind in found if kind == "custody")
    return {location_id: retired for location_id, retired, _ in found}, custody


class Held(NamedTuple):
    """The rows hold() holds that exist: places by id, whether retired, and
    items; and which places are volunteers' custody."""

    places: dict[int, bool]
    items: dict[int, Item]
    custody: frozenset[int] = frozenset()


def hold(
    session: Session, header: Mapping[str, object], item_ids: Collection[int]
) -> Held:
    """Hold a transaction's places, then its items."""
    places, custody = _hold_places(session, header)
    if not item_ids:
        return Held(places, {}, custody)
    query = select(Item).where(Item.id.in_(list(item_ids))).order_by(Item.id)
    held = session.scalars(locked(query, key_share=True, shared=True))
    return Held(places, {item.id: item for item in held}, custody)


def _hold_forms(
    session: Session, lines: Sequence[Entry]
) -> tuple[dict[int, PackageFacts], dict[int, OpenFacts], dict[int, OpenPackage]]:
    """Hold the packages and open packages a transaction's lines name, in
    that order (above), and read what form_breaks needs of them."""
    forms = [form for line in lines for form in (line.leaves, line.arrives)]
    package_ids = sorted({f.package_id for f in forms if f.package_id is not None})
    open_ids = sorted(
        {f.open_package_id for f in forms if f.open_package_id is not None}
    )
    packages: dict[int, PackageFacts] = {}
    if package_ids:
        rows = session.execute(
            locked(
                select(ItemPackage, Item.track_opened)
                .join(Item, Item.id == ItemPackage.item_id)
                .where(ItemPackage.id.in_(package_ids))
                .order_by(ItemPackage.id),
                key_share=True,
                shared=True,
                of=ItemPackage,
            )
        )
        packages = {
            package.id: PackageFacts(
                package.item_id,
                package.size,
                open_packages.tracks_opened(item_tracks, package.track_opened),
                package.retired_at is not None,
            )
            for package, item_tracks in rows
        }
    if not open_ids:
        return packages, {}, {}
    opened = {
        row.id: row
        for row in session.scalars(
            locked(
                select(OpenPackage)
                .where(OpenPackage.id.in_(open_ids))
                .order_by(OpenPackage.id),
                key_share=True,
            )
        )
    }
    # What is left on each where it is. One no line names yet has no row
    # in by_form at all.
    rows = session.execute(
        select(
            by_form.c.open_package_id, by_form.c.location_id, by_form.c.quantity
        ).where(by_form.c.open_package_id.in_(open_ids))
    ).all()
    named = {open_id for open_id, _, _ in rows}
    left = {
        open_id: quantity
        for open_id, location_id, quantity in rows
        if location_id == opened[open_id].location_id
    }
    facts = {
        row.id: OpenFacts(
            row.item_id,
            row.package_id,
            row.location_id,
            row.closed_at is not None,
            left.get(row.id, Decimal(0)),
            row.id not in named,
        )
        for row in opened.values()
    }
    return packages, facts, opened


def _where(header: Mapping[str, object], custody: frozenset[int]) -> int | None:
    """The place whose zone a transaction happened in: where stock left,
    else where it arrived (a receipt, or stock a count or adjustment
    found); where it arrived, too, from a volunteer's `custody`, which is a
    person, not a place with a clock."""
    left = _place(header, "from_location_id")
    arrives = _place(header, "to_location_id")
    if left is not None and not (left in custody and arrives is not None):
        return left
    return arrives


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
    again. created_at is the database's now(), whatever the header says;
    time_zone the zone of its place then (local_time.zone_at). A
    receipt lowers what is on order of each item it brings. RuleBroken
    (movement_item_once) for an item on more than one line."""
    kind = str(header["kind"])
    lines = entries(lines)
    enforce("ledger_transaction_lines", when=not lines and kind != "count")
    held = hold(session, header, _each_once(lines))
    items = held.items
    for broken in lines_break(kind, items, lines):
        # An item that does not exist is the foreign key's to refuse.
        for rule in broken or ():
            enforce(rule)
    packages, opens, opened = _hold_forms(session, lines)
    # The form rules read what the transaction itself changes, so one sent
    # again is found before them: the open packages' locks waited for it.
    if opens and exists_where(session, LedgerTransaction.idempotency_key == key):
        return None
    places = (_place(header, "from_location_id"), _place(header, "to_location_id"))
    for line in lines:
        for rule in form_breaks(kind, line, places, packages, opens):
            enforce(rule)
    zone = zone_at(session, _where(header, held.custody))
    transaction = session.scalar(
        pg_insert(LedgerTransaction)
        .values(
            {
                **header,
                "idempotency_key": key,
                "created_at": func.now(),
                "time_zone": zone,
            }
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(LedgerTransaction)
    )
    if transaction is not None and lines:
        session.execute(
            insert(Movement),
            [
                {
                    "transaction_id": transaction.id,
                    "item_id": line.item_id,
                    "quantity": line.quantity,
                    "from_package_id": line.leaves.package_id,
                    "from_open_package_id": line.leaves.open_package_id,
                    "to_package_id": line.arrives.package_id,
                    "to_open_package_id": line.arrives.open_package_id,
                }
                for line in lines
            ],
        )
        # An open package moved whole is where it arrived: form_breaks
        # checked that all of it did. Through the ORM, so it is history.
        for line in lines:
            open_id = line.arrives.open_package_id
            if open_id is not None and line.leaves == line.arrives and places[1]:
                opened[open_id].location_id = places[1]
                log.info(
                    "open package moved", open_package_id=open_id, location_id=places[1]
                )
        if transaction.kind == "receipt":
            _receive(session, lines)
    return transaction


def _place(header: Mapping[str, object], side: str) -> int | None:
    place = header.get(side)
    return place if isinstance(place, int) else None


def _each_once(lines: Sequence[Entry]) -> list[int]:
    """The lines' items, each in each form on one line, or RuleBroken
    (movement_item_once); and each open package on one line, as each line
    is checked against what was left on it before the transaction
    (movement_open_package_once)."""
    keys = [(line.item_id, line.leaves, line.arrives) for line in lines]
    enforce("movement_item_once", when=bool(repeated(keys)))
    enforce("movement_open_package_once", when=bool(repeated(open_packages_of(lines))))
    return list(dict.fromkeys(line.item_id for line in lines))


def open_packages_of(lines: Sequence[Entry]) -> list[int | None]:
    """The open package each line names, if any: one, on either side or
    both."""
    return [
        line.leaves.open_package_id or line.arrives.open_package_id for line in lines
    ]


def _receive(session: Session, lines: Sequence[Entry]) -> None:
    """Through the ORM, so it is history. The items locked by id, so two
    receipts of the same items take them in one order."""
    received: dict[int, Decimal] = {}
    for line in lines:
        received[line.item_id] = received.get(line.item_id, Decimal(0)) + line.quantity
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


def _line_form_breaks(
    kind: str,
    line: Entry,
    places: tuple[int | None, int | None],
    packages: Mapping[int, PackageFacts],
    opens: Mapping[int, OpenFacts],
) -> list[str]:
    """form_breaks' rules a batch's line breaks, before an opening's open
    packages are made: one opened into one, if its item tracks them, is one
    whole package (open_package_full)."""
    package = packages.get(line.leaves.package_id or 0)
    if kind != "open" or package is None or not package.tracked:
        return form_breaks(kind, line, places, packages, opens)
    sealed = Entry(line.item_id, line.quantity, line.leaves, line.leaves)
    broken = form_breaks("move", sealed, places, packages, {})
    if line.quantity != package.size:
        broken.append("open_package_full")
    return broken


# Each of the rules a batch's line can break, as its problem: which field,
# and what. A batch's forms are sealed or loose alike on both sides
# (Line.entry), or an opening's, so no other form rule can be broken by one.
LINE_PROBLEMS: dict[str, tuple[str, ProblemKind]] = {
    "movement_whole_units": ("quantity", "not_whole"),
    "receipt_retired_item": ("item_id", "retired"),
    "movement_package_of_item": ("package_id", "not_found"),
    "movement_whole_packages": ("quantity", "not_whole_packages"),
    "receipt_retired_package": ("package_id", "retired_package"),
    "open_package_full": ("quantity", "one_package"),
    "opening_from_sealed": ("package_id", "not_found"),
    "movement_open_package_of_item": ("open_package_key", "not_found"),
    "movement_open_package_unopened": ("open_package_key", "not_found"),
    "movement_open_package_closed": ("open_package_key", "open_package_closed"),
    "movement_open_package_place": ("open_package_key", "open_package_elsewhere"),
    "movement_open_package_whole": ("quantity", "open_package_whole"),
}


def _problems(
    session: Session,
    batch: Batch,
    held: Held,
    lines: Sequence[Entry],
    packages: Mapping[int, PackageFacts],
    opens: Mapping[int, OpenFacts],
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
    twice = repeated(
        [
            (line.item_id, line.leaves, line.arrives, sent.open_package_key)
            for line, sent in zip(lines, batch.lines, strict=True)
        ]
    )
    places = (batch.from_location_id, batch.to_location_id)
    for number, broken in enumerate(lines_break(batch.kind, held.items, lines)):
        if number in twice:
            problems.append((("lines", number, "item_id"), "repeated"))
        if broken is None:
            problems.append((("lines", number, "item_id"), "not_found"))
            continue
        broken += _line_form_breaks(batch.kind, lines[number], places, packages, opens)
        for rule in dict.fromkeys(broken):
            field, kind = LINE_PROBLEMS[rule]
            problems.append((("lines", number, field), kind))
    # Two rules may say one thing of one field: said once.
    return list(dict.fromkeys(problems))


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
    counted: ItemLines,
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
    _each_once(entries(counted))
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
    rows = session.execute(
        select(
            Movement.item_id,
            Movement.quantity,
            Movement.from_package_id,
            Movement.from_open_package_id,
            Movement.to_package_id,
            Movement.to_open_package_id,
        ).where(Movement.transaction_id == transaction.id)
    )
    # As many of each line, whatever their order: an item may be on several
    # lines, in different forms.
    written = Counter(
        Entry(
            item_id, quantity, Form(from_package, from_open), Form(to_package, to_open)
        )
        for item_id, quantity, from_package, from_open, to_package, to_open in rows
    )
    sent = Counter(_written_as(session, batch))
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


def _open_packages_named(session: Session, batch: Batch) -> dict[int, int]:
    """The open packages a batch's lines name by key, by line number: an
    opening's made under them, another's taken from. A key with none is
    left out."""
    keys = {
        key: number
        for number, line in enumerate(batch.lines)
        if (
            key := _opening_key(batch, number)
            if batch.kind == "open"
            else line.open_package_key
        )
        is not None
    }
    if not keys:
        return {}
    found = session.execute(
        select(OpenPackage.key, OpenPackage.id).where(OpenPackage.key.in_(keys))
    )
    return {keys[key]: open_id for key, open_id in found}


def _written_as(session: Session, batch: Batch) -> list[Entry]:
    """A batch's lines as written once, to compare with what was: an
    opening's with the open packages made under their keys (_opening),
    another's taken from the open packages they name."""
    named = _open_packages_named(session, batch)
    if batch.kind == "open":
        return _as_opened(batch, named)
    return [
        line.entry(batch.kind, named.get(number))
        for number, line in enumerate(batch.lines)
    ]


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
    FastAPI's own do: an item, package, location or volunteer that does not
    exist, a fraction of an item counted in each or of a package, a retired
    item or package received, or an item another line names in the same
    package, or loose, too (repeated): send each item on one line for each
    package it is in, sealed, and one loose (docs/decisions/0020)."""
    # So the history of anything the batch changes, such as on_order, says who.
    set_actor(session, f"volunteer:{batch.volunteer_id}")
    transaction, written, retired, opened = record(session, batch, device_id=device.id)
    # Read before the commit, which expires it.
    transaction_id = transaction.id
    session.commit()
    if not written:
        response.status_code = 200
        return Saved(transaction_id=transaction_id)
    # Counted once committed, so a failed commit and its retry count once.
    recorded.add(1, {"kind": batch.kind})
    lines_per_batch.record(len(batch.lines), {"kind": batch.kind})
    for into, packages in opened.items():
        packages_opened.add(packages, {"into": into})
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
