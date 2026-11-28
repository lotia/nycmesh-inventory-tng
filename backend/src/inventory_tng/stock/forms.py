"""How the stock pages' other forms are read and checked: an adjustment,
lines recorded with more than was moved, and lengths to check, and what
is wrong, in words, beside the field it is about. Also the hidden fields
of the units and package sizes a stock form showed, the count form's
too, and the words asking again in the new ones (UNITS, SIZES). The handlers in
stock/counts_routes.py, stock/over_recorded_routes.py,
stock/length_checks_routes.py and stock/used_up_routes.py read the
request and pass the form's fields. A place's count form is
stock/count_forms.py's."""

import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy.orm import Session

from inventory_tng.core.phrases import Separator, Words, gettext, lazy_gettext, number
from inventory_tng.models.catalogue import Item
from inventory_tng.models.places import Location
from inventory_tng.models.stock import (
    OVER_RECORDED_WORDS_MAX,
    AdjustmentReason,
)
from inventory_tng.stock import over_recorded, queries, units_shown
from inventory_tng.stock.units_shown import Now, Shown
from inventory_tng.web.form_problems import Problem
from inventory_tng.web.forms import (
    chosen,
    quantity,
    read_amount,
    row_id,
    type_a_number,
    up_to,
)

PICK_A_PLACE = lazy_gettext("Pick a place.")
PICK_AN_ITEM = lazy_gettext("Pick an item.")
HOW_MUCH = lazy_gettext("How much.")
FOUND_OR_OFF = lazy_gettext("Found or written off?")
PICK_A_REASON = lazy_gettext("Pick a reason.")
OUT_OF_DATE = lazy_gettext("This form is out of date: try again.")
# A form for open packages sent with none ticked.
TICK_OPEN_PACKAGES = lazy_gettext("Tick the open packages first.")


# The units and package sizes a form showed its amounts in
# (stock/units_shown.py): "<item id>:<unit> ..." and "<package id>:<size>
# ...", hidden on the form, so a save of one changed since is refused and
# asked again (Ali, 2026-11-23, inventory-tng-57u8).
UNITS, SIZES = "units", "sizes"


def shown_field(shown: Mapping[int, object]) -> str:
    """A hidden field of what a form shows (UNITS, SIZES), by id."""
    return " ".join(f"{row_id}:{value}" for row_id, value in sorted(shown.items()))


def _pairs(typed: str) -> dict[int, str]:
    """A hidden field of what a form showed, read: those not an id and a
    value left out."""
    pairs: dict[int, str] = {}
    for each in typed.split():
        typed_id, _, value = each.partition(":")
        if (found := row_id(typed_id)) is not None and value:
            pairs[found] = value
    return pairs


def shown_in(form: Mapping[str, str]) -> Shown:
    """The units and package sizes a form showed (UNITS, SIZES); a size
    that is no number is not shown."""
    sizes = {
        package_id: size
        for package_id, typed in _pairs(form.get(SIZES, "")).items()
        if (size := read_amount(typed)) is not None
    }
    return Shown(_pairs(form.get(UNITS, "")), sizes)


def _then(said: str, problem: Words | None) -> str:
    """`said`, of what changed since a form was shown, then `problem`,
    anything else wrong with what was typed, so both are fixed at once:
    still the form's own Problem, of its kind, so the page counts it
    (web/form_problems.py)."""
    if problem is None:
        return said
    # Translators: %(changed)s says what changed since the form was shown,
    # such as the item now counted in another unit or the package now
    # holding another size; %(problem)s what else is wrong with what was
    # typed.
    both = gettext("%(changed)s %(problem)s", changed=said, problem=problem)
    return Problem(both, problem.kind) if isinstance(problem, Problem) else both


def now_counted_in(unit: str, problem: Words | None = None) -> str:
    """Beside an amount shown in an item's unit since changed: refused, and
    shown again in `unit`. Then `problem`, anything else wrong with it in
    any unit (web/forms.unit_free_problem), so both are fixed at once
    (inventory-tng-hhlr, inventory-tng-yxd1)."""
    return _then(
        gettext(
            "This item is now counted in %(unit)s. Check the amount and save again.",
            unit=unit,
        ),
        problem,
    )


def now_holds(size: Decimal, unit: str, problem: Words | None = None) -> str:
    """Beside a count of a package shown at a size since changed: refused,
    and shown again at `size`, in its item's `unit`. Then `problem`,
    anything else wrong with it at any size, so both are fixed at once
    (Ali, 2026-11-25, inventory-tng-ehfu)."""
    return _then(
        gettext(
            "This package now holds %(size)s %(unit)s. Check the count and save again.",
            size=number(size),
            unit=unit,
        ),
        problem,
    )


class Adjustment(NamedTuple):
    """An adjustment as its form asks for it: at a place, of an item, how
    much, found or written off ("found" or "off"), why, the key its form
    was shown with, and the unit it showed the item in, its unit when read,
    checked again as it is saved (counts.record_adjustment)."""

    place: Location
    item: Item
    amount: Decimal
    direction: str
    reason: AdjustmentReason
    key: uuid.UUID
    unit: str


def adjustment(
    session: Session,
    form: Mapping[str, str],
    key: uuid.UUID | None,
    *,
    separator: Separator,
) -> tuple[Adjustment | None, dict[str, Words]]:
    """The adjustment the form asks for, its amount in the decimal
    `separator` of the page's boxes (web/decimal_separator.py), unless
    something is wrong, and what is: a form without its `key`
    (sent_forms.form_key) is out of date."""
    problems: dict[str, Words] = {}
    location_id = row_id(form.get("location_id", ""))
    place = session.get(Location, location_id) if location_id is not None else None
    if place is None:
        problems["location_id"] = Problem(PICK_A_PLACE, "not_chosen")
    item = chosen(session, Item, form.get("item_id", ""), current=False)
    if item is None:
        problems["item_id"] = Problem(PICK_AN_ITEM, "not_chosen")
    # Shown in another unit, or in none: asked again in its unit now, which
    # the form shown again shows, whatever else is wrong (units_shown); what
    # is wrong with the amount in any unit is said after, read once so the
    # other separator is counted once.
    unit = shown_in(form).units.get(item.id, "") if item else ""
    amount = quantity(
        form.get("amount", ""),
        positive=True,
        unit=item.unit if item and unit == item.unit else None,
        required=HOW_MUCH,
        separator=separator,
    )
    if isinstance(amount, str):
        problems["amount"] = amount
    if item and unit != item.unit:
        units_shown.refusing("adjustment", Now({item.id: item.unit}, {}))
        problems["amount"] = now_counted_in(
            item.unit, amount if isinstance(amount, str) else None
        )
    direction = form.get("direction")
    if direction not in ("found", "off"):
        problems["direction"] = Problem(FOUND_OR_OFF, "not_chosen")
    reason = chosen(session, AdjustmentReason, form.get("reason_id", ""))
    if reason is None:
        problems["reason_id"] = Problem(PICK_A_REASON, "not_chosen")
    if key is None:
        problems["amount"] = Problem(OUT_OF_DATE, "other")
    if (
        problems
        or not (place and item and reason and key and direction)
        or not isinstance(amount, Decimal)
    ):
        return None, problems
    return Adjustment(place, item, amount, direction, reason, key, unit), problems


# Mark checked with no reason, or put right another way with no words.
SAY_WHY = lazy_gettext("Say why it is right as it is.")
# Words typed with a reason other than another way: asked, never dropped
# (Ali, 2026-11-15, inventory-tng-zgy).
WORDS_FOR_ANOTHER = lazy_gettext(
    "You wrote why, but picked another reason. Pick Put right another way "
    "to keep your words, or clear them."
)


def confirmed_count(
    session: Session, movement_id: int, typed: str, shown: str, *, separator: Separator
) -> Decimal | str | None:
    """The count typed to confirm an open package moved whole by a ledger
    line, in the unit the page showed, the line's, or what is wrong with
    it: shown in another, from a form sent from elsewhere, it is asked
    again (stock/units_shown.py). Its item's unit may be the other length
    now: over_recorded.confirm_count converts the difference into it, under
    the lock it is saved with (inventory-tng-4fjq). Shown in another, it
    also says what is wrong with the number in any unit, read once so the
    other separator is counted once (inventory-tng-fezg)."""
    item_id, unit = queries.item_moved(session, movement_id)
    read = quantity(
        typed,
        positive=False,
        unit=unit if shown == unit else None,
        required=type_a_number(separator),
        separator=separator,
    )
    if shown != unit:
        units_shown.refusing("confirm_count", Now({item_id: unit}, {}))
        return now_counted_in(unit, read if isinstance(read, str) else None)
    return read


def why_problem(reason: str, words: str) -> Problem | None:
    """What is wrong with why one is marked checked, `words` stripped: no
    reason picked, put right another way with no words or too many, or
    words with another reason."""
    if reason not in over_recorded.CHECKED_REASONS:
        return Problem(SAY_WHY, "not_chosen")
    if reason != "other":
        return Problem(WORDS_FOR_ANOTHER, "other") if words else None
    if not words:
        return Problem(SAY_WHY, "required")
    if len(words) > OVER_RECORDED_WORDS_MAX:
        return up_to(OVER_RECORDED_WORDS_MAX)
    return None


def length(
    session: Session, item_id: int, typed: str, *, separator: Separator
) -> Decimal | str | None:
    """What is on an open package of an item, as typed to set its length,
    in the item's unit, or what is wrong with it."""
    unit = session.get_one(Item, item_id).unit
    return quantity(typed, positive=False, unit=unit, separator=separator)


def ticked(boxes: Sequence[str]) -> dict[int, str]:
    """The open packages ticked, each box's id and what was seen of its
    check, "id seen", by id."""
    return {
        open_id: seen
        for each in boxes
        for typed, _, seen in [each.partition(" ")]
        if (open_id := row_id(typed)) is not None
    }
