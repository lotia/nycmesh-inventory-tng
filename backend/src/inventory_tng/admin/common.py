"""What the administrator pages share."""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from inventory_tng.pages import templates

ADMIN = "/admin"
NAME_MAX = 200

# The sections, in the order the navigation shows them: (path, label).
SECTIONS: list[tuple[str, str]] = []


def section(path: str, label: str) -> str:
    """Add a section to the navigation; returns its path."""
    SECTIONS.append((path, label))
    return path


_globals = cast(dict[str, Any], templates.env.globals)
_globals["sections"] = SECTIONS
_globals["name_max"] = NAME_MAX


def amount(value: Decimal | None) -> str:
    """12.500 as "12.5", 3.000 as "3": as people write quantities."""
    return "" if value is None else format(value.normalize(), "f")


templates.env.filters["amount"] = amount


def now() -> datetime:
    return datetime.now(UTC)


def name_problem(name: str) -> str | None:
    if not 1 <= len(name) <= NAME_MAX:
        return f"Give it a name of up to {NAME_MAX} characters."
    return None


QUANTITY_PLACES = 3  # numeric(12,3), as models.QUANTITY keeps them.
QUANTITY_MAX = Decimal("999999999.999")


def quantity(text: str, *, positive: bool) -> Decimal | str | None:
    """A quantity typed into a form: None if blank, else a Decimal, or what
    is wrong with it as a str. Up to three decimal places, as the ledger
    keeps; more than zero if `positive`, else zero or more."""
    text = text.strip()
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return "Type a number, such as 12 or 0.5."
    if not value.is_finite() or value > QUANTITY_MAX:
        return "That number is too big."
    if value.as_tuple().exponent < -QUANTITY_PLACES:  # ty: ignore[unsupported-operator]
        return f"Use at most {QUANTITY_PLACES} decimal places."
    if value < 0 or (positive and value == 0):
        return "Use a number above 0." if positive else "Use 0 or more."
    return value
