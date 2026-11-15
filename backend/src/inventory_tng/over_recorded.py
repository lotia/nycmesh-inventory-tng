"""Lines recorded with more than was moved (docs/decisions/0020,
inventory-tng-0gg): a scan page's batch, changed after another tab recorded
it under the same key, is refused (409 key_reused), and what it took off a
line already recorded is kept here, for an administrator to correct. The
ledger never changes, so the volunteer cannot take it back; an
administrator corrects it with adjustments that undo the difference on
each side, or marks it checked, as "Needs your action" on the home page
asks. Every write to the over_recorded table."""

import uuid
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Literal, NamedTuple

import structlog
from opentelemetry import metrics
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from inventory_tng import adjustment_reasons, ledger
from inventory_tng.db import RuleBroken, get_locked
from inventory_tng.models import (
    Item,
    ItemPackage,
    LedgerTransaction,
    Location,
    Movement,
    OverRecorded,
)

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

# The reason the adjustments correcting one give.
REASON = "Recorded more than moved"

Settled = Literal["corrected", "checked"]

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
    description="Lines recorded with more than was moved that an administrator "
    "settled, by how: corrected with adjustments, or marked checked",
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


def measure_settled(how: Settled) -> None:
    """One settled, measured once committed."""
    settled_counter.add(1, {"how": how})


def seen(row: OverRecorded) -> str:
    """What an administrator saw of a check, to settle it only as seen: a
    later batch may say another amount meanwhile."""
    return f"{row.quantity.normalize():f}"


# Where stock left, and where it arrived.
FROM, TO = aliased(Location), aliased(Location)


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


def amount_words(
    quantity: Decimal, unit: str, package: str | None, size: Decimal | None
) -> str:
    """`quantity` of an item as a volunteer counted it: whole packages of
    one, or so much in its unit."""
    if package is not None and size and quantity % size == 0:
        return f"{quantity // size} \N{MULTIPLICATION SIGN} {package}"
    after = "" if unit == "each" else f" {unit}"
    return f"{quantity.normalize():f}{after}"


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
        )
        .join(Movement, Movement.id == OverRecorded.movement_id)
        .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
        .join(Item, Item.id == Movement.item_id)
        .outerjoin(ItemPackage, ItemPackage.id == package_id)
        .outerjoin(FROM, FROM.id == LedgerTransaction.from_location_id)
        .outerjoin(TO, TO.id == LedgerTransaction.to_location_id)
        .where(WAITING, *where)
        .order_by(func.lower(Item.name), Item.id, OverRecorded.id)
    ).all()
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
    ) in found:
        words = amount_words(row.quantity, unit, package, size)
        if from_open is not None and kind != "open":
            words += " from an open package"
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
                MovedWhole(
                    f"{held.normalize():f}", amount_words(held, unit, None, None)
                )
                if from_open is not None and from_open == to_open
                else None,
            )
        )
    return lines


def _settle(
    session: Session, check_id: int, seen_as: str, admin_id: int, how: Settled
) -> OverRecorded:
    """Settle one waiting, still as seen, holding its row. RuleBroken
    (over_recorded_unchanged) if it was settled or said again meanwhile."""
    row = get_locked(session, OverRecorded, check_id)
    if row is None or row.settled_at is not None or seen(row) != seen_as:
        raise RuleBroken("over_recorded_unchanged")
    row.settled_at = func.now()
    row.settled_by_admin_id = admin_id
    row.settled_how = how
    return row


def places_of(session: Session, check_id: int) -> list[int]:
    """The places a check's transaction moved stock between, to lock first,
    as saves take places (ledger.lock_places)."""
    found = session.execute(
        select(LedgerTransaction.from_location_id, LedgerTransaction.to_location_id)
        .join(Movement, Movement.transaction_id == LedgerTransaction.id)
        .join(OverRecorded, OverRecorded.movement_id == Movement.id)
        .where(OverRecorded.id == check_id)
    ).first()
    return [] if found is None else [place for place in found if place is not None]


def mark_checked(session: Session, check_id: int, seen_as: str, admin_id: int) -> None:
    """Settle one, as checked: nothing written. RuleBroken as _settle."""
    _settle(session, check_id, seen_as, admin_id, "checked")
    log.info("over recorded checked", over_recorded_id=check_id, admin_id=admin_id)


def correct(
    session: Session, check_id: int, seen_as: str, admin_id: int
) -> list[ledger.Written]:
    """Settle one, as corrected: an adjustment on each side its transaction
    has undoes what was recorded more than moved, in the forms it moved in,
    with the reason REASON. Found again where it left, written off where it
    arrived. Call it holding its places (places_of), locked first. Each under
    a key of the check's, so it is written once. RuleBroken as _settle, or
    as ledger.record_adjustment."""
    row = _settle(session, check_id, seen_as, admin_id, "corrected")
    movement = session.get_one(Movement, row.movement_id)
    transaction = session.get_one(LedgerTransaction, movement.transaction_id)
    reason = adjustment_reasons.seeded(session, REASON)
    sides = (
        (
            transaction.from_location_id,
            True,
            movement.from_package_id,
            movement.from_open_package_id,
        ),
        (
            transaction.to_location_id,
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
            f"over recorded {row.id} {'found' if found else 'off'}",
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
