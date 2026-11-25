"""Changing an item's unit, as items.save does: what fixes it
(unit_fixed), the units it may change to (units_open), and what a change
does to what is recorded in it. While nothing fixes it, its packages and
vendor offers follow it, their numbers as typed (follow). Once fixed, it
changes only between lengths, converting what is recorded once an
administrator confirms it was in the old unit (convert; Ali, 2026-11-24,
inventory-tng-y50b); never between each and a length.

The arithmetic of converting is unit_conversion's; the ledger's lines keep
their unit and convert as they are read (stock/line_amounts.py)."""

from decimal import Decimal
from typing import Literal

import structlog
from opentelemetry import metrics
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from inventory_tng.catalogue.unit_conversion import converted, convertible
from inventory_tng.core.db_errors import enforce
from inventory_tng.core.db_locking import locked
from inventory_tng.core.db_once_committed import once_committed
from inventory_tng.core.db_sessions import exists_where
from inventory_tng.models.base import QUANTITY_MAX, QUANTITY_STEP
from inventory_tng.models.catalogue import UNITS, Item, ItemPackage, VendorOffer
from inventory_tng.models.labels import Label
from inventory_tng.models.stock import Movement, OpenPackage, OverRecorded

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

unit_changes_refused = meter.create_counter(
    "inventory.unit_changes.refused",
    unit="{change}",
    description="Changes to an item's unit refused, by why: what fixes it, "
    "or, between lengths, what stops what is recorded converting",
)
unit_changes_converted = meter.create_counter(
    "inventory.unit_changes.converted",
    unit="{change}",
    description="Changes to an item's unit between lengths that converted "
    "what is recorded, once stock had moved or a label was printed, by the "
    "unit it was in and the one it is in now",
)

# Why an item's unit is fixed, and the rule each refuses a change by.
UnitFixed = Literal["moved", "labelled"]
UNIT_FIXED_RULES: dict[UnitFixed, str] = {
    "moved": "item_unit_fixed",
    "labelled": "item_unit_labelled",
}
# Asked: a change between lengths, the item's unit fixed, that converts
# what is recorded, not yet confirmed as recorded in its unit (Ali,
# 2026-11-24, inventory-tng-y50b).
UNIT_UNCONFIRMED = "item_unit_unconfirmed"
# Refused: that change while a line of the item recorded with more than
# was moved waits, its amounts shown in the line's unit
# (stock/over_recorded.py).
UNIT_CHECKS_WAITING = "item_unit_checks_waiting"
# Refused: that change, an amount it converts too large to keep in the new
# unit, or a package's size or too-short length too small or no longer
# below its size there (QUANTITY_MAX, QUANTITY_STEP).
UNIT_OUT_OF_RANGE = "item_unit_out_of_range"


def unit_fixed(session: Session, item_id: int) -> UnitFixed | None:
    """Why the item's unit can no longer change freely, if it can't: stock
    has moved in it, for good, or a label not revoked has been printed for
    it or one of its packages (a package's label names its item too). Its
    ledger's lines record it, and its labels say what it was then (Ali,
    2026-11-23, inventory-tng-rpud). It may still change between lengths,
    converting what is recorded, once an administrator confirms what was
    recorded was in its unit (convert); never between each and a length.
    A revoked label scans as no label, so it fixes nothing, and revoking
    an item's labels lets a mistaken unit be corrected without converting
    (Ali, 2026-11-23, inventory-tng-37dj)."""
    if exists_where(session, Movement.item_id == item_id):
        return "moved"
    if exists_where(session, Label.item_id == item_id, Label.revoked_at.is_(None)):
        return "labelled"
    return None


def units_open(unit: str, why: UnitFixed | None) -> tuple[str, ...]:
    """The units an item in `unit` may change to, its own first: any, while
    nothing fixes it (`why`, unit_fixed); else only another length, which
    converts. Just its own: fixed."""
    if why is None:
        return (unit, *(each for each in UNITS if each != unit))
    return (unit, *(each for each in UNITS if convertible(unit, each)))


def follow(session: Session, item_id: int, unit: str) -> None:
    """An item's packages and vendor offers, retired ones too, recorded in
    its new unit, their amounts as they were: its unit may change, so no
    stock has moved in it and no label says it (unit_fixed), and what was
    typed was meant in the unit chosen (Ali, 2026-11-23,
    inventory-tng-rpud)."""
    for model in (ItemPackage, VendorOffer):
        query = select(model).where(model.item_id == item_id).order_by(model.id)
        for row in session.scalars(locked(query)):
            row.unit = unit


def convert(
    session: Session,
    item: Item,
    new: str,
    minimum: Decimal | None,
    *,
    why: UnitFixed,
    recorded_in: str | None,
) -> Decimal | None:
    """The item, held FOR UPDATE and its unit fixed (`why`), changed to
    `new`: refused unless both are lengths, or while a check of it waits;
    asked (UNIT_UNCONFIRMED) until `recorded_in`, the administrator's
    answer, says what was recorded was in its unit as it is held; then
    what it records converted (_converted), and `minimum`, its Reorder at
    as typed in its unit, comes back in `new`."""
    if not convertible(item.unit, new):
        _refused(item.id, why, UNIT_FIXED_RULES[why])
    # Refused before it is asked: a line recorded with more than was
    # moved, waiting, is shown in the line's unit. One raised while this
    # runs is converted as it is corrected (stock/over_recorded.py).
    waiting = (
        OverRecorded.movement_id == Movement.id,
        Movement.item_id == item.id,
        OverRecorded.settled_at.is_(None),
    )
    if exists_where(session, *waiting):
        _refused(item.id, "checks_waiting", UNIT_CHECKS_WAITING)
    if recorded_in != item.unit:
        log.info("unit change asked", item_id=item.id, reason=why)
        enforce(UNIT_UNCONFIRMED)
    return _converted(session, item, new, minimum, why)


def _refused(item_id: int, reason: str, rule: str) -> None:
    """A change of the item's unit refused by `rule`, logged and counted
    by `reason`, from a fixed list: UnitFixed's, "checks_waiting" or
    "out_of_range"."""
    log.info("unit change refused", item_id=item_id, reason=reason)
    unit_changes_refused.add(1, {"reason": reason})
    enforce(rule)


def _converted(
    session: Session, item: Item, new: str, minimum: Decimal | None, why: UnitFixed
) -> Decimal | None:
    """What the item records in its unit converted into `new` (Ali,
    2026-11-24, inventory-tng-y50b), holding it FOR UPDATE, then each row
    it changes by id, in the ledger's order (stock/ledger.py): `minimum`,
    its Reorder at as typed in its unit, which comes back converted; what
    is on order; its vendor offers' units per order, retired ones too; and
    what volunteers said is on its open packages. Every open one not
    closed is held, so a report being written of one is converted once it
    is in, never left in the old unit. Its stock converts as the ledger is
    read (stock/line_amounts.py), its lines keeping their unit, and its
    packages keep theirs. Refused (UNIT_OUT_OF_RANGE) if an amount
    converted is too large to keep, or a package would not keep its size
    or too-short length in the new unit."""
    old = item.unit
    offers = select(VendorOffer).where(VendorOffer.item_id == item.id)
    offered = session.scalars(locked(offers.order_by(VendorOffer.id))).all()
    opened = select(OpenPackage).where(
        OpenPackage.item_id == item.id,
        or_(OpenPackage.closed_at.is_(None), OpenPackage.length_reported.is_not(None)),
    )
    held = session.scalars(
        locked(opened.order_by(OpenPackage.id), key_share=True)
    ).all()
    minimum = None if minimum is None else converted(minimum, old, new)
    on_order = None if item.on_order is None else converted(item.on_order, old, new)
    per_order = {
        # Never none at all: so few per order in m is still some.
        offer.id: max(converted(offer.units_per_order, offer.unit, new), QUANTITY_STEP)
        for offer in offered
        if offer.units_per_order is not None
    }
    reported = [row for row in held if row.length_reported is not None]
    lengths = {
        # Set with its unit (its check); narrowed again for the type checker.
        row.id: converted(row.length_reported, row.length_reported_unit or old, new)
        for row in reported
        if row.length_reported is not None
    }
    amounts = [minimum, on_order, *per_order.values(), *lengths.values()]
    # Under the catalogue lock (relock), which every package write takes.
    packages = session.scalars(
        select(ItemPackage).where(ItemPackage.item_id == item.id)
    )
    if any(each is not None and each > QUANTITY_MAX for each in amounts) or not all(
        _kept_in(package, new) for package in packages
    ):
        _refused(item.id, "out_of_range", UNIT_OUT_OF_RANGE)
    item.on_order = on_order
    for offer in offered:
        offer.units_per_order = per_order.get(offer.id)
        offer.unit = new
    for row in reported:
        row.length_reported = lengths[row.id]
        row.length_reported_unit = new
    once_committed(session, unit_changes_converted.add, 1, {"from": old, "to": new})
    once_committed(
        session,
        log.info,
        "item unit converted",
        item_id=item.id,
        reason=why,
        from_unit=old,
        to_unit=new,
        vendor_offer_ids=[offer.id for offer in offered],
        open_package_ids=sorted(lengths),
    )
    return minimum


def _kept_in(package: ItemPackage, unit: str) -> bool:
    """Whether `package`'s size and too-short length, in its own unit, are
    kept in `unit`, its item's once converted: the size from QUANTITY_STEP
    to QUANTITY_MAX, the length no less than QUANTITY_STEP and below the
    size, as their checks hold them in their own."""
    size = converted(package.size, package.unit, unit)
    if not QUANTITY_STEP <= size <= QUANTITY_MAX:
        return False
    if package.short_below is None:
        return True
    return QUANTITY_STEP <= converted(package.short_below, package.unit, unit) < size
