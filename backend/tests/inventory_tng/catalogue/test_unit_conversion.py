"""Converting between length units, in Python and SQL alike
(catalogue/unit_conversion.py; Ali, 2026-11-24, inventory-tng-y50b)."""

from decimal import Decimal

import pytest
from sqlalchemy import Numeric, Text, literal, select
from sqlalchemy.orm import Session

from inventory_tng.catalogue.amount_rules import LENGTHS
from inventory_tng.catalogue.unit_conversion import (
    IN_METRES,
    METRES_PER_FOOT,
    converted_kept,
    converted_kept_sql,
    convertible,
)
from inventory_tng.models.catalogue import UNITS

# Amounts and units, with what each is in the other length, as kept.
CONVERTED = [
    (Decimal(305), "m", "ft", Decimal("1000.656")),
    (Decimal(1000), "ft", "m", Decimal("304.800")),
    (Decimal("0.001"), "m", "ft", Decimal("0.003")),
    (Decimal("0.001"), "ft", "m", Decimal("0.000")),
    (Decimal("1.5"), "ft", "m", Decimal("0.457")),
    # Half away from zero, as PostgreSQL rounds, never to even.
    (Decimal("0.0025"), "m", "m", Decimal("0.003")),
    (Decimal("999999999.999"), "ft", "m", Decimal("304800000.000")),
]


def test_every_length_converts_and_nothing_else() -> None:
    assert set(IN_METRES) == set(LENGTHS)
    assert IN_METRES["ft"] is METRES_PER_FOOT
    assert [(old, new) for old in UNITS for new in UNITS if convertible(old, new)] == [
        ("m", "ft"),
        ("ft", "m"),
    ]


@pytest.mark.parametrize(("amount", "unit", "into", "kept"), CONVERTED)
def test_python_and_sql_convert_alike(
    session: Session, amount: Decimal, unit: str, into: str, kept: Decimal
) -> None:
    assert converted_kept(amount, unit, into) == kept
    sql = converted_kept_sql(
        literal(amount, Numeric), literal(unit, Text), literal(into, Text)
    )
    assert session.scalar(select(sql)) == kept


def test_each_does_not_convert() -> None:
    with pytest.raises(KeyError):
        converted_kept(Decimal(1), "each", "m")
