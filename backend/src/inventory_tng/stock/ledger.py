"""The ledger's core (docs/decisions/0003): its rules, the order a save holds
the rows it refers to in, and insert_transaction, the one function that writes
it. A volunteer's batch (scan/batch.py), counts and adjustments
(stock/counts.py) all reach it, and it refuses what breaks the ledger's rules
(db_errors.RuleBroken), as hold() finds them. The app's login may only read and
add to the ledger (DEPLOYMENT.md, Database roles), and nothing here adds
movements to a transaction already written, so a transaction never changes once
written.

Two choices that keep a volunteer's work from being lost:

- Retired items, locations and volunteers are still accepted. A batch can
  wait in the outbox while an administrator retires or merges something, and
  readers follow a merged volunteer forward. The exceptions: a retired
  item is never received (docs/decisions/0003), so a receipt of one is
  refused, and the outbox says so; and a retired package with none sealed
  left at a current place, nor at the place it is taken from, is never
  taken sealed, from any client, the scan page included (0003, Amendment,
  2026-11-19; _none_sealed). A save at a retired place is logged and
  counted (scan/batch.py), and the place shows as holding stock
  (balance.retired_holding_stock) until an administrator counts it.
- The server's clock decides when a batch happened, not the phone's: a batch
  resent from the outbox is recorded when it arrives. created_at is the
  database's now(), and nothing sent sets it.
"""

import uuid
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from decimal import Decimal
from typing import NamedTuple

import structlog
from sqlalchemy import func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from inventory_tng.core.db_errors import enforce
from inventory_tng.core.db_locking import get_locked, locked
from inventory_tng.core.db_once_committed import once_committed
from inventory_tng.core.db_sessions import exists_where
from inventory_tng.core.local_time import zone_at
from inventory_tng.models.catalogue import Item, ItemPackage
from inventory_tng.models.places import Location
from inventory_tng.models.stock import LedgerTransaction, Movement, OpenPackage
from inventory_tng.stock.balance import by_form, sealed_somewhere

log = structlog.get_logger(__name__)


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


def tracks_opened(item: bool, package: bool | None) -> bool:
    """Whether opening a package makes an open package, followed until used
    up (docs/decisions/0020, Amendment): the package's own setting, if it
    has one, else its item's."""
    return item if package is None else package


class PackageFacts(NamedTuple):
    """What the ledger's rules read of a package: its item, its size,
    whether opening one makes an open package (tracks_opened), whether it
    is retired, and whether a line takes it sealed with none left
    (_none_sealed)."""

    item_id: int
    size: Decimal
    tracked: bool
    retired: bool = False
    none_sealed: bool = False


# The kinds a volunteer takes stock from a place in.
TAKES = ("move", "install", "open")


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
      item is not (0003), nor taken once none is left sealed (_none_sealed;
      0003, Amendment, 2026-11-19).
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
            taken = not arriving and kind in TAKES
            if package is not None and package.none_sealed and taken:
                broken.append("movement_retired_package_none_left")
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


def lock_place(session: Session, location_id: int) -> None:
    """Wait for every save under way at a place, and hold up new ones until
    this transaction ends, so its ledger reads as one moment: every save
    takes FOR KEY SHARE on its places before it draws an id
    (_hold_places), and this takes FOR UPDATE, as a retire of the place
    does (locations.retire), so the two go one at a time."""
    get_locked(session, Location, location_id)


def lock_places(session: Session, location_ids: Iterable[int]) -> None:
    """lock_place for each of these places, by id, as saves take places, so
    none waits on another the wrong way round."""
    for location_id in sorted(set(location_ids)):
        lock_place(session, location_id)


# A save, count or adjustment holds the rows it refers to until it ends, so
# what it read of them holds when it is written, in this order, as every
# change that locks rows a save refers to must (db_locking.locked_row):
#
# 1. Its places, FOR KEY SHARE (_hold_places), before it has an id.
# 2. Its items, FOR KEY SHARE, by id (hold): what the ledger's rules read
#    of them, a unit and whether retired. Changing either takes FOR UPDATE
#    (items.save, items.retire), which waits for it, and it for that.
# 3. Its volunteer, device and administrator, FOR KEY SHARE, as the
#    transaction's foreign keys take them.
# 4. Its packages, FOR KEY SHARE, by id: their item, size and whether
#    tracked opened, which a change to one waits for (catalogue/items.py).
#    Then the retired ones a line takes sealed, FOR NO KEY UPDATE, by id,
#    so two saves taking one go one at a time, and the second reads
#    whether any is left sealed once the first has committed.
# 5. Its open packages, FOR NO KEY UPDATE, by id: where each is and whether
#    closed, which a report or close waits for (stock/open_packages.py), and
#    what is left on it, which another line on it waits for; and so a whole
#    move changes where it is.
# 6. A receipt's items, FOR NO KEY UPDATE, by id (_receive), to lower what
#    is on order, which KEY SHARE does not wait for.


def _hold_places(
    session: Session, header: Mapping[str, object]
) -> tuple[dict[int, bool], frozenset[int]]:
    """A save's places, held before it has an id: a count (lock_place) then
    waits for it whole, so one that drew an id inside a count's moment and
    committed after it is found as the count is written
    (counts.record_count). Those that exist, by id, and
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


def hold_forms(
    session: Session,
    header: Mapping[str, object],
    lines: Sequence[Entry],
    *,
    also: Iterable[int] = (),
) -> tuple[dict[int, PackageFacts], dict[int, OpenFacts], dict[int, OpenPackage]]:
    """Hold the packages and open packages a transaction's lines name, and
    the open packages `also`, in that order (above), and read what
    form_breaks needs of them."""
    forms = [form for line in lines for form in (line.leaves, line.arrives)]
    package_ids = sorted({f.package_id for f in forms if f.package_id is not None})
    open_ids = sorted(
        {f.open_package_id for f in forms if f.open_package_id is not None} | set(also)
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
                tracks_opened(item_tracks, package.track_opened),
                package.retired_at is not None,
            )
            for package, item_tracks in rows
        }
        packages |= _none_sealed(session, header, lines, packages)
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


def _none_sealed(
    session: Session,
    header: Mapping[str, object],
    lines: Sequence[Entry],
    packages: Mapping[int, PackageFacts],
) -> dict[int, PackageFacts]:
    """The retired packages the lines of a kind in TAKES take sealed, held
    FOR NO KEY UPDATE (above), with none left sealed at a current place
    nor at the place they are taken from, as form_breaks reads them."""
    if header["kind"] not in TAKES:
        return {}
    leaving = {line.leaves.package_id for line in lines}
    taken = sorted(
        package_id
        for package_id, package in packages.items()
        if package.retired and package_id in leaving
    )
    if not taken:
        return {}
    held = locked(
        select(ItemPackage.id)
        .where(ItemPackage.id.in_(taken))
        .order_by(ItemPackage.id),
        key_share=True,
    )
    session.execute(held)
    left = sealed_somewhere(session, taken, at=_place(header, "from_location_id"))
    return {
        package_id: packages[package_id]._replace(none_sealed=True)
        for package_id in taken
        if package_id not in left
    }


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
    enforce(
        "ledger_transaction_lines", when=not lines and kind not in ("count", "report")
    )
    held = hold(session, header, each_once(lines))
    items = held.items
    for broken in lines_break(kind, items, lines):
        # An item that does not exist is the foreign key's to refuse.
        for rule in broken or ():
            enforce(rule)
    packages, opens, opened = hold_forms(session, header, lines)
    # The form rules read what the transaction itself changes, so one sent
    # again is found before them: the locks just taken waited for it.
    if exists_where(session, LedgerTransaction.idempotency_key == key):
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
                once_committed(
                    session,
                    log.info,
                    "open package moved",
                    open_package_id=open_id,
                    location_id=places[1],
                )
        if transaction.kind == "receipt":
            _receive(session, lines)
    return transaction


def _place(header: Mapping[str, object], side: str) -> int | None:
    place = header.get(side)
    return place if isinstance(place, int) else None


def each_once(lines: Sequence[Entry]) -> list[int]:
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
