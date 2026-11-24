"""Values as pages show them, which rules' messages use too. It loads
neither FastAPI nor the templates, so rules may import it
(docs/decisions/0023)."""

from decimal import Decimal

from inventory_tng.catalogue.amount_rules import counted_whole, is_length
from inventory_tng.core.phrases import Separator, gettext, lazy_gettext, number


def amount(value: Decimal | None) -> str:
    """A quantity people read: 1234.500 as "1,234.5" in English and
    "1.234,5" in German, 3.000 as "3"; "1234,5" on a page whose boxes take a
    comma (phrases.number). Every digit it holds after the point but
    trailing zeros, never rounded."""
    return "" if value is None else number(value.normalize())


def plain_amount(value: Decimal | None) -> str:
    """A quantity a page posts back in a field it wrote itself, such as a
    hidden one: 1234.500 as "1234.5", in every locale, as the forms read
    it; and a printed label's, in one style whatever the locale
    (labels/labels.py). A box someone types in takes box_amount."""
    return "" if value is None else format(value.normalize(), "f")


def box_amount(value: Decimal | None, separator: Separator) -> str:
    """A quantity in a number box, to be read back as typed
    (web/forms.read_decimal): plain_amount with the box's decimal
    `separator`, "." or ",", so the box posts back unchanged as valid."""
    return plain_amount(value).replace(".", separator)


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
    `unit`, if it is a length (amount_rules.is_length); else nothing."""
    return str(PRINTED_LENGTH) if is_length(unit) else ""


def times(num: int | Decimal, package: str) -> str:
    """So many of a package: 2, a multiplication sign, "Spool 305 m"."""
    return gettext(
        "%(num)s \N{MULTIPLICATION SIGN} %(package)s",
        num=number(num),
        package=package,
    )


def keyboard(unit: str, whole: bool = False) -> str:
    """The inputmode of a box for an amount of an item in `unit`: the
    number keypad for one counted each, whose amounts are whole
    (amount_rules.whole_enough), or for a `whole` number of something
    else, such as sealed packages; else the keypad with a decimal point."""
    return "numeric" if whole or counted_whole(unit) else "decimal"
