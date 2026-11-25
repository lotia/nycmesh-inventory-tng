"""Converting an amount from one length unit to another (Ali, 2026-11-24,
inventory-tng-y50b), in Python and in SQL, by one factor each way, so both
sides convert alike. Each and a length never convert.

A conversion is exact until it is kept: an amount is stored, and shown, to
QUANTITY_SCALE places, rounded half away from zero, as PostgreSQL's
round() does. A sum converts each line exactly and rounds once
(stock/line_amounts.py, at_scale_sql), so no sum drifts by its lines'
roundings.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import Numeric, SQLColumnExpression, case, func, literal

from inventory_tng.models.base import QUANTITY_SCALE, QUANTITY_STEP

# A foot in metres, exactly, by international agreement (1959).
METRES_PER_FOOT = Decimal("0.3048")
# Each length unit in metres: every unit of amount_rules.LENGTHS, which a
# test holds.
IN_METRES = {"m": Decimal(1), "ft": METRES_PER_FOOT}


def convertible(old: str, new: str) -> bool:
    """Whether amounts in `old` convert into `new`, another unit: both are
    lengths."""
    return old != new and old in IN_METRES and new in IN_METRES


def at_scale(amount: Decimal) -> Decimal:
    """`amount` as it is kept: to QUANTITY_SCALE places, half away from
    zero."""
    return amount.quantize(QUANTITY_STEP, rounding=ROUND_HALF_UP)


def at_scale_sql(amount: SQLColumnExpression[Any]) -> SQLColumnExpression[Any]:
    """at_scale() in SQL: PostgreSQL's round() is half away from zero too.
    NULL stays NULL, as a sum of no lines is."""
    return func.round(amount, QUANTITY_SCALE)


def converted(amount: Decimal, unit: str, into: str) -> Decimal:
    """`amount` in `unit` said in `into`, as it is kept (at_scale), as
    converted_kept_sql says it. KeyError for each, which no length
    converts to."""
    if unit == into:
        return at_scale(amount)
    return at_scale(amount * IN_METRES[unit] / IN_METRES[into])


def _in_metres(unit: SQLColumnExpression[str]) -> SQLColumnExpression[Any]:
    return case(
        *(
            (unit == name, literal(metres, Numeric))
            for name, metres in IN_METRES.items()
        )
    )


def converted_sql(
    amount: SQLColumnExpression[Any],
    unit: SQLColumnExpression[str],
    into: SQLColumnExpression[str],
) -> SQLColumnExpression[Any]:
    """converted() in SQL, exact: `amount` in the unit `unit` holds, said
    in the one `into` holds, not rounded. NULL unless both are lengths or
    the same."""
    return case(
        (unit == into, amount),
        else_=amount * _in_metres(unit) / _in_metres(into),
    )


def converted_kept_sql(
    amount: SQLColumnExpression[Any],
    unit: SQLColumnExpression[str],
    into: SQLColumnExpression[str],
) -> SQLColumnExpression[Any]:
    """converted_sql() as it is kept, as converted() rounds it."""
    return at_scale_sql(converted_sql(amount, unit, into))
