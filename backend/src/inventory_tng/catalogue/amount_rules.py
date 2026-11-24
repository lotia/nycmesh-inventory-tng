"""What an item's unit says of its amounts (Ali, 2026-11-23,
inventory-tng-xu6x): counted each, in whole units, or a length; and when
what is left on an open package is a short end.

Every rule asks only which of the two a unit (models.catalogue.UNITS) is,
so each is written here once. The scan page reads the same table
(frontend/src/amount-rules.json, which `python -m devtools
write-amount-rules` writes) the same way (frontend/src/amount-rules.ts),
and both are tested against frontend/src/amount-rules-vectors.json.
Rules that read other rows, or need the catalogue lock, stay in the
locked service code and call these.
"""

from decimal import Decimal
from typing import Any

# Each unit of models.catalogue.UNITS is in exactly one, which a test holds,
# so a new unit is placed on purpose, never taken for a length unnoticed.
COUNTED_WHOLE = ("each",)
LENGTHS = ("m", "ft")


def counted_whole(unit: str) -> bool:
    """Whether an item in `unit` is counted, in whole units, not measured."""
    return unit in COUNTED_WHOLE


def is_length(unit: str) -> bool:
    """Whether an item in `unit` is measured as a length, as cable is."""
    return unit in LENGTHS


def whole_enough(unit: str, amount: Decimal) -> bool:
    """An item counted each moves, and is held, in whole units."""
    return not counted_whole(unit) or amount == amount.to_integral_value()


def is_short_end(left: Decimal, short_below: Decimal | None) -> bool:
    """Whether an open package with `left` on it is a short end: some
    left, but less than its package's too-short-to-use length. Less than
    nothing is the records', not a short end."""
    return short_below is not None and 0 < left < short_below


def table() -> dict[str, Any]:
    """The units of each kind, as the scan page reads them."""
    return {"counted_whole": list(COUNTED_WHOLE), "lengths": list(LENGTHS)}
