"""How the counts pages' forms are read and checked: a place's count, its
lines by place, by item and by form, and an adjustment, and what is wrong, in
words, beside the field it is about; and those of lines recorded with
more than was moved, and of lengths to check. The handlers in
stock/counts_routes.py, stock/over_recorded_routes.py and
stock/length_checks_routes.py read the request and pass the form's
fields."""

import uuid
from collections.abc import Collection, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import NamedTuple

from sqlalchemy.orm import Session

from inventory_tng.core.phrases import Words, lazy_gettext
from inventory_tng.models.base import INTEGER_MAX, whole
from inventory_tng.models.catalogue import Item, ItemPackage
from inventory_tng.models.places import Location
from inventory_tng.models.stock import (
    OVER_RECORDED_WORDS_MAX,
    AdjustmentReason,
    OpenPackage,
)
from inventory_tng.stock import over_recorded, queries
from inventory_tng.stock.adjustment_reasons import LOST, USED_UP
from inventory_tng.stock.counts import ItemLines
from inventory_tng.stock.ledger import LOOSE, Form, repeated
from inventory_tng.web.forms import (
    QUANTITY_MAX,
    RULES,
    TYPE_A_NUMBER,
    chosen,
    quantity,
    row_id,
    up_to,
)

# The count form's fields of items: counted, by item, and one chosen at
# the foot.
COUNTED = "counted_"
EXTRA = "extra_quantity"
# An open package closed while it was counted: a ledger rule a race can
# break past the page's checks, said above the form as rules are.
CLOSED_MEANWHILE = "movement_open_package_closed"
ON_THE_FORM_TWICE = lazy_gettext("This item is on the form more than once.")
NO_SUCH_PACKAGE = lazy_gettext("No such package.")
NO_SUCH_ITEM = lazy_gettext("No such item.")
NO_SUCH_OPEN_PACKAGE = lazy_gettext("No such open package here.")
WHOLE_PACKAGES = lazy_gettext("A whole number of packages.")
TOO_MANY = lazy_gettext("That is more than the inventory can count.")
HOW_MUCH_LEFT = lazy_gettext("How much is left on it.")
PICK_A_PLACE = lazy_gettext("Pick a place.")
PICK_AN_ITEM = lazy_gettext("Pick an item.")
HOW_MUCH = lazy_gettext("How much.")
FOUND_OR_OFF = lazy_gettext("Found or written off?")
PICK_A_REASON = lazy_gettext("Pick a reason.")
OUT_OF_DATE = lazy_gettext("This form is out of date: try again.")
# A form for open packages sent with none ticked.
TICK_OPEN_PACKAGES = lazy_gettext("Tick the open packages first.")

# The count form's fields of forms: sealed packages, by item and package;
# an open package, by its id, and what became of it; loose units, by item;
# and an open package nobody recorded.
SEALED, OPENED, STATE, LOOSE_FIELD = "sealed_", "opened_", "state_", "loose_"
FOUND_PACKAGE, FOUND_LEFT = "found_package_id", "found_left"
# What became of an open package counted: here, with what is left on it,
# or closed, as used up or not there.
STATES = {"here": None, "used_up": USED_UP, "missing": LOST}


# A field of the count form: its name, the item it names (None for no
# item), and what was typed for it.
Line = tuple[str, int | None, str]


def _item_id(typed: str) -> int | None:
    """The item named by digits, however many zeros lead them."""
    return row_id(typed.lstrip("0"))


def lines(form: Mapping[str, str]) -> list[Line]:
    """The count form's lines. An item chosen at the foot with an amount is
    one more, shown at its amount; one with none is not counted."""
    found = [
        (field, _item_id(field.removeprefix(COUNTED)), typed)
        for field, typed in form.items()
        if field.startswith(COUNTED)
    ]
    extra, typed = form.get("extra_item_id", ""), form.get(EXTRA, "")
    if extra and typed.strip():
        found.append((EXTRA, _item_id(extra), typed))
    return found


def named_twice(found: Sequence[Line]) -> dict[str, int | None]:
    """The fields naming an item another field names too (ledger.repeated),
    and that item."""
    return {
        found[number][0]: found[number][1]
        for number in repeated([item_id for _, item_id, _ in found])
    }


class ByForm(NamedTuple):
    """What a count form says of forms: lines of an item, a form and how
    much; open packages to close, each with the reason; an open package
    nobody recorded, its package and what is left on it."""

    lines: list[tuple[int, Form, Decimal]]
    close: list[tuple[int, str]]
    found: tuple[ItemPackage, Decimal] | None


def by_form(
    session: Session, form: Mapping[str, str], place_id: int
) -> tuple[ByForm, dict[str, Words]]:
    """The count form's forms (stock/counts_routes.py's _forms), as lines, and
    what is wrong."""
    problems: dict[str, Words] = {}
    lines: list[tuple[int, Form, Decimal]] = []
    close: list[tuple[int, str]] = []
    item_ids = [
        item_id
        for field in form
        if field.startswith(LOOSE_FIELD)
        and (item_id := row_id(field.removeprefix(LOOSE_FIELD))) is not None
    ]
    items = {item.id: item for item in queries.items_with_ids(session, item_ids)}
    for field, typed in form.items():
        state = form.get(f"{STATE}{field.removeprefix(OPENED)}", "here")
        # A blank line is not counted, but one said used up or not here is
        # closed, whatever is typed.
        if not typed.strip() and not (field.startswith(OPENED) and state != "here"):
            continue
        if field.startswith(SEALED):
            item_part, _, package_part = field.removeprefix(SEALED).partition("_")
            package = session.get(ItemPackage, row_id(package_part) or 0)
            count = whole(typed, 0, INTEGER_MAX)
            if package is None or package.item_id != row_id(item_part):
                problems[field] = NO_SUCH_PACKAGE
            elif count is None:
                problems[field] = WHOLE_PACKAGES
            elif count * package.size > QUANTITY_MAX:
                problems[field] = TOO_MANY
            else:
                lines.append(
                    (package.item_id, Form(package_id=package.id), count * package.size)
                )
        elif field.startswith(LOOSE_FIELD):
            item = items.get(row_id(field.removeprefix(LOOSE_FIELD)) or 0)
            if item is None:
                problems[field] = NO_SUCH_ITEM
                continue
            amount = quantity(typed, positive=False, unit=item.unit)
            if isinstance(amount, str):
                problems[field] = amount
            else:
                assert amount is not None  # Typed, so not blank.
                lines.append((item.id, LOOSE, amount))
        elif field.startswith(OPENED):
            row = session.get(OpenPackage, row_id(field.removeprefix(OPENED)) or 0)
            unit = session.get_one(Item, row.item_id).unit if row else "m"
            amount = quantity(typed, positive=False, unit=unit)
            if row is None or row.location_id != place_id or state not in STATES:
                problems[field] = NO_SUCH_OPEN_PACKAGE
            elif row.closed_at is not None:
                # Its line is gone from the form, so said above it.
                problems[CLOSED_MEANWHILE] = RULES[CLOSED_MEANWHILE]
            elif (reason := STATES[state]) is not None:
                close.append((row.id, reason))
            elif isinstance(amount, str):
                problems[field] = amount
            else:
                assert amount is not None  # Typed, so not blank.
                lines.append((row.item_id, Form(open_package_id=row.id), amount))
    found = None
    if form.get(FOUND_PACKAGE, "").strip():
        package = session.get(ItemPackage, row_id(form[FOUND_PACKAGE]) or 0)
        unit = session.get_one(Item, package.item_id).unit if package else "m"
        left = quantity(
            form.get(FOUND_LEFT, ""),
            positive=True,
            unit=unit,
            required=HOW_MUCH_LEFT,
        )
        if package is None:
            problems[FOUND_PACKAGE] = NO_SUCH_PACKAGE
        elif isinstance(left, str):
            problems[FOUND_LEFT] = left
        else:
            assert left is not None  # Required, so a problem if blank.
            found = (package, left)
    return ByForm(lines, close, found), problems


# A count of a store and its areas on one form (inventory-tng-8i4.51.4):
# an area's fields are named at_<its id>_<field>; the page's own place's
# have no prefix, as on a count of one place. The form lists every place
# it shows, so one with nothing on it is counted too.
AT, PLACES, ITEMS = "at_", "places", "items"
# Something else here and an open package nobody recorded are asked once
# for the whole form, each with the place it was found in beside it.
EXTRA_PLACE, FOUND_PLACE = "extra_location_id", "found_location_id"
PICKED = {
    EXTRA_PLACE: ("extra_item_id", EXTRA),
    FOUND_PLACE: (FOUND_PACKAGE, FOUND_LEFT),
}
# Fields said once, wherever their place: never prefixed.
SHARED = {*PICKED, *(name for names in PICKED.values() for name in names)}


def field_at(root_id: int, place_id: int, name: str) -> str:
    """The count form's name for a field of a place on the count of
    `root_id` (AT)."""
    if place_id == root_id or name in SHARED or name == CLOSED_MEANWHILE:
        return name
    return f"{AT}{place_id}_{name}"


def places_listed(form: Mapping[str, str], root_id: int) -> list[int] | None:
    """The places a count form shows (PLACES), by id; just its own, on a
    form that lists none; None if one listed is no id at all."""
    listed = form.get(PLACES, "").split() or [str(root_id)]
    ids = [row_id(typed) for typed in listed]
    if any(place_id is None for place_id in ids):
        return None
    return sorted({place_id for place_id in ids if place_id is not None})


def by_place(
    form: Mapping[str, str], root_id: int, place_ids: Collection[int]
) -> tuple[dict[int, dict[str, str]], dict[str, Words]]:
    """A count form's fields, place by place, each unprefixed (AT): those
    of a place it does not list are dropped. Something else here and an
    open package nobody recorded go to the place picked beside them, the
    page's own if none is; and what is wrong: one picked it does not list."""
    split: dict[int, dict[str, str]] = {place_id: {} for place_id in place_ids}
    for field, typed in form.items():
        if field in SHARED:
            continue
        place_id: int | None = root_id
        name = field
        if field.startswith(AT):
            number, _, name = field.removeprefix(AT).partition("_")
            place_id = row_id(number)
        if place_id in split:
            split[place_id][name] = typed
    problems: dict[str, Words] = {}
    for picker, names in PICKED.items():
        typed = form.get(picker, "")
        place_id = row_id(typed) if typed else root_id
        if place_id in split:
            split[place_id] |= {name: form[name] for name in names if name in form}
        elif any(form.get(name, "").strip() for name in names):
            problems[picker] = PICK_A_PLACE
    return split, problems


class PlaceCount(NamedTuple):
    """What a count form says of one place: its lines as typed, those
    counted by total, those by form, and, on a recount, the items it asks
    for again."""

    found: list[Line]
    counted: ItemLines
    forms: ByForm
    items: set[int]

    @property
    def anything(self) -> bool:
        """Whether it says anything of the place."""
        forms = self.forms
        return bool(self.counted or forms.lines or forms.close or forms.found)

    def typed(self) -> dict[int, Decimal]:
        """What was typed of each item: its total, or its forms', an open
        package nobody recorded among them."""
        typed = dict(self.counted)
        found = (
            [(self.forms.found[0].item_id, LOOSE, self.forms.found[1])]
            if self.forms.found
            else []
        )
        for item_id, _, amount in [*self.forms.lines, *found]:
            typed[item_id] = typed.get(item_id, Decimal(0)) + amount
        return typed


class CountForm(NamedTuple):
    """A count form read: each place's count, by id; what is wrong, by the
    form's own field names; and the fields naming an item twice there, by
    those names too, and that item."""

    places: dict[int, PlaceCount]
    problems: dict[str, Words]
    twice: dict[str, int | None]


def read_count(
    session: Session, form: Mapping[str, str], root_id: int, place_ids: list[int]
) -> CountForm:
    """A count form, of the places `place_ids` (places_listed), read place
    by place (by_place) and checked."""
    split, problems = by_place(form, root_id, place_ids)
    places: dict[int, PlaceCount] = {}
    twice: dict[str, int | None] = {}
    for place_id, fields in split.items():
        found = lines(fields)
        counted, wrong = item_counts(session, found)
        forms, form_wrong = by_form(session, fields, place_id)
        items = {
            item_id
            for typed in fields.get(ITEMS, "").split()
            if (item_id := row_id(typed)) is not None
        }
        places[place_id] = PlaceCount(found, counted, forms, items)
        named = {
            field_at(root_id, place_id, field): item_id
            for field, item_id in named_twice(found).items()
        }
        twice |= named
        problems |= {
            field_at(root_id, place_id, field): words
            for field, words in (wrong | form_wrong).items()
        }
        problems |= dict.fromkeys(named, ON_THE_FORM_TWICE)
    return CountForm(places, problems, twice)


def item_counts(
    session: Session, found: Collection[Line]
) -> tuple[ItemLines, dict[str, Words]]:
    """What was typed, as lines of an item and an amount, and what is wrong
    with it."""
    units = queries.units_of(session, [item_id for _, item_id, _ in found if item_id])
    counted: list[tuple[int, Decimal]] = []
    problems: dict[str, Words] = {}
    for field, item_id, typed in found:
        if item_id is None or (unit := units.get(item_id)) is None:
            problems[field] = NO_SUCH_ITEM
            continue
        amount = quantity(typed, positive=False, unit=unit)
        if isinstance(amount, str):
            problems[field] = amount
        elif amount is not None:
            counted.append((item_id, amount))
    return counted, problems


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
