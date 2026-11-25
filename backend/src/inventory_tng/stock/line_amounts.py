"""A ledger line's amount as every sum of the ledger reads it: in its
item's unit. Each line records the unit it was entered in (Movement.unit;
Ali, 2026-11-24, inventory-tng-2rlr); a sum adds only lines in their
item's unit now, and the database refuses one in another as it reads it,
so no sum adds amounts in two units unawares. Converting one into its
item's unit is a change of its own (inventory-tng-y50b).
"""

from typing import Any

from sqlalchemy import ColumnElement, Numeric, Text, case, cast, literal

from inventory_tng.models.catalogue import Item
from inventory_tng.models.stock import Movement


def in_items_unit() -> ColumnElement[Any]:
    """Movement.quantity, read in its item's unit, of the Item the query
    joins to the line. A line in another unit is an error as it is read,
    saying the line's id and both units: those words cast to a number, a
    DataError, as the database may hold no function to raise one
    (docs/decisions/0016). The
    words are made from the row's columns, so PostgreSQL cannot work them
    out as it plans, only for a line that reaches them: one in its item's
    unit never does. No page answers the error: the ledger writes no such
    line (ledger.insert_transaction, movement_unit), so one means a bug,
    a 500 whose words name only the line and units."""
    refused = (
        literal("ledger line ")
        + cast(Movement.id, Text)
        + " in "
        + Movement.unit
        + " summed in "
        + Item.unit
    )
    return case(
        (Movement.unit == Item.unit, Movement.quantity),
        else_=cast(refused, Numeric),
    )
