"""How every page's forms read and check what was typed: versions, ids,
names and quantities, and what is wrong with them in words, as are the
catalogue's rules a change breaks. It loads neither FastAPI nor the
templates, so each feature's forms.py may import it
(docs/decisions/0023)."""

import re
import unicodedata
from decimal import Decimal
from enum import StrEnum
from typing import Final, Literal

import structlog
from opentelemetry import metrics
from sqlalchemy.orm import Session

from inventory_tng.catalogue.amount_rules import whole_enough
from inventory_tng.core.db_base import Base
from inventory_tng.core.phrases import Separator, Words, gettext, lazy_gettext, number
from inventory_tng.models.base import (
    INTEGER_MAX,
    QUANTITY_PRECISION,
    QUANTITY_SCALE,
    whole,
)
from inventory_tng.models.names import canonical

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

NAME_MAX = 200

WHOLE_UNITS = lazy_gettext("This item is counted in whole units.")
PICK_A_ZONE = lazy_gettext("Pick a zone from the list.")
# The rules domain functions check, by the name db_errors.attempt gives one
# that a change broke (db_errors.RuleBroken's), in words. A page passes its own
# words for "unique", which depend on what it is naming.
RULES = {
    "location_area_parent": lazy_gettext("An area is part of a store or another area."),
    "location_no_cycle": lazy_gettext("That would make it part of itself."),
    "location_parent_current": lazy_gettext(
        "Its place is retired, or places are still "
        "part of it: bring that back, or move those, first."
    ),
    "location_custody_volunteer": lazy_gettext("Stop them holding stock first."),
    "location_holds_stock": lazy_gettext(
        "Move what is here elsewhere, or count it, first."
    ),
    "volunteer_merge_target": lazy_gettext(
        "Others are merged into them: merge those "
        "elsewhere first, or pick a current volunteer to merge into."
    ),
    "item_category_current": lazy_gettext(
        "Its category is retired: bring that back, or pick another, first."
    ),
    "item_unit_fixed": lazy_gettext("Its unit is fixed, as stock has moved in it."),
    "item_unit_labelled": lazy_gettext(
        "Its unit is fixed, as a label has been printed for it."
    ),
    "item_unit_checks_waiting": lazy_gettext(
        "Settle its lines in Recorded more than moved first: they are in its unit now."
    ),
    "package_size_fixed": lazy_gettext(
        "Its size is fixed, as some has moved or been opened: "
        "add a package of the new size instead."
    ),
    "package_size_labelled": lazy_gettext(
        "Its size is fixed, as a label has been printed for it: "
        "revoke its labels to change it."
    ),
    "ck_item_package_short_below_under_size": lazy_gettext(
        "Larger than its too-short length, or change that first."
    ),
    "category_has_items": lazy_gettext("Move or retire its items first."),
    "site_settings_time_zone": PICK_A_ZONE,
    "location_time_zone": PICK_A_ZONE,
    "ck_item_minimum_whole": WHOLE_UNITS,
    "movement_whole_units": WHOLE_UNITS,
    "package_whole_units": WHOLE_UNITS,
    "open_package_untracked": lazy_gettext(
        "That package isn't followed once opened any "
        "more: count what is on it as loose."
    ),
    "movement_item_once": lazy_gettext("An item is on the count twice: count it once."),
    "movement_open_package_closed": lazy_gettext(
        "An open package was closed meanwhile: count what is on it as loose."
    ),
    "location_countable": lazy_gettext(
        "That place is retired and holds nothing: pick another."
    ),
    "changed_since_shown": lazy_gettext(
        "Not saved: someone else saved this after you opened "
        "it. Here it is as it is now: make your change again."
    ),
    "retired_read_only": lazy_gettext(
        "Not saved: it's retired. Bring it back first to change it."
    ),
}
BREAKS_A_RULE = lazy_gettext("That breaks one of the inventory's rules.")
# A save refused as its row changed since its form was shown
# (db_locking.unchanged): the page shows it again as it is now, with RULES'
# words as an alert.
CHANGED = "changed_since_shown"
# A change to a retired row, which is read only until brought back
# (docs/decisions/0019): said the same way.
RETIRED = "retired_read_only"
# The refusals said as an alert above the page, not beside a field.
ALERTS = frozenset({CHANGED, RETIRED})


def rule_words(broken: str, unique: Words | None = None) -> Words:
    """RULES' words for a rule broken; `unique`'s for a unique index, where
    a page can say which."""
    if broken == "unique" and unique is not None:
        return unique
    return RULES.get(broken, BREAKS_A_RULE)


def form_version(typed: str) -> int:
    """The version of its row an edit form showed (models.base.Versioned), or
    0, which no row has, so a form sent without one is refused as changed."""
    return whole(typed, 0, INTEGER_MAX) or 0


def row_id(typed: str) -> int | None:
    """The id a form sent, or None if it is not one."""
    return whole(typed, 1, INTEGER_MAX)


def chosen[T: Base](
    session: Session, model: type[T], typed: str, *, current: bool = True
) -> T | None:
    """The row a form's select sent the id of: None if it sent none, there
    is no such row, or (if `current`) it is retired."""
    ident = row_id(typed)
    row = session.get(model, ident) if ident is not None else None
    if row is None or (current and getattr(row, "retired_at", None) is not None):
        return None
    return row


def up_to(limit: int) -> str:
    """A field's longest, refused: in characters."""
    return gettext("Up to %(max)s characters.", max=number(limit))


def name_up_to(limit: int) -> str:
    """A name too long, refused: in characters."""
    return gettext("Give it a name of up to %(max)s characters.", max=number(limit))


def at_most_places(places: int) -> str:
    """A quantity typed too finely, refused."""
    return gettext("Use at most %(digits)s decimal places.", digits=number(places))


def name_problem(name: str) -> str | None:
    """What is wrong with a name, stripped: blank, or too long."""
    # As stored (models.names.CanonicalName): °C for ℃ is a character longer.
    if not 1 <= len(canonical(name)) <= NAME_MAX:
        return name_up_to(NAME_MAX)
    return None


TOO_BIG = lazy_gettext("That number is too big.")
ABOVE_ZERO = lazy_gettext("Use a number above 0.")
ZERO_OR_MORE = lazy_gettext("Use 0 or more.")
# The largest amount models.base.QUANTITY holds: every digit a 9.
QUANTITY_MAX: Final = Decimal(10**QUANTITY_PRECISION - 1).scaleb(-QUANTITY_SCALE)


class Unreadable(StrEnum):
    """Why what was typed in a number box is not an amount: not a number;
    written with the other decimal separator than the box's; that
    separator followed by three digits after a whole part above 0, as
    thousands are grouped; or more decimal places than the ledger keeps
    (QUANTITY_SCALE).
    frontend/src/shared/quantity.ts says the same of the scan page's boxes,
    and both are tested against frontend/src/shared/quantity-cases.json."""

    NUMBER = "number"
    SEPARATOR = "separator"
    THOUSANDS = "thousands"
    PLACES = "places"


# The Arabic decimal sign, read as the box's own separator, as digits of any
# script are read as 0-9 (Ali, 2026-11-23, inventory-tng-qnwz).
ARABIC_DECIMAL = "\N{ARABIC DECIMAL SEPARATOR}"
_DIGITS = re.compile("[0-9]*")


def _split(plain: str, separator: str) -> tuple[bool, str, str] | None:
    """`plain` as a sign, whole digits and decimals, with `separator`
    between them; None if it is not so."""
    negative = plain.startswith("-")
    units, _, fraction = plain.removeprefix("-").partition(separator)
    if not (units or fraction) or not all(
        _DIGITS.fullmatch(part) for part in (units, fraction)
    ):
        return None
    return negative, units, fraction


def read_decimal(text: str, separator: Separator = ".") -> Decimal | Unreadable:
    """A number typed in a box whose decimal separator is `separator`, "."
    or ",": digits, of any script, with that separator and up to
    QUANTITY_SCALE decimals after it, and a "-" before them if below
    zero. Neither grouping, an exponent, "+" nor "_"; -0 reads as 0. With
    the default it reads a field the page wrote itself (plain_amount)."""
    other = "," if separator == "." else "."
    plain = "".join(
        str(unicodedata.decimal(char))
        if char.isdecimal()
        else separator
        if char == ARABIC_DECIMAL
        else char
        for char in text.strip()
    )
    parts = _split(plain, separator)
    if parts is None:
        mistaken = _split(plain, other)
        if mistaken is None:
            return Unreadable.NUMBER
        _, units, fraction = mistaken
        # A whole part of 0, or none, is no thousands, so a decimal
        # (Ali, 2026-11-23, inventory-tng-mat2).
        if units.strip("0") and len(fraction) == 3:
            return Unreadable.THOUSANDS
        return Unreadable.SEPARATOR
    negative, units, fraction = parts
    if len(fraction) > QUANTITY_SCALE:
        return Unreadable.PLACES
    value = Decimal(f"{units or 0}.{fraction}" if fraction else units)
    return -value if negative else value


def read_amount(text: str, separator: Separator = ".") -> Decimal | None:
    """read_decimal's number, or None if it is none."""
    value = read_decimal(text, separator)
    return value if isinstance(value, Decimal) else None


def type_a_number(separator: Separator = ".") -> str:
    """Something that is no number typed in a box whose decimal separator
    is `separator`, refused, with a number such as it takes."""
    return gettext(
        "Type a number, such as 12 or %(example)s.", example=f"0{separator}5"
    )


# What a box refused as written with the decimal separator its locale does
# not use says, the one it does use named (Ali, 2026-11-23,
# inventory-tng-qnwz); and the other followed by three digits after a
# whole part above 0, as thousands are grouped, which naming the decimal
# one would turn into a thousandth of what was meant.
THOUSANDS = lazy_gettext("Type thousands without a separator: 1000.")


def use_separator(separator: Separator) -> str:
    """A number typed with the other decimal separator than its box's,
    `separator`, refused."""
    return gettext(
        "Use “%(separator)s” for decimals, such as %(example)s.",
        separator=separator,
        example=f"0{separator}5",
    )


# Which a box refused as its locale does not write decimals so: a point,
# a comma, or either read as thousands (Unreadable). The counter's
# attribute, from this list only; the scan page reports its own
# (scan/page_telemetry_api).
OtherSeparator = Literal["point", "comma", "thousands"]

other_separators = meter.create_counter(
    "inventory.numbers.other_separator",
    unit="{refusal}",
    description="Numbers typed in a box with the decimal separator its locale "
    "does not use, refused, by which: a point, a comma, or either followed by "
    "three digits after a whole part above 0, as thousands are grouped",
)


def other_separator(refused: Unreadable, separator: Separator) -> OtherSeparator | None:
    """Which other decimal separator than a box's, `separator`, it was
    refused for, if that is why."""
    if refused is Unreadable.THOUSANDS:
        return "thousands"
    if refused is Unreadable.SEPARATOR:
        return "comma" if separator == "." else "point"
    return None


def refused_words(refused: Unreadable, separator: Separator) -> str:
    """What a box whose decimal separator is `separator` says of what it
    could not read, refused for `refused`."""
    if refused is Unreadable.PLACES:
        return at_most_places(QUANTITY_SCALE)
    if refused is Unreadable.THOUSANDS:
        return str(THOUSANDS)
    if refused is Unreadable.SEPARATOR:
        return use_separator(separator)
    return type_a_number(separator)


def quantity(
    text: str,
    *,
    positive: bool,
    unit: str | None = None,
    required: Words | None = None,
    separator: Separator,
) -> Decimal | str | None:
    """A quantity typed into a form's box whose decimal separator is
    `separator` (read_decimal): a Decimal, or what is wrong with it as a
    str (refused_words), one typed with the other separator counted. Blank
    is None, or `required` if one must be given. Up to QUANTITY_SCALE decimal
    places, as the ledger keeps; more than zero if `positive`, else zero or
    more; whole, for an item in `unit` counted each (whole_enough)."""
    text = text.strip()
    if not text:
        return None if required is None else str(required)
    value = read_decimal(text, separator)
    if isinstance(value, Unreadable):
        if (which := other_separator(value, separator)) is not None:
            other_separators.add(1, {"separator": which})
            log.info("number refused", separator=which)
        return refused_words(value, separator)
    if value > QUANTITY_MAX:
        return str(TOO_BIG)
    if value < 0 or (positive and value == 0):
        return str(ABOVE_ZERO if positive else ZERO_OR_MORE)
    if unit is not None and not whole_enough(unit, value):
        return str(WHOLE_UNITS)
    return value
