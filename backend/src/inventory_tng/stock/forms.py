"""How the stock pages' other forms are read and checked: an adjustment,
lines recorded with more than was moved, and lengths to check, and what
is wrong, in words, beside the field it is about. The handlers in
stock/counts_routes.py, stock/over_recorded_routes.py,
stock/length_checks_routes.py and stock/used_up_routes.py read the
request and pass the form's fields. A place's count form is
stock/count_forms.py's."""

import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import NamedTuple

from sqlalchemy.orm import Session

from inventory_tng.core.phrases import Words, lazy_gettext
from inventory_tng.models.catalogue import Item
from inventory_tng.models.places import Location
from inventory_tng.models.stock import (
    OVER_RECORDED_WORDS_MAX,
    AdjustmentReason,
)
from inventory_tng.stock import over_recorded, queries
from inventory_tng.web.forms import (
    TYPE_A_NUMBER,
    chosen,
    quantity,
    row_id,
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


class Adjustment(NamedTuple):
    """An adjustment as its form asks for it: at a place, of an item, how
    much, found or written off ("found" or "off"), why, and the key its
    form was shown with."""

    place: Location
    item: Item
    amount: Decimal
    direction: str
    reason: AdjustmentReason
    key: uuid.UUID


def adjustment(
    session: Session, form: Mapping[str, str], key: uuid.UUID | None
) -> tuple[Adjustment | None, dict[str, Words]]:
    """The adjustment the form asks for, unless something is wrong, and
    what is: a form without its `key` (sent_forms.form_key) is out of date."""
    problems: dict[str, Words] = {}
    location_id = row_id(form.get("location_id", ""))
    place = session.get(Location, location_id) if location_id is not None else None
    if place is None:
        problems["location_id"] = PICK_A_PLACE
    item = chosen(session, Item, form.get("item_id", ""), current=False)
    if item is None:
        problems["item_id"] = PICK_AN_ITEM
    amount = quantity(
        form.get("amount", ""),
        positive=True,
        unit=item.unit if item else None,
        required=HOW_MUCH,
    )
    if isinstance(amount, str):
        problems["amount"] = amount
    direction = form.get("direction")
    if direction not in ("found", "off"):
        problems["direction"] = FOUND_OR_OFF
    reason = chosen(session, AdjustmentReason, form.get("reason_id", ""))
    if reason is None:
        problems["reason_id"] = PICK_A_REASON
    if key is None:
        problems["amount"] = OUT_OF_DATE
    if (
        problems
        or not (place and item and reason and key and direction)
        or not isinstance(amount, Decimal)
    ):
        return None, problems
    return Adjustment(place, item, amount, direction, reason, key), problems


# Mark checked with no reason, or put right another way with no words.
SAY_WHY = lazy_gettext("Say why it is right as it is.")
# Words typed with a reason other than another way: asked, never dropped
# (Ali, 2026-11-15, inventory-tng-zgy).
WORDS_FOR_ANOTHER = lazy_gettext(
    "You wrote why, but picked another reason. Pick Put right another way "
    "to keep your words, or clear them."
)


def confirmed_count(
    session: Session, movement_id: int, typed: str
) -> Decimal | str | None:
    """The count typed to confirm an open package moved whole by a ledger
    line, in its item's unit, or what is wrong with it."""
    unit = queries.unit_moved(session, movement_id)
    return quantity(typed, positive=False, unit=unit, required=TYPE_A_NUMBER)


def why_problem(reason: str, words: str) -> Words | None:
    """What is wrong with why one is marked checked, `words` stripped: no
    reason picked, put right another way with no words or too many, or
    words with another reason."""
    if reason not in over_recorded.CHECKED_REASONS:
        return SAY_WHY
    if reason != "other":
        return WORDS_FOR_ANOTHER if words else None
    if not words:
        return SAY_WHY
    if len(words) > OVER_RECORDED_WORDS_MAX:
        return up_to(OVER_RECORDED_WORDS_MAX)
    return None


def length(session: Session, item_id: int, typed: str) -> Decimal | str | None:
    """What is on an open package of an item, as typed to set its length,
    in the item's unit, or what is wrong with it."""
    unit = session.get_one(Item, item_id).unit
    return quantity(typed, positive=False, unit=unit)


def showed(typed: str) -> Decimal | None:
    """What a package showed on the page, as it sent it back, below zero
    too; None if it sent none it could have."""
    try:
        found = Decimal(typed)
    except InvalidOperation:
        return None
    return found if found.is_finite() else None


def ticked(boxes: Sequence[str]) -> dict[int, str]:
    """The open packages ticked, each box's id and what was seen of its
    check, "id seen", by id."""
    return {
        open_id: seen
        for each in boxes
        for typed, _, seen in [each.partition(" ")]
        if (open_id := row_id(typed)) is not None
    }
