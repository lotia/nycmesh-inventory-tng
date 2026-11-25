"""What the ledger says is where (docs/decisions/0003): each item's balance
at each place, in each form stock is in there (docs/decisions/0020), and
what it makes of places, items and packages, such as which retired ones
still hold stock. Queries only: they lock nothing and refuse nothing,
which the ledger's rules (stock/ledger.py) do.
"""

from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import (
    ColumnElement,
    Select,
    Subquery,
    any_,
    func,
    or_,
    select,
    union_all,
)
from sqlalchemy.orm import Session

from inventory_tng.catalogue.unit_conversion import at_scale_sql
from inventory_tng.core.db_sessions import by_name, exists_where
from inventory_tng.models.catalogue import Item, ItemPackage
from inventory_tng.models.places import Location
from inventory_tng.models.stock import LedgerTransaction, Movement, OpenPackage
from inventory_tng.stock.line_amounts import in_items_unit


def _sides() -> Subquery:
    """Each side of every line, as stock into a place (+) or out of it (-),
    in the form it is in there, in the item's unit as that side reads
    (line_amounts). Each side is a select of its own, joined by UNION ALL,
    so a filter on a place or an item reaches the indexes on each side,
    instead of summing the whole ledger first."""
    moved = (
        select(Movement.item_id)
        .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
        .join(Item, Item.id == Movement.item_id)
    )
    into = moved.add_columns(
        LedgerTransaction.to_location_id.label("location_id"),
        Movement.to_package_id.label("package_id"),
        Movement.to_open_package_id.label("open_package_id"),
        in_items_unit("to").label("quantity"),
    ).where(LedgerTransaction.to_location_id.is_not(None))
    out = moved.add_columns(
        LedgerTransaction.from_location_id,
        Movement.from_package_id,
        Movement.from_open_package_id,
        -in_items_unit("from"),
    ).where(LedgerTransaction.from_location_id.is_not(None))
    return union_all(into, out).subquery("side")


def _by_form() -> Subquery:
    """In minus out of each item at each place, in each form there, each
    rounded once, as kept (unit_conversion.at_scale_sql)."""
    side = _sides()
    columns = [
        side.c[column]
        for column in ("item_id", "location_id", "package_id", "open_package_id")
    ]
    return (
        select(*columns, at_scale_sql(func.sum(side.c.quantity)).label("quantity"))
        .group_by(*columns)
        .subquery("by_form")
    )


# In minus out, for every item and place stock has been at, by form:
# package_id for sealed packages, open_package_id for an open one, neither
# for loose units (ledger.Form).
by_form = _by_form()
# The same, whatever its form: a place whose stock all left keeps its row,
# at zero. Read as a table: select(balance.c.quantity).where(
# balance.c.item_id == ...). The sum of its forms as by_form keeps them,
# not of every line: two forms each holding a part of a thousandth would
# otherwise make the total differ from its rows (inventory-tng-8s2c).
balance = (
    select(
        by_form.c.item_id,
        by_form.c.location_id,
        func.sum(by_form.c.quantity).label("quantity"),
    )
    .group_by(by_form.c.item_id, by_form.c.location_id)
    .subquery("balance")
)


# Less than nothing at a place: the records are wrong there until it is
# counted (docs/decisions/0003).
SHORT = balance.c.quantity < 0


def _retired(model: type[Item | ItemPackage | Location]) -> ColumnElement[list[int]]:
    """The ids of a model's retired rows, as an array for balance to take:
    PostgreSQL pushes it down to the ledger's indexes, so only their
    balances are summed, however long the ledger."""
    return func.array(
        select(model.id).where(model.retired_at.is_not(None)).scalar_subquery()
    )


def _retired_left(
    summed: Subquery, column: str, model: type[Item | ItemPackage]
) -> Select[tuple[int]]:
    """The ids, in `summed`'s `column`, of `model`'s retired rows with some
    left at a current place."""
    return (
        _left_at_current(select(summed.c[column]), summed)
        .where(summed.c[column] == any_(_retired(model)))
        .distinct()
    )


def _left_at_current[*T](
    query: Select[*T], summed: Subquery, *, at: int | None = None
) -> Select[*T]:
    """`query` of `summed`'s rows with some left at a current place, or at
    the place `at`, even retired."""
    current = Location.retired_at.is_(None)
    if at is not None:
        current = or_(current, Location.id == at)
    return query.join(Location, Location.id == summed.c.location_id).where(
        current, summed.c.quantity > 0
    )


def retired_in_stock() -> Select[tuple[int]]:
    """The ids of retired items with some left at a current place: what
    volunteers may still take."""
    return _retired_left(balance, "item_id", Item)


def retired_not_zero() -> Select[tuple[int]]:
    """The ids of retired items whose balance at a current place is not
    zero, more or less than nothing: what a count may still bring to zero
    there (docs/decisions/0003)."""
    # By form, as holds_stock: sealed and loose that net to nothing are
    # still to be counted.
    return (
        select(by_form.c.item_id)
        .join(Location, Location.id == by_form.c.location_id)
        .where(
            Location.retired_at.is_(None),
            by_form.c.item_id == any_(_retired(Item)),
            by_form.c.quantity != 0,
        )
        .distinct()
    )


def retired_packages_in_stock() -> Select[tuple[int]]:
    """The ids of retired packages with some sealed at a current place:
    what volunteers may still take, as retired_in_stock's items."""
    return _retired_left(by_form, "package_id", ItemPackage)


def sealed_somewhere(
    session: Session, package_ids: Sequence[int], *, at: int | None = None
) -> set[int]:
    """Which of these packages have some sealed at a current place, as
    retired_packages_in_stock reads them, or at the place `at`, even
    retired."""
    query = _left_at_current(select(by_form.c.package_id), by_form, at=at)
    query = query.where(by_form.c.package_id.in_(package_ids)).distinct()
    return set(session.scalars(query))


def sealed_places(session: Session) -> dict[int, dict[int, Decimal]]:
    """How much of each package is sealed at each current place with some,
    in its item's unit, by package then place: where opening its item opens
    that package (docs/decisions/0020)."""
    found: dict[int, dict[int, Decimal]] = {}
    for package_id, location_id, quantity in session.execute(
        _left_at_current(
            select(by_form.c.package_id, by_form.c.location_id, by_form.c.quantity),
            by_form,
        )
        .where(by_form.c.package_id.is_not(None))
        # So a reply for the same stock is the same.
        .order_by(by_form.c.location_id)
    ):
        found.setdefault(package_id, {})[location_id] = quantity
    return found


def retired_holding_stock() -> Select[int]:
    """The ids of retired places the records say hold something, more or
    less than nothing: what a save brought or took after the place was
    retired, for an administrator to count."""
    # By form, as holds_stock.
    return (
        select(by_form.c.location_id)
        .where(
            by_form.c.location_id == any_(_retired(Location)),
            by_form.c.quantity != 0,
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


def holds_stock(session: Session, location_id: int) -> bool:
    """Whether the records say anything is at a place, of any item, more or
    less than nothing."""
    # By form: sealed and loose that net to nothing still hold something.
    return exists_where(
        session, by_form.c.location_id == location_id, by_form.c.quantity != 0
    )


def countable_item() -> ColumnElement[bool]:
    """Items a count or an adjustment may name: current ones, and retired
    ones still to be counted to zero (retired_not_zero; Ali, 2026-11-24)."""
    return or_(Item.retired_at.is_(None), Item.id.in_(retired_not_zero()))


def takeable() -> ColumnElement[bool]:
    """Items volunteers may still pick: current ones, and retired ones with
    some left (retired_in_stock). What the scan page's catalogue holds."""
    return or_(Item.retired_at.is_(None), Item.id.in_(retired_in_stock()))


def takeable_package() -> ColumnElement[bool]:
    """Packages volunteers may still pick: current ones, and retired ones
    with some sealed left (retired_packages_in_stock), taking only."""
    return or_(
        ItemPackage.retired_at.is_(None),
        ItemPackage.id.in_(retired_packages_in_stock()),
    )


def left_on(session: Session, rows: Sequence[OpenPackage]) -> dict[int, Decimal]:
    """What the records say is left on each open package, where it is, by
    id: one query, of their balances alone."""
    if not rows:
        return {}
    where = {row.id: row.location_id for row in rows}
    found = session.execute(
        select(
            by_form.c.open_package_id, by_form.c.location_id, by_form.c.quantity
        ).where(by_form.c.open_package_id.in_(list(where)))
    )
    left = {row.id: Decimal(0) for row in rows}
    for open_id, location_id, quantity in found:
        if where[open_id] == location_id:
            left[open_id] = quantity
    return left
