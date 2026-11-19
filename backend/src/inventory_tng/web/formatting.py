"""Values as pages show them, which rules' messages use too. It loads
neither FastAPI nor the templates, so rules may import it
(docs/decisions/0023)."""

from decimal import Decimal

from inventory_tng.core.phrases import lazy_gettext, number


def amount(value: Decimal | None) -> str:
    """A quantity people read: 1234.500 as "1,234.5" in English and
    "1.234,5" in German, 3.000 as "3". Every digit it holds after the point
    but trailing zeros, never rounded."""
    return "" if value is None else number(value.normalize())


def plain_amount(value: Decimal | None) -> str:
    """A quantity a form posts back, in a box's value or a hidden field:
    1234.500 as "1234.5", in every locale, as the forms read it."""
    return "" if value is None else format(value.normalize(), "f")


# One wording, wherever what is on an open package is typed: the count
# page, the length checks and the scan page
# (frontend/src/scan/OpenPackageCard.tsx), which a test keeps the same
# (docs/decisions/0020, 2026-11-12; inventory-tng-pg7).
PRINTED_LENGTH = lazy_gettext(
    "Read the length printed on the cable where it leaves the spool. If it "
    "counts up from the start, what's left is the package size minus the mark."
)


def printed_length(unit: str) -> str:
    """How to read the length printed on an open package of an item in
    `unit`, if it is a length (any but each, models.catalogue.UNITS); else
    nothing."""
    return str(PRINTED_LENGTH) if unit != "each" else ""
