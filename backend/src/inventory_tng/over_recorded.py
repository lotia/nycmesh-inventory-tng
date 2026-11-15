"""Lines recorded with more than was moved (docs/decisions/0020,
inventory-tng-0gg): a scan page's batch, changed after another tab recorded
it under the same key, is refused (409 key_reused), and what it took off a
line already recorded is kept here, for an administrator to correct. The
ledger never changes, so the volunteer cannot take it back; an
administrator corrects it with adjustments that undo the difference on
each side, or marks it checked, as "Needs your action" on the home page
asks. A count of its item at one of its ends, as of a moment after it,
puts that end right; one counted at every end it has closes as counted
(counted(); Ali, 2026-11-15, inventory-tng-qs8). Every write to the
over_recorded table."""

import uuid
from collections.abc import Collection, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Literal, NamedTuple

import structlog
from opentelemetry import metrics
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from inventory_tng import adjustment_reasons, ledger
from inventory_tng.db import RuleBroken, get_locked, locked
from inventory_tng.models import (
    Item,
    ItemPackage,
    LedgerTransaction,
    Location,
    Movement,
    OverRecorded,
)
from inventory_tng.pages import amount

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

# The reason the adjustments correcting one give.
REASON = "Recorded more than moved"

Settled = Literal["corrected", "checked", "counted"]

# Why one marked checked is right as it is, as the page says each
# (models.OVER_RECORDED_REASONS; Ali, 2026-11-15, inventory-tng-zgy). Mark
# checked is the only way of settling one that writes nothing, so a bare
# one could mean fixed elsewhere, nothing fixed, or one end fixed.
CHECKED_REASONS = {
    "left": "Counted where it left",
    "arrived": "Counted where it arrived",
    "both": "Counted both ends",
    "other": "Put right another way:",
}

raised_counter = meter.create_counter(
    "inventory.over_recorded.raised",
    unit="{line}",
    description="Lines recorded with more than was moved, as a batch changed "
    "after another tab recorded it said, raised or said again for an "
    "administrator to correct",
)
settled_counter = meter.create_counter(
    "inventory.over_recorded.settled",
    unit="{line}",
    description="Lines recorded with more than was moved that were settled, "
    "by how: corrected with adjustments, marked checked, then by why it was "
    "right as it was, or counted at every end",
)
brought_back_counter = meter.create_counter(
    "inventory.over_recorded.places_brought_back",
    unit="{location}",
    description="Retired places an administrator brought back from Recorded "
    "more than moved, to correct a line there",
)

WAITING = OverRecorded.settled_at.is_(None)


def raise_checks(session: Session, over: Mapping[int, Decimal]) -> int:
    """Keep, for each movement, how much more of it was recorded than moved:
    a new check, or one waiting told the newest amount; one settled stays
    settled. How many were raised or said again. The caller commits."""
    if not over:
        return 0
    rows = [
        {"movement_id": movement_id, "quantity": quantity}
        for movement_id, quantity in sorted(over.items())
    ]
    statement = insert(OverRecorded).values(rows)
    changed = session.execute(
        statement.on_conflict_do_update(
            index_elements=[OverRecorded.movement_id],
            set_={"quantity": statement.excluded.quantity},
            where=WAITING,
        ).returning(OverRecorded.id)
    ).all()
    for (check_id,) in changed:
        log.info("over recorded raised", over_recorded_id=check_id)
    return len(changed)


def measure_raised(count: int) -> None:
    """Measured once committed."""
    if count:
        raised_counter.add(count)


def measure_settled(how: Settled, reason: str | None = None) -> None:
    """One settled, measured once committed; marked checked, by its
    reason (CHECKED_REASONS), never its words."""
    settled_counter.add(1, {"how": how} | ({"reason": reason} if reason else {}))


def measure_brought_back() -> None:
    """A place brought back to correct one there, measured once committed."""
    brought_back_counter.add(1)


def seen(row: OverRecorded) -> str:
    """What an administrator saw of a check, to settle it only as seen: a
    later batch may say another amount meanwhile, and a count may put one
    of its ends right, so Correct would write only the other."""
    ends = (("left", row.left_count_id), ("arrived", row.arrived_count_id))
    return " ".join([amount(row.quantity), *(end for end, by in ends if by)])


# Where stock left, and where it arrived, and the counts that put each
# right.
FROM, TO = aliased(Location), aliased(Location)
LEFT_COUNT, ARRIVED_COUNT = aliased(LedgerTransaction), aliased(LedgerTransaction)


class OneCounted(NamedTuple):
    """A check one of whose two ends a count put right: the end counted
    and when, the end still to put right, as a sentence starts with each,
    and as its Correct button names it; and the place to count, by id and
    name, and whether it may be counted (ledger.countable). A move's ends
    are its places; an opening's, both at one place, its forms: "Sealed
    Spool" where it left, "Open Spool" or "Loose Screws" where it arrived,
    the names as the administrators gave them (Ali, 2026-11-15,
    inventory-tng-r5z)."""

    counted: str
    on: datetime
    still: str
    correct: str
    place_id: int
    place: str
    countable: bool


class MovedWhole(NamedTuple):
    """What an open package moved whole held when it moved: as its count
    box is filled, and in words."""

    count: str
    words: str


class Shown(NamedTuple):
    """A check waiting, as the administrators' pages show it."""

    row: OverRecorded
    item_id: int
    item: str
    unit: str
    # How much more was recorded: whole packages ("1 TIMES Bag of 50"), or
    # so much in the item's unit.
    amount: str
    transaction_id: int
    kind: str
    recorded_at: datetime
    from_place: str | None
    to_place: str | None
    # A move of an open package whole, which is now where it arrived, so
    # cannot be found again where it left: what it held then, for its count
    # to be confirmed or corrected there (confirm_count). None for others,
    # which Correct undoes.
    moved_whole: MovedWhole | None
    # One end of two put right by a count; None if neither is, or it has
    # one end only.
    one_counted: OneCounted | None


def amount_words(
    quantity: Decimal, unit: str, package: str | None, size: Decimal | None
) -> str:
    """`quantity` of an item as a volunteer counted it: whole packages of
    one, or so much in its unit."""
    if package is not None and size and quantity % size == 0:
        return f"{quantity // size} \N{MULTIPLICATION SIGN} {package}"
    after = "" if unit == "each" else f" {unit}"
    return f"{amount(quantity)}{after}"


def shown(session: Session, *where: ColumnElement[bool]) -> list[Shown]:
    """The checks waiting that `where` picks, by item name, oldest first:
    one query, however many."""
    package_id = func.coalesce(Movement.from_package_id, Movement.to_package_id)
    found = session.execute(
        select(
            OverRecorded,
            Item.id,
            Item.name,
            Item.unit,
            ItemPackage.name,
            ItemPackage.size,
            Movement.from_open_package_id,
            Movement.to_open_package_id,
            Movement.quantity,
            LedgerTransaction.id,
            LedgerTransaction.kind,
            LedgerTransaction.created_at,
            FROM.name,
            TO.name,
            LedgerTransaction.from_location_id,
            LedgerTransaction.to_location_id,
            LEFT_COUNT.created_at,
            ARRIVED_COUNT.created_at,
        )
        .join(Movement, Movement.id == OverRecorded.movement_id)
        .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
        .join(Item, Item.id == Movement.item_id)
        .outerjoin(ItemPackage, ItemPackage.id == package_id)
        .outerjoin(FROM, FROM.id == LedgerTransaction.from_location_id)
        .outerjoin(TO, TO.id == LedgerTransaction.to_location_id)
        .outerjoin(LEFT_COUNT, LEFT_COUNT.id == OverRecorded.left_count_id)
        .outerjoin(ARRIVED_COUNT, ARRIVED_COUNT.id == OverRecorded.arrived_count_id)
        .where(WAITING, *where)
        .order_by(func.lower(Item.name), Item.id, OverRecorded.id)
    ).all()
    # Only a place that may be counted is offered for counting: the end
    # still to put right of one with one end counted.
    still = {
        to_id if left_on else from_id
        for *_, from_id, to_id, left_on, arrived_on in found
        if (left_on is None) != (arrived_on is None)
    }
    countable = (
        set(
            session.scalars(
                select(Location.id).where(Location.id.in_(still), ledger.countable())
            )
        )
        if still
        else set()
    )
    lines = []
    for (
        row,
        item_id,
        item,
        unit,
        package,
        size,
        from_open,
        to_open,
        held,
        transaction_id,
        kind,
        at,
        from_place,
        to_place,
        from_id,
        to_id,
        left_on,
        arrived_on,
    ) in found:
        words = amount_words(row.quantity, unit, package, size)
        if from_open is not None and kind != "open":
            words += " from an open package"
        whole = from_open is not None and from_open == to_open
        one = None
        # Moved whole, it has one end only: where it arrived.
        if (
            from_id is not None
            and to_id is not None
            and not whole
            and (left_on is None) != (arrived_on is None)
        ):
            if kind == "open":
                # An opening leaves sealed, and arrives open if its item
                # tracks opened, else loose (ledger._opening_breaks).
                left = f"Sealed {package}"
                arrived = f"Open {package}" if to_open is not None else f"Loose {item}"
            else:
                left, arrived = from_place or "", to_place or ""
            # The other end's words, and the button's: a form's in the
            # sentence's middle ("Correct open Spool"), a place's as named.
            counted_end, on, other, still_id, still_place = (
                (left, left_on, arrived, to_id, to_place)
                if left_on is not None
                else (arrived, arrived_on, left, from_id, from_place)
            )
            correct = other[0].lower() + other[1:] if kind == "open" else other
            one = OneCounted(
                counted_end,
                on,
                other,
                correct,
                still_id,
                still_place or "",
                still_id in countable,
            )
        lines.append(
            Shown(
                row,
                item_id,
                item,
                unit,
                words,
                transaction_id,
                kind,
                at,
                from_place,
                to_place if kind != "open" else None,
                MovedWhole(amount(held), amount_words(held, unit, None, None))
                if whole
                else None,
                one,
            )
        )
    return lines


def _settle(
    session: Session, check_id: int, seen_as: str, admin_id: int, how: Settled
) -> OverRecorded:
    """Settle one waiting, still as seen, holding its row. RuleBroken
    as waiting()."""
    row = waiting(session, check_id, seen_as)
    row.settled_at = func.now()
    row.settled_by_admin_id = admin_id
    row.settled_how = how
    return row


def waiting(session: Session, check_id: int, seen_as: str) -> OverRecorded:
    """One still waiting as seen, its row held: taken after its places, as
    answer() and a place brought back for it take them. RuleBroken
    (over_recorded_unchanged) if it was settled or said again meanwhile."""
    row = get_locked(session, OverRecorded, check_id)
    if row is None or row.settled_at is not None or seen(row) != seen_as:
        raise RuleBroken("over_recorded_unchanged")
    return row


def ends(session: Session, check_id: int) -> tuple[int | None, int | None]:
    """Where a check's transaction moved stock from, and to."""
    found = session.execute(
        select(LedgerTransaction.from_location_id, LedgerTransaction.to_location_id)
        .join(Movement, Movement.transaction_id == LedgerTransaction.id)
        .join(OverRecorded, OverRecorded.movement_id == Movement.id)
        .where(OverRecorded.id == check_id)
    ).first()
    return (None, None) if found is None else (found[0], found[1])


def places_of(session: Session, check_id: int) -> list[int]:
    """The places a check's transaction moved stock between, to lock first,
    as saves take places (ledger.lock_places)."""
    return [place for place in ends(session, check_id) if place is not None]


def mark_checked(
    session: Session,
    check_id: int,
    seen_as: str,
    admin_id: int,
    reason: str,
    words: str | None = None,
) -> None:
    """Settle one, as checked, saying why it is right as it is (a key of
    CHECKED_REASONS) and, put right another way, how in `words`: nothing
    written to the ledger. RuleBroken as _settle."""
    row = _settle(session, check_id, seen_as, admin_id, "checked")
    row.checked_reason = reason
    row.checked_words = words
    log.info(
        "over recorded checked",
        over_recorded_id=check_id,
        admin_id=admin_id,
        reason=reason,
    )


def correct(
    session: Session, check_id: int, seen_as: str, admin_id: int
) -> list[ledger.Written]:
    """Settle one, as corrected: an adjustment on each side its transaction
    has, but one a count put right (counted), undoes what was recorded more
    than moved, in the forms it moved in, with the reason REASON. Found
    again where it left, written off where it arrived. Call it holding its
    places (places_of), locked first. Each under a key of the check's, so
    it is written once; with one end counted, a key of its own. RuleBroken
    as _settle, or as ledger.record_adjustment."""
    row = _settle(session, check_id, seen_as, admin_id, "corrected")
    movement = session.get_one(Movement, row.movement_id)
    transaction = session.get_one(LedgerTransaction, movement.transaction_id)
    reason = adjustment_reasons.seeded(session, REASON)
    # The other end only, after a count put one right.
    only = " only" if row.left_count_id or row.arrived_count_id else ""
    sides = (
        (
            None if row.left_count_id else transaction.from_location_id,
            True,
            movement.from_package_id,
            movement.from_open_package_id,
        ),
        (
            None if row.arrived_count_id else transaction.to_location_id,
            False,
            movement.to_package_id,
            movement.to_open_package_id,
        ),
    )
    written: list[ledger.Written] = []
    for place, found, package_id, open_id in sides:
        if place is None:
            continue
        key = uuid.uuid5(
            transaction.idempotency_key,
            f"over recorded {row.id} {'found' if found else 'off'}{only}",
        )
        written += ledger.record_adjustment(
            session,
            key,
            admin_id,
            place,
            movement.item_id,
            row.quantity,
            found=found,
            reason_id=reason.id,
            form=ledger.Form(package_id, open_id),
        )
    log.info("over recorded corrected", over_recorded_id=check_id, admin_id=admin_id)
    return written


def confirm_count(
    session: Session, check_id: int, seen_as: str, admin_id: int, count: Decimal
) -> tuple[Settled, list[ledger.Written]]:
    """Settle one of an open package moved whole (Shown.moved_whole), given
    the count an administrator says it held when it moved: as it was, as
    checked, writing nothing; else as corrected, with an adjustment at the
    place it arrived, in it, for the difference, with the reason REASON,
    under a key of the check's. How it was settled, and what was written.
    Call it holding its places (places_of), locked first. RuleBroken as
    _settle, if it is not such a move (over_recorded_not_whole), or as
    ledger.record_adjustment."""
    row = _settle(session, check_id, seen_as, admin_id, "checked")
    movement = session.get_one(Movement, row.movement_id)
    transaction = session.get_one(LedgerTransaction, movement.transaction_id)
    open_id = movement.to_open_package_id
    if (
        open_id is None
        or open_id != movement.from_open_package_id
        or transaction.to_location_id is None
    ):
        raise RuleBroken("over_recorded_not_whole")
    if count == movement.quantity:
        log.info(
            "over recorded confirmed", over_recorded_id=check_id, admin_id=admin_id
        )
        return "checked", []
    row.settled_how = "corrected"
    written = ledger.record_adjustment(
        session,
        uuid.uuid5(transaction.idempotency_key, f"over recorded {row.id} count"),
        admin_id,
        transaction.to_location_id,
        movement.item_id,
        abs(count - movement.quantity),
        found=count > movement.quantity,
        reason_id=adjustment_reasons.seeded(session, REASON).id,
        form=ledger.Form(movement.to_package_id, open_id),
    )
    log.info(
        "over recorded count corrected", over_recorded_id=check_id, admin_id=admin_id
    )
    return "corrected", written


def openings_waiting(
    session: Session, place_id: int, as_of: int
) -> dict[int, set[int]]:
    """Of those waiting, the openings at a place as of moment `as_of`: by
    item, the packages each opened. Its count asks each such item by form,
    with a line of those packages sealed though the records hold none, so
    the sealed ones really there can be counted and each end put right
    (Ali, 2026-11-15, inventory-tng-he9, inventory-tng-dd8)."""
    found: dict[int, set[int]] = {}
    for item_id, package_id in session.execute(
        select(Movement.item_id, Movement.from_package_id)
        .join(OverRecorded, OverRecorded.movement_id == Movement.id)
        .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
        .where(
            WAITING,
            LedgerTransaction.kind == "open",
            LedgerTransaction.from_location_id == place_id,
            LedgerTransaction.id <= as_of,
        )
    ):
        # An opening leaves sealed (ledger._opening_breaks).
        assert package_id is not None
        found.setdefault(item_id, set()).add(package_id)
    return found


def counted(
    session: Session,
    admin_id: int,
    place_id: int,
    as_of: int,
    written: Sequence[ledger.Written],
    put_right: Collection[tuple[int, ledger.Form | None]],
    emptied: Collection[tuple[int, ledger.Form]] = (),
) -> int:
    """Put right the ends at a place of those waiting that a count there
    has, as of moment `as_of`, after their movement (Ali, 2026-11-15,
    inventory-tng-qs8): `put_right` is what it counted, each item by its
    total (no form) or a form of it (ledger.Counted.put_right), and
    `written` its transactions, the first of which each end keeps. An end
    is put right by its item counted by its total, or by the form it is in
    at that end counted (docs/decisions/0020); an open package moved whole
    has one end, where it arrived, which only that open package's own count
    puts right; an opening's ends, both at one place, only a count of each
    one's form, as a total cannot show the sealed and open split (Ali,
    2026-11-15, inventory-tng-r5z). An open package the count closed
    (`emptied`: used up, or not there) puts right an opening's end that
    arrived in it, as a count of it at 0 would, so Correct writes only the
    sealed end (Ali, 2026-11-15, inventory-tng-he9); a move's it does not.
    One with every end it has put right closes as counted, by the count's
    administrator, then. How many closed.

    Call it in the count's transaction, holding the place (ledger.lock_place):
    it holds the rows it reads, by id, as Correct and Mark checked take a
    check after its places, so none waits on another the wrong way round.
    A send that wrote nothing (sent before) puts nothing right."""
    if not written or not (put_right or emptied):
        return 0
    count_id = written[0].id
    totals = {item_id for item_id, form in put_right if form is None}
    forms = {(item_id, form) for item_id, form in put_right if form is not None}
    query = (
        select(
            OverRecorded,
            Movement,
            LedgerTransaction.kind,
            LedgerTransaction.from_location_id,
            LedgerTransaction.to_location_id,
        )
        .join(Movement, Movement.id == OverRecorded.movement_id)
        .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
        .where(
            WAITING,
            Movement.item_id.in_({item_id for item_id, _ in [*put_right, *emptied]}),
            LedgerTransaction.id <= as_of,
            or_(
                LedgerTransaction.from_location_id == place_id,
                LedgerTransaction.to_location_id == place_id,
            ),
        )
        .order_by(OverRecorded.id)
    )
    found = session.execute(locked(query, of=OverRecorded)).all()
    closed = 0
    for row, movement, kind, left_at, arrived_at in found:
        whole = (
            movement.from_open_package_id is not None
            and movement.from_open_package_id == movement.to_open_package_id
        )
        item_id = movement.item_id
        by_total = item_id in totals and not whole and kind != "open"
        left = ledger.Form(movement.from_package_id, movement.from_open_package_id)
        arrived = ledger.Form(movement.to_package_id, movement.to_open_package_id)
        if (
            left_at == place_id
            and not whole
            and row.left_count_id is None
            and (by_total or (item_id, left) in forms)
        ):
            row.left_count_id = count_id
            _end_counted(row.id, "left", count_id, admin_id)
        if (
            arrived_at == place_id
            and row.arrived_count_id is None
            and (
                by_total
                or (item_id, arrived) in forms
                or (kind == "open" and (item_id, arrived) in emptied)
            )
        ):
            row.arrived_count_id = count_id
            _end_counted(row.id, "arrived", count_id, admin_id)
        # Every end it has put right: one it has not is no end to put right.
        if (left_at is None or whole or row.left_count_id) and (
            arrived_at is None or row.arrived_count_id
        ):
            row.settled_at = func.now()
            row.settled_by_admin_id = admin_id
            row.settled_how = "counted"
            closed += 1
            log.info(
                "over recorded counted",
                over_recorded_id=row.id,
                ledger_transaction_id=count_id,
                admin_id=admin_id,
            )
    return closed


def _end_counted(check_id: int, end: str, count_id: int, admin_id: int) -> None:
    log.info(
        "over recorded end counted",
        over_recorded_id=check_id,
        end=end,
        ledger_transaction_id=count_id,
        admin_id=admin_id,
    )


def measure_counted(closed: int) -> None:
    """Those a count closed, measured once committed."""
    if closed:
        settled_counter.add(closed, {"how": "counted"})
