"""A ledger line's amount as every sum of the ledger reads it: in its
item's unit. Each line records the unit it was entered in (Movement.unit;
Ali, 2026-11-24, inventory-tng-2rlr), which its item's may have changed
from since, between lengths (inventory-tng-y50b): such a line converts as
it is read, and keeps its own unit in the ledger. A line in a unit that
does not convert, each and a length, is refused as it is read, so no sum
adds amounts in two units unawares.

A line converts exactly, and a sum is rounded once, as kept
(unit_conversion.at_scale_sql), so it does not drift by its lines'
roundings: Shelf A's 305 m reads 1000.656 ft.
A line of sealed packages converts by its packages instead: so many, each
its size in its item's unit as kept
(models.catalogue.ItemPackage.size_in_items_unit), as the ledger takes
them since, so packages received before a change leave whole after it.
So an open package's lines may net a part of a thousandth, which its form
rounds away: a place's total sums its forms as kept, not its lines
(balance.balance; inventory-tng-8s2c).
An amount said of a line, as a correction undoing some of it is written,
is read by the same expression (said_in_items_unit; inventory-tng-4fjq).
"""

from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Numeric,
    SQLColumnExpression,
    Text,
    and_,
    case,
    cast,
    func,
    literal,
    select,
)
from sqlalchemy.orm import Session, aliased

from inventory_tng.catalogue.unit_conversion import (
    IN_METRES,
    at_scale_sql,
    converted_kept_sql,
    converted_sql,
)
from inventory_tng.models.catalogue import Item, ItemPackage
from inventory_tng.models.stock import Movement


def _by_packages(
    sealed: ColumnElement[Any], quantity: SQLColumnExpression[Any]
) -> ColumnElement[Any]:
    """A sealed line's amount, `quantity`, in its item's unit: how many
    packages it moves, each its size as kept in the line's unit then, times
    that size as kept in its item's now. Its package is `sealed`, an alias of its
    own, so a query that joins a package for another reason does not
    change which."""
    package = aliased(ItemPackage)
    then = converted_kept_sql(package.size, package.unit, Movement.unit)
    now = converted_kept_sql(package.size, package.unit, Item.unit)
    return (
        select(quantity / then * now)
        .where(package.id == sealed)
        .correlate_except(package)
        .scalar_subquery()
    )


def in_items_unit(
    quantity: SQLColumnExpression[Any] = Movement.quantity,
) -> ColumnElement[Any]:
    """`quantity` of a line, its Movement.quantity unless an amount said
    of it, read in its item's unit, of the Item the query joins to the
    line, exactly: a sum or a line read alone is rounded with
    unit_conversion.at_scale_sql(). A line in a unit that does not convert is an error as it is
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
        (Movement.unit == Item.unit, quantity),
        (and_(lengths, sealed.is_not(None)), _by_packages(sealed, quantity)),
        (lengths, converted_sql(quantity, Movement.unit, Item.unit)),
        else_=cast(refused, Numeric),
    )


def said_in_items_unit(
    session: Session, movement_id: int, amount: Decimal
) -> tuple[Decimal, str]:
    """`amount` of the line `movement_id`, said in the line's unit, as
    in_items_unit() reads the line, kept, and its item's unit. Call it
    holding the item (ledger.hold_across), so its unit cannot change
    before it is written."""
    said, unit = session.execute(
        select(at_scale_sql(in_items_unit(literal(amount, Numeric))), Item.unit)
        .join(Item, Item.id == Movement.item_id)
        .where(Movement.id == movement_id)
    ).one()
    return said, unit
