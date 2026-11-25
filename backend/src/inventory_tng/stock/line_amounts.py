"""A ledger line's amount as every sum of the ledger reads it: in its
item's unit. Each line records the unit it was entered in (Movement.unit;
Ali, 2026-11-24, inventory-tng-2rlr), which its item's may have changed
from since, between lengths (inventory-tng-y50b): such a line converts as
it is read, and keeps its own unit in the ledger. A line in a unit that
does not convert, each and a length, is refused as it is read, so no sum
adds amounts in two units unawares.

A line converts exactly, and a sum is rounded once, as kept (kept), so it
does not drift by its lines' roundings: Shelf A's 305 m reads 1000.656 ft.
A line of sealed packages converts by its packages instead: so many, each
its size in its item's unit as kept
(models.catalogue.ItemPackage.size_in_items_unit), as the ledger takes
them since, so packages received before a change leave whole after it.
"""

from typing import Any

from sqlalchemy import (
    ColumnElement,
    Numeric,
    Text,
    and_,
    case,
    cast,
    func,
    literal,
    select,
)
from sqlalchemy.orm import aliased

from inventory_tng.catalogue.unit_conversion import (
    IN_METRES,
    converted_kept_sql,
    converted_sql,
)
from inventory_tng.models.base import QUANTITY_SCALE
from inventory_tng.models.catalogue import Item, ItemPackage
from inventory_tng.models.stock import Movement


def _by_packages(sealed: ColumnElement[Any]) -> ColumnElement[Any]:
    """A sealed line's amount in its item's unit: how many packages it
    moves, each its size as kept in the line's unit then, times that size
    as kept in its item's now. Its package is `sealed`, an alias of its
    own, so a query that joins a package for another reason does not
    change which."""
    package = aliased(ItemPackage)
    then = converted_kept_sql(package.size, package.unit, Movement.unit)
    now = converted_kept_sql(package.size, package.unit, Item.unit)
    return (
        select(Movement.quantity / then * now)
        .where(package.id == sealed)
        .correlate_except(package)
        .scalar_subquery()
    )


def in_items_unit() -> ColumnElement[Any]:
    """Movement.quantity, read in its item's unit, of the Item the query
    joins to the line, exactly: a sum or a line read alone is rounded with
    kept(). A line in a unit that does not convert is an error as it is
    read, saying the line's id and both units: those words cast to a
    number, a DataError, as the database may hold no function to raise one
    (docs/decisions/0016). The words are made from the row's columns, so
    PostgreSQL cannot work them out as it plans, only for a line that
    reaches them: one in its item's unit or a length never does. No page
    answers the error: the ledger writes no line in another unit than its
    item's (ledger.insert_transaction, movement_unit), and an item's unit
    changes from each to a length only while it has none, so one means a
    bug, a 500 whose words name only the line and units."""
    refused = (
        literal("ledger line ")
        + cast(Movement.id, Text)
        + " in "
        + Movement.unit
        + " summed in "
        + Item.unit
    )
    lengths = and_(Movement.unit.in_(list(IN_METRES)), Item.unit.in_(list(IN_METRES)))
    sealed = func.coalesce(Movement.from_package_id, Movement.to_package_id)
    return case(
        (Movement.unit == Item.unit, Movement.quantity),
        (and_(lengths, sealed.is_not(None)), _by_packages(sealed)),
        (lengths, converted_sql(Movement.quantity, Movement.unit, Item.unit)),
        else_=cast(refused, Numeric),
    )


def kept(amount: ColumnElement[Any]) -> ColumnElement[Any]:
    """A sum of in_items_unit(), or one line of it, as amounts are kept:
    to QUANTITY_SCALE places (unit_conversion.converted). NULL stays NULL,
    as a sum of no lines is."""
    return func.round(amount, QUANTITY_SCALE)
