"""How a place's count form (stock/count_routes.py) is read and checked:
its lines by place, by item and by form, the areas gone from it and what
arrived from them, a recount's figures, and what is wrong, in words,
beside the field it is about. The handlers pass the form's fields."""

from collections.abc import Callable, Collection, Mapping, Sequence
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy.orm import Session

from inventory_tng.core.phrases import Separator, Words, lazy_gettext
from inventory_tng.models.base import INTEGER_MAX, whole
from inventory_tng.models.catalogue import Item, ItemPackage
from inventory_tng.models.stock import OpenPackage
from inventory_tng.stock import queries
from inventory_tng.stock.adjustment_reasons import LOST, USED_UP
from inventory_tng.stock.counts import ItemLines
from inventory_tng.stock.forms import (
    PICK_A_PLACE,
    SIZES,
    UNITS,
    now_counted_in,
    now_holds,
)
from inventory_tng.stock.ledger import LOOSE, Form, repeated
from inventory_tng.stock.units_shown import Now
from inventory_tng.web.forms import (
    QUANTITY_MAX,
    RULES,
    quantity,
    read_amount,
    row_id,
    unit_free_problem,
)

# The count form's fields of items: counted, by item, and one chosen at
# the foot.
COUNTED = "counted_"
EXTRA, EXTRA_ITEM = "extra_quantity", "extra_item_id"
# An open package closed while it was counted: a ledger rule a race can
# break past the page's checks, said above the form as rules are.
CLOSED_MEANWHILE = "movement_open_package_closed"
ON_THE_FORM_TWICE = lazy_gettext("This item is on the form more than once.")
# Something else here naming an item the place picked for it lists: said on
# that item's line there (Ali, 2026-11-21, inventory-tng-fh0e).
TYPE_IT_HERE = lazy_gettext("Type everything you found here.")
NO_SUCH_PACKAGE = lazy_gettext("No such package.")
NO_SUCH_ITEM = lazy_gettext("No such item.")
NO_SUCH_OPEN_PACKAGE = lazy_gettext("No such open package here.")
WHOLE_PACKAGES = lazy_gettext("A whole number of packages.")
TOO_MANY = lazy_gettext("That is more than the inventory can count.")
HOW_MUCH_LEFT = lazy_gettext("How much is left on it.")
# What read_count says of a box that is not about its number, so said
# after its item's new unit too, in place of the number's problem
# (changed_words; Ali, 2026-11-25, inventory-tng-jnwh). Not NO_SUCH_ITEM:
# an item that does not exist has no new unit to say it after.
NOT_THE_NUMBER = (
    ON_THE_FORM_TWICE,
    TYPE_IT_HERE,
    NO_SUCH_PACKAGE,
    NO_SUCH_OPEN_PACKAGE,
)

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
    extra, typed = form.get(EXTRA_ITEM, ""), form.get(EXTRA, "")
    if extra and typed.strip():
        found.append((EXTRA, _item_id(extra), typed))
    return found


def listed_line(fields: Mapping[str, str], item_id: int) -> str | None:
    """A place's field for an item it lists: counted by its total, or, by
    form, its loose units, which such an item always has."""
    for field in fields:
        if field.startswith(COUNTED):
            named = _item_id(field.removeprefix(COUNTED))
        elif field.startswith(LOOSE_FIELD):
            named = row_id(field.removeprefix(LOOSE_FIELD))
        else:
            continue
        if named == item_id:
            return field
    return None


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
    session: Session, form: Mapping[str, str], place_id: int, *, separator: Separator
) -> tuple[ByForm, dict[str, Words]]:
    """The count form's forms (count_asked_for.forms_by_place), as lines,
    their boxes in the decimal `separator` (web/decimal_separator.py), and
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
            elif count * package.size_in_items_unit > QUANTITY_MAX:
                problems[field] = TOO_MANY
            else:
                held = count * package.size_in_items_unit
                lines.append((package.item_id, Form(package_id=package.id), held))
        elif field.startswith(LOOSE_FIELD):
            item = items.get(row_id(field.removeprefix(LOOSE_FIELD)) or 0)
            if item is None:
                problems[field] = NO_SUCH_ITEM
                continue
            amount = quantity(
                typed, positive=False, unit=item.unit, separator=separator
            )
            if isinstance(amount, str):
                problems[field] = amount
            else:
                assert amount is not None  # Typed, so not blank.
                lines.append((item.id, LOOSE, amount))
        elif field.startswith(OPENED):
            row = session.get(OpenPackage, row_id(field.removeprefix(OPENED)) or 0)
            if row is None or row.location_id != place_id or state not in STATES:
                problems[field] = NO_SUCH_OPEN_PACKAGE
            elif (reason := STATES[state]) is not None:
                # Closed already, by this form saved and sent again or
                # meanwhile, it is as said: closing it writes nothing
                # (open_packages.close), so a form sent again unchanged
                # is answered as before (inventory-tng-kro9). Its amount
                # is not read, so not counted refused (inventory-tng-7f45).
                close.append((row.id, reason))
            elif row.closed_at is not None:
                # Its line is gone from the form, so said above it.
                problems[CLOSED_MEANWHILE] = RULES[CLOSED_MEANWHILE]
            else:
                unit = session.get_one(Item, row.item_id).unit
                amount = quantity(typed, positive=False, unit=unit, separator=separator)
                if isinstance(amount, str):
                    problems[field] = amount
                else:
                    assert amount is not None  # Typed, so not blank.
                    lines.append((row.item_id, Form(open_package_id=row.id), amount))
    found = None
    if form.get(FOUND_PACKAGE, "").strip():
        package = session.get(ItemPackage, row_id(form[FOUND_PACKAGE]) or 0)
        if package is None:
            # What is left is not read, so not counted refused
            # (inventory-tng-ndrg).
            problems[FOUND_PACKAGE] = NO_SUCH_PACKAGE
        else:
            left = quantity(
                form.get(FOUND_LEFT, ""),
                positive=True,
                unit=session.get_one(Item, package.item_id).unit,
                required=HOW_MUCH_LEFT,
                separator=separator,
            )
            if isinstance(left, str):
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
    EXTRA_PLACE: (EXTRA_ITEM, EXTRA),
    FOUND_PLACE: (FOUND_PACKAGE, FOUND_LEFT),
}
# Fields said once, wherever their place: never prefixed. The units and
# sizes shown are the whole form's (forms.UNITS, forms.SIZES).
SHARED = {
    *PICKED,
    *(name for names in PICKED.values() for name in names),
    UNITS,
    SIZES,
}


def field_at(root_id: int, place_id: int, name: str) -> str:
    """The count form's name for a field of a place on the count of
    `root_id` (AT)."""
    if place_id == root_id or name in SHARED or name == CLOSED_MEANWHILE:
        return name
    return f"{AT}{place_id}_{name}"


def fields_at[V](
    root_id: int, place_id: int, by_field: Mapping[str, V]
) -> dict[str, V]:
    """These fields of a place, each by its name on the count form of
    `root_id` (field_at)."""
    return {field_at(root_id, place_id, name): v for name, v in by_field.items()}


def places_listed(form: Mapping[str, str], root_id: int) -> list[int] | None:
    """The places a count form shows (PLACES), by id; just its own, on a
    form that lists none; None if one listed is no id at all."""
    listed = form.get(PLACES, "").split() or [str(root_id)]
    ids = [row_id(typed) for typed in listed]
    if any(place_id is None for place_id in ids):
        return None
    return sorted({place_id for place_id in ids if place_id is not None})


# The areas gone from a count form since it was first shown, retired and
# holding nothing, and that first form's moment: what left them since is
# shown on the form until it is saved (Ali, 2026-11-22, inventory-tng-ygk5).
GONE, GONE_SINCE = "gone", "gone_since"


class Gone(NamedTuple):
    """The areas gone from a count form (GONE), by id, and the moment what
    left them is read from."""

    ids: tuple[int, ...] = ()
    since: int = 0


# A count form with no area gone from it.
NONE_GONE = Gone()

# How many arrived of an item moved from an area gone from a count form to
# a place on it, fixing the amount moved, in each form it arrived in:
# arrived_<area id>_<the field the count form counts that form by> (SEALED,
# LOOSE_FIELD, OPENED), as the place's field (stock/count_arrivals.py). A
# sealed form is fixed in whole packages, so no fix leaves a loose balance
# below nothing (coordinator, 2026-11-22, inventory-tng-ygk5).
ARRIVED = "arrived_"


def arrivals(
    session: Session, fields: Mapping[str, str], place_id: int, *, separator: Separator
) -> tuple[dict[tuple[int, int, Form], Decimal], dict[str, Words]]:
    """A place's boxes of how many arrived, read as its count by form is
    (by_form), by the area each move left, its item and form, those blank
    left out; and what is wrong."""
    by_area: dict[int, dict[str, str]] = {}
    for field, value in fields.items():
        if field.startswith(ARRIVED):
            area, _, name = field.removeprefix(ARRIVED).partition("_")
            if (area_id := row_id(area)) is not None:
                by_area.setdefault(area_id, {})[name] = value
    arrived: dict[tuple[int, int, Form], Decimal] = {}
    problems: dict[str, Words] = {}
    for area_id, typed in by_area.items():
        read, wrong = by_form(session, typed, place_id, separator=separator)
        for item_id, form, amount in read.lines:
            arrived[(area_id, item_id, form)] = amount
        problems |= {
            name if name == CLOSED_MEANWHILE else f"{ARRIVED}{area_id}_{name}": words
            for name, words in wrong.items()
        }
    return arrived, problems


def gone_listed(form: Mapping[str, str]) -> Gone | None:
    """The areas a count form lists as gone from it (GONE), none on a form
    that lists none; None if one listed is no id, or its moment no
    number."""
    listed = form.get(GONE, "").split()
    if not listed:
        return NONE_GONE
    ids = [row_id(typed) for typed in listed]
    since = whole(form.get(GONE_SINCE, ""), 0, INTEGER_MAX)
    if since is None or any(area_id is None for area_id in ids):
        return None
    return Gone(
        tuple(sorted({area_id for area_id in ids if area_id is not None})), since
    )


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


# A recount's figures of an item, as it first showed them: what was typed,
# what came in and what went out while it was counted.
Figures = tuple[Decimal, Decimal, Decimal]

# A recount's figures of each item it asks for again, carried so a recount
# refused for a problem shows them again (inventory-tng-up4h): while_<item
# id>, "typed came went". Only ever shown, never written.
WHILE = "while_"


def recount_figures(
    fields: Mapping[str, str], items: Collection[int]
) -> dict[int, Figures]:
    """A place's recount figures (WHILE) of the items it asks for again,
    each read as plain_amount wrote it; those of other items, or not three
    such of 0 or more, left out."""
    figures: dict[int, Figures] = {}
    for item_id in items:
        value = fields.get(f"{WHILE}{item_id}", "")
        match [read_amount(typed) for typed in value.split()]:
            case [Decimal() as typed, Decimal() as came, Decimal() as went] if (
                min(typed, came, went) >= 0
            ):
                figures[item_id] = (typed, came, went)
    return figures


class PlaceCount(NamedTuple):
    """What a count form says of one place: its lines as typed, those
    counted by total, those by form, on a recount, the items it asks for
    again and their figures (recount_figures), and how many arrived of the
    moves to it (arrivals)."""

    found: list[Line]
    counted: ItemLines
    forms: ByForm
    items: set[int]
    recount: dict[int, Figures]
    arrived: dict[tuple[int, int, Form], Decimal]

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


class Listed(NamedTuple):
    """Something else here naming an item the place picked for it lists
    already: the item, one there is, and the place, by id; and whether the
    form shows its line there, which a recount's may not."""

    item_id: int
    place_id: int
    on_form: bool = True


class CountForm(NamedTuple):
    """A count form read: each place's count, by id; what is wrong, by the
    form's own field names; the fields naming an item twice there, by those
    names too, and that item; and something else here naming an item its
    place lists, if it does."""

    places: dict[int, PlaceCount]
    problems: dict[str, Words]
    twice: dict[str, int | None]
    listed: Listed | None


def read_count(
    session: Session,
    form: Mapping[str, str],
    root_id: int,
    place_ids: list[int],
    held: Callable[[int, int], bool],
    *,
    separator: Separator,
) -> CountForm:
    """A count form, of the places `place_ids` (places_listed), read place
    by place (by_place) and checked; `held(place_id, item_id)`, whether a
    full count of a place, as of the form's moment, asks for an item
    (count_asked_for.asks_for). Something
    else here naming an item its place lists is not that item twice but
    typed on the wrong line: its line there is marked (TYPE_IT_HERE), and
    the page says the rest. On a recount, naming one the place held then
    but the recount does not ask for, it is refused too, and the item is
    asked for there, so the form shown again has its line (Ali,
    2026-11-22, inventory-tng-oazl)."""
    split, problems = by_place(form, root_id, place_ids)
    places: dict[int, PlaceCount] = {}
    twice: dict[str, int | None] = {}
    listed: Listed | None = None
    for place_id, fields in split.items():
        found = lines(fields)
        extra = next((item_id for field, item_id, _ in found if field == EXTRA), None)
        items = {
            item_id
            for typed in fields.get(ITEMS, "").split()
            if (item_id := row_id(typed)) is not None
        }
        found_once = found
        if extra is not None and session.get(Item, extra) is not None:
            if (line := listed_line(fields, extra)) is not None:
                listed = Listed(extra, place_id)
                problems[field_at(root_id, place_id, line)] = TYPE_IT_HERE
                # Not counted twice, then, unless another line names it too.
                found_once = [each for each in found if each[0] != EXTRA]
            elif items and extra not in items and held(place_id, extra):
                listed = Listed(extra, place_id, on_form=False)
                items.add(extra)
        counted, wrong = item_counts(session, found, separator=separator)
        forms, form_wrong = by_form(session, fields, place_id, separator=separator)
        arrived, arrived_wrong = arrivals(
            session, fields, place_id, separator=separator
        )
        figures = recount_figures(fields, items)
        places[place_id] = PlaceCount(found, counted, forms, items, figures, arrived)
        named = fields_at(root_id, place_id, named_twice(found_once))
        twice |= named
        problems |= fields_at(root_id, place_id, wrong | form_wrong | arrived_wrong)
        problems |= dict.fromkeys(named, ON_THE_FORM_TWICE)
    return CountForm(places, problems, twice, listed)


def item_counts(
    session: Session, found: Collection[Line], *, separator: Separator
) -> tuple[ItemLines, dict[str, Words]]:
    """What was typed, as lines of an item and an amount, in the decimal
    `separator`, and what is wrong with it."""
    units = queries.units_of(session, [item_id for _, item_id, _ in found if item_id])
    counted: list[tuple[int, Decimal]] = []
    problems: dict[str, Words] = {}
    for field, item_id, typed in found:
        if item_id is None or (unit := units.get(item_id)) is None:
            problems[field] = NO_SUCH_ITEM
            continue
        amount = quantity(typed, positive=False, unit=unit, separator=separator)
        if isinstance(amount, str):
            problems[field] = amount
        elif amount is not None:
            counted.append((item_id, amount))
    return counted, problems


def _counts_of(
    session: Session, name: str, fields: Mapping[str, str]
) -> tuple[int | None, int | None]:
    """The item a place's field `name` counts an amount of, in its unit,
    and the package, if it counts it sealed; neither for a field of no
    amount."""
    if name.startswith(COUNTED):
        return _item_id(name.removeprefix(COUNTED)), None
    if name.startswith(LOOSE_FIELD):
        return row_id(name.removeprefix(LOOSE_FIELD)), None
    if name.startswith(SEALED):
        item_part, _, package_part = name.removeprefix(SEALED).partition("_")
        return row_id(item_part), row_id(package_part)
    if name.startswith(OPENED):
        row = session.get(OpenPackage, row_id(name.removeprefix(OPENED)) or 0)
        return (row.item_id if row else None), None
    if name == EXTRA:
        return _item_id(fields.get(EXTRA_ITEM, "")), None
    if name == FOUND_LEFT:
        package = session.get(ItemPackage, row_id(fields.get(FOUND_PACKAGE, "")) or 0)
        return (package.item_id if package else None), None
    if name.startswith(ARRIVED):
        _, _, inner = name.removeprefix(ARRIVED).partition("_")
        return _counts_of(session, inner, fields)
    return None, None


def typed_named(
    session: Session, form: Mapping[str, str], root_id: int, place_ids: Collection[int]
) -> tuple[set[int], set[int]]:
    """The items and packages a count form's boxes with anything typed in
    count (_counts_of), read or not, so a box refused as no number is
    compared with the unit and size it was shown in too."""
    split, _ = by_place(form, root_id, place_ids)
    items: set[int] = set()
    packages: set[int] = set()
    for fields in split.values():
        for name, typed in fields.items():
            if not typed.strip():
                continue
            item_id, package_id = _counts_of(session, name, fields)
            if item_id is not None:
                items.add(item_id)
            if package_id is not None:
                packages.add(package_id)
    return items, packages


def _unit_free_problem(
    name: str, fields: Mapping[str, str], *, separator: Separator
) -> Words | None:
    """What is wrong with what a place's box `name` holds whatever unit its
    item is counted in, as by_form and item_counts read it: not a whole
    number of packages, or what web/forms.unit_free_problem says. Not
    whole units, nor too many packages, which depend on the unit
    (inventory-tng-yxd1). An open package closed is not read."""
    typed = fields.get(name, "")
    prefix = ""
    if name.startswith(ARRIVED):
        area, _, name = name.removeprefix(ARRIVED).partition("_")
        prefix = f"{ARRIVED}{area}_"
    if name.startswith(SEALED):
        unread = typed.strip() and whole(typed, 0, INTEGER_MAX) is None
        return WHOLE_PACKAGES if unread else None
    state = fields.get(f"{prefix}{STATE}{name.removeprefix(OPENED)}", "here")
    if name.startswith(OPENED) and state != "here":
        return None
    if name == FOUND_LEFT:
        return unit_free_problem(
            typed, positive=True, required=HOW_MUCH_LEFT, separator=separator
        )
    return unit_free_problem(typed, positive=False, separator=separator)


def changed_words(
    session: Session,
    form: Mapping[str, str],
    root_id: int,
    place_ids: Collection[int],
    now: Now,
    *,
    separator: Separator,
    others: Mapping[str, Words] | None = None,
) -> dict[str, Words]:
    """Beside each box of a count form of an item counted in another unit
    since it was shown, or of a package now of another size, by the form's
    own field names: what it is now, to check and save again (Ali,
    2026-11-23, inventory-tng-57u8). After an item's new unit, what else is
    wrong with the box: its problem in `others` (read_count's) that is not
    about its number (NOT_THE_NUMBER; Ali, 2026-11-25, inventory-tng-jnwh),
    or else what is wrong with its number in any unit, typed in the
    decimal `separator` (Ali, 2026-11-25, inventory-tng-yxd1)."""
    split, _ = by_place(form, root_id, place_ids)
    others = others or {}
    words: dict[str, Words] = {}
    for place_id, fields in split.items():
        for name in fields:
            field = field_at(root_id, place_id, name)
            item_id, package_id = _counts_of(session, name, fields)
            if item_id in now.units:
                other = others.get(field)
                if other not in NOT_THE_NUMBER:
                    other = _unit_free_problem(name, fields, separator=separator)
                said = now_counted_in(now.units[item_id], other)
            elif package_id in now.sizes:
                said = now_holds(*now.sizes[package_id])
            else:
                continue
            words[field] = said
    return words
