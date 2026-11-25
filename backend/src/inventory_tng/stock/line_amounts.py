"""A ledger line's amount as every sum of the ledger reads it: in its
item's unit. Each line records the unit it was entered in (Movement.unit;
Ali, 2026-11-24, inventory-tng-2rlr); a sum adds only lines in their
item's unit now, and the database refuses one in another as it reads it,
so no sum adds amounts in two units unawares. Converting one into its
item's unit is a change of its own (inventory-tng-y50b).
"""

from typing import Any

from sqlalchemy import ColumnElement, Numeric, case, cast, literal

from inventory_tng.models.catalogue import Item
from inventory_tng.models.stock import Movement


def in_items_unit() -> ColumnElement[Any]:
    """Movement.quantity, read in its item's unit, of the Item the query
    joins to the line. A line in another unit is an error as it is read,
    saying both units: those words cast to a number, so no function in the
    database is needed (models' test_no_logic_is_in_the_database). Never
    evaluated for a line in its item's unit, as it reads the line's
    columns."""
    refused = literal("ledger line in ") + Movement.unit + " summed in " + Item.unit
    return case(
        (Movement.unit == Item.unit, Movement.quantity),
        else_=cast(refused, Numeric),
    )
