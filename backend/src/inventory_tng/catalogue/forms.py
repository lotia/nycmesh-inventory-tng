"""How the item pages' forms are read and checked: an item's or a
package's name as it will be stored, the units in it to ask about, what
to set, and what is wrong, in words, beside the field it is about; and
the readings chosen on the unit readings pages, and a category's name
taken. The handlers in items_routes.py, reading_rules_routes.py and
categories_routes.py read the request and pass the form's fields."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

import structlog
from markupsafe import Markup, escape
from sqlalchemy.orm import Session

from inventory_tng.catalogue import (
    items,
    reading_rules,
    unit_changes,
    unit_recounts,
    units,
)
from inventory_tng.catalogue.amount_rules import becomes_counted_whole, is_whole
from inventory_tng.catalogue.name_keys import nameless
from inventory_tng.catalogue.queries import UnitChangeExample
from inventory_tng.catalogue.reading_rules import chosen_rule, read_alike_words
from inventory_tng.core.db_errors import Broken
from inventory_tng.core.phrases import (
    Separator,
    Words,
    gettext,
    lazy_gettext,
    ngettext,
)
from inventory_tng.models.base import INTEGER_MAX, whole
from inventory_tng.models.catalogue import (
    UNITS,
    Category,
    Item,
    ItemPackage,
    reading_rules_held,
)
from inventory_tng.models.names import canonical
from inventory_tng.web.formatting import amount
from inventory_tng.web.forms import chosen, name_problem, quantity

log = structlog.get_logger(__name__)

TEXT_FIELDS = ("manufacturer", "part_number", "old_code", "notes", "ordering_notes")
# The fields of the add form on the items list (templates/catalogue/
# items.html), besides what its name field adds as it asks (add_form):
# an add reads only these, so a new item's others are left as Fields.new
# leaves them, whatever is sent (Ali, 2026-11-24, inventory-tng-iibw).
# The item page's form has every field.
ADD_FIELDS = frozenset({"name", "category_id", "unit", "manufacturer", "part_number"})
# Said when another item had what was asked for, but no longer does.
TAKEN = lazy_gettext(
    "Another item, perhaps a retired one, already has that name, that "
    "manufacturer and part number, or that old code."
)
NO_LETTERS = lazy_gettext("A name needs at least one letter or number.")
# A field saying how a unit after a number in a name is read, asked where
# nothing settles it, or corrected ("Not right?"): this, then the unit as
# typed (units.rule_key); its value is str(Reading), or
# NOT_A_UNIT, or blank: as read. Kept as a reading rule
# (reading_rules.decide), so it is asked once.
READING = "unit:"
ASK_UNIT = lazy_gettext("Say below whether it is a unit.")
# The form's field holding the name an administrator was shown close names
# for, and sent again: a different item's, so not asked about again.
DIFFERENT = "different"
# What of another item is taken (items.taken_by), naming it.
TAKEN_FIELDS: dict[str, Callable[[str], str]] = {
    "name": lambda other: gettext(
        "%(other)s already has that name, or one differing from it only in "
        "case, spaces, punctuation or how its units are spelt.",
        other=other,
    ),
    "part_number": lambda other: gettext(
        "%(other)s already has that manufacturer and part number.", other=other
    ),
    "old_code": lambda other: gettext(
        "%(other)s already has that old code.", other=other
    ),
    "alias": lambda other: gettext(
        "%(other)s has that name, or one differing from it only in case, "
        "spaces, punctuation or how its units are spelt, as an alias.",
        other=other,
    ),
}
# What of another item an alias would be (items.alias_taken_by), naming it.
ALIAS_TAKEN: dict[str, Callable[[str], str]] = {
    "name": TAKEN_FIELDS["name"],
    "alias": lambda other: gettext(
        "%(other)s already has that alias, or one differing from it only in "
        "case, spaces, punctuation or how its units are spelt.",
        other=other,
    ),
}
# An alias an item has already, as its name or as an alias.
ALIAS_ITS_OWN = {
    "name": lazy_gettext("That is its name already."),
    "alias": lazy_gettext("That is its alias already."),
}
# After another item is named as having what was asked for.
IS_RETIRED = lazy_gettext("It is retired.")


def _retired(other: Item, words: str) -> str:
    """`words` naming `other`, saying so if it is retired."""
    return f"{words} {IS_RETIRED}" if other.retired_at is not None else words


def taken(
    session: Session,
    fields: items.Fields,
    typed: Typed,
    item_id: int | None,
    broken: Broken,
) -> tuple[str, Words]:
    """The field another item already has, by the unique index or rule
    `broken` refused it by, its name's units read as the form's answers
    say too, and words naming that item."""
    refused_by = broken.index or broken.rule
    found = items.taken_by(session, fields, item_id, typed.rules, refused_by)
    field, other = found or ("name", None)
    # Beside the name, whose field "alias" is.
    shown = "name" if field == "alias" else field
    log.debug(
        "item refused",
        rule=refused_by,
        field=field,
        item_id=item_id,
        other_item_id=other and other.id,
    )
    if other is None:
        return shown, TAKEN
    return shown, _retired(other, TAKEN_FIELDS[field](other.name_shown))


READ_AS_ONE = lazy_gettext(
    "Read so, names would read as one. Try again, or answer otherwise."
)


def read_as_one(names: str) -> str:
    """Names that answers would read alike, refused."""
    return gettext(
        "Read so, these names would read as one: %(names)s. Rename one of "
        "each, or answer otherwise.",
        names=names,
    )


def read_alike(session: Session, typed: Typed) -> Words:
    """Words for answers refused as they would read names alike, naming
    them by id, the savepoint undone (items.read_alike)."""
    words = read_alike_words(session, typed.answered, typed.rules)
    log.debug("unit answer refused", read_alike=len(words))
    if not words:  # Renamed meanwhile: none to name now.
        return READ_AS_ONE
    return read_as_one("; ".join(words))


@dataclass(frozen=True)
class Typed:
    """A name as it will be stored, the units in it still to ask about, the
    answers and corrections given, to keep as reading rules, the rules it
    is read by (those held, with the answers), and the rules the answers
    wrote, once written."""

    name: str
    asked: list[units.Found]
    answered: dict[str, units.Rule]
    rules: units.Rules
    decided: list[int] = field(default_factory=list)


def typed_name(session: Session, typed: str, form: Mapping[str, str]) -> Typed:
    """An item's or a package's name as it will be stored: as typed, in
    canonical spelling (models.names.CanonicalName), each unit respelt only
    where its reading rule says (units.spell; Ali, 2026-11-15,
    inventory-tng-c0c.3); the units still to ask about; and how `form` says a
    unit is read, where that is new: an answer, or a correction of what it was
    read as."""
    return read(typed, reading_rules_held(session), form)


def read(typed: str, rules: units.Rules, form: Mapping[str, str]) -> Typed:
    """typed_name, by the reading rules `rules`."""
    name = canonical(typed.strip())
    answered = {}
    for found in units.found_in(name, rules):
        rule = chosen_rule(form.get(READING + found.key, ""), found.offered)
        if rule is None:
            continue
        current = None if found.read is None else str(found.read)
        if found.asks or rule.reading != current:
            # Corrected, a rule respelling it goes on doing so.
            kept = found.rule is not None and found.rule.respell and rule.reading
            answered[found.key] = units.Rule(rule.reading, bool(kept))
    rules = {**rules, **answered}
    found = units.found_in(name, rules)
    asked = [each for each in found if each.asks]
    return Typed(units.spell(name, found), asked, answered, rules)


def item_name_problem(
    name: str, asked: Sequence[units.Found], item_id: int | None
) -> Words | None:
    """What is wrong with an item's or a package's name, compared as items'
    are (name_keys.name_key), or a unit in it to ask about."""
    if problem := name_problem(name):
        return problem
    if nameless(name):
        return NO_LETTERS
    if asked:
        log.debug("unit asked about", item_id=item_id, asked=len(asked))
        return ASK_UNIT
    return None


PICK_A_CATEGORY = lazy_gettext("Pick a category.")
PICK_A_UNIT = lazy_gettext("Pick a unit.")


def unit_declined_problem(
    old: str,
    new: str,
    why: unit_changes.UnitFixed,
    example: UnitChangeExample | None,
    labels_printed: int,
    retired: bool,
) -> Markup:
    """What the item form says beside its unit, in `old`, when the
    administrator answers No to changing it to `new` (Ali, 2026-11-25,
    inventory-tng-y50b, inventory-tng-aknv): that it is still in `old`,
    and what a change would do, with `example`, one of its own amounts,
    if it has one (queries.unit_change_example). Only labelled (`why`),
    until its `labels_printed` labels are revoked, unless `retired`, as a
    retired item's labels are inactive. The way to put right a unit wrong
    from the start follows, a link in it, unless it is retired
    (catalogue/item.html). Markup, as the example's field name is bold:
    the values in it escaped."""
    values: dict[str, object] = {"old": escape(old), "new": escape(new)}
    if example is not None:
        values["example"] = _example(example, old, new)
    if why == "labelled" and retired:
        return _unit_retired(values)
    if why == "labelled":
        return _unit_labelled(labels_printed, values)
    if why == "moved":
        if example is None:
            # Translators: answered No to "Were the recorded amounts really in
            # %(old)s?" for an item whose stock has moved, though it holds
            # and stores none. Never "converting".
            return Markup(
                gettext(
                    "The unit is still %(old)s. Changing it to %(new)s "
                    "recalculates every recorded amount from %(old)s.",
                    **values,
                )
            )
        # Translators: answered No to "Were the recorded amounts really in
        # %(old)s?" for an item whose stock has moved; %(example)s says one
        # of its amounts converted, as "The stock at Shelf A would change
        # from 305 m to 1000.656 ft", or, with none held anywhere, a stored
        # one, as "Its Reorder at would change from 100 m to 328.084 ft".
        # Never "converting".
        return Markup(
            gettext(
                "The unit is still %(old)s. Changing it to %(new)s recalculates "
                "every recorded amount from %(old)s. %(example)s.",
                **values,
            )
        )
    if example is None:
        # Translators: answered No to "Were the recorded amounts really in
        # %(old)s?" for an item whose amounts have been converted before.
        # Never "converting".
        return Markup(
            gettext(
                "The unit is still %(old)s. Changing it to %(new)s recalculates "
                "its amounts from %(old)s.",
                **values,
            )
        )
    # Translators: answered No to "Were the recorded amounts really in
    # %(old)s?" for an item whose amounts have been converted before;
    # %(example)s says one of them converted, as "The Units per order from
    # Acme would change from 1000.656 ft to 305 m". Never "converting".
    return Markup(
        gettext(
            "The unit is still %(old)s. Changing it to %(new)s recalculates its "
            "amounts from %(old)s. %(example)s.",
            **values,
        )
    )


def _unit_retired(values: Mapping[str, object]) -> Markup:
    """unit_declined_problem for a retired item its printed labels fix:
    no change to speak of until it is brought back, which the page's top
    offers (Ali, 2026-11-25, inventory-tng-aknv)."""
    if "example" not in values:
        # Translators: answered No to "Were the recorded amounts really in
        # %(old)s?" for a retired item with labels printed for it.
        return Markup(gettext("The unit is still %(old)s.", **values))
    # Translators: as "The unit is still %(old)s.", for an item with
    # %(example)s, one of its amounts converted, as "Its Reorder at would
    # change from 100 m to 328.084 ft".
    return Markup(gettext("The unit is still %(old)s. %(example)s.", **values))


def _unit_labelled(count: int, values: Mapping[str, object]) -> Markup:
    """unit_declined_problem for an item only its `count` printed labels
    fix, its count said as the revoke partial says it
    (catalogue/_revoke_labels.html), opening a sentence."""
    if count == 2:
        # Translators: opens "…, changing it recalculates its amounts", said
        # of an item with two labels printed for it, not revoked.
        until = gettext("Until both its printed labels are revoked")
    else:
        # Translators: opens "…, changing it recalculates its amounts", said
        # of an item with labels printed for it, not revoked; the plural
        # is said of more than two, as two have their own words.
        until = ngettext(
            "Until its printed label is revoked",
            "Until all %(num)s of its printed labels are revoked",
            count,
        )
    if "example" not in values:
        # Translators: answered No to "Were the recorded amounts really in
        # %(old)s?" for an item its printed labels fix; %(until)s is as
        # "Until both its printed labels are revoked". Never "converting".
        return Markup(
            gettext(
                "The unit is still %(old)s. %(until)s, changing it recalculates "
                "its amounts from %(old)s.",
                until=escape(until),
                **values,
            )
        )
    # Translators: as "The unit is still %(old)s. %(until)s, changing it
    # recalculates its amounts from %(old)s."; %(example)s says one of
    # them converted, as "Its On order would change from 50 m to 164.042 ft".
    return Markup(
        gettext(
            "The unit is still %(old)s. %(until)s, changing it recalculates its "
            "amounts from %(old)s. %(example)s.",
            until=escape(until),
            **values,
        )
    )


def _example(example: UnitChangeExample, old: str, new: str) -> Markup:
    """One amount of an item converted from `old` into `new`, as the note
    after No says it, a sentence but for its full stop (Ali, 2026-11-25,
    inventory-tng-aknv): a stored amount's field name bold, as its label
    on the page."""
    values = {
        "amount": escape(amount(example.amount)),
        "becomes": escape(amount(example.becomes)),
        "old": escape(old),
        "new": escape(new),
        "name": escape(example.name or ""),
    }
    if example.of == "place":
        # Translators: completes "…recalculates every recorded amount from
        # m."; %(name)s is a place of any kind, such as a store, a shelf, a
        # bin or a volunteer's home; %(amount)s %(old)s is what the records
        # hold there, %(becomes)s %(new)s that converted.
        message = gettext(
            "The stock at %(name)s would change from %(amount)s %(old)s to "
            "%(becomes)s %(new)s",
            **values,
        )
    elif example.of == "minimum":
        # Translators: follows "…recalculates its amounts from m.",
        # "…recalculates every recorded amount from m." or "The unit is
        # still m."; <strong>Reorder at</strong> is the item's field of that
        # name, kept bold; %(amount)s %(old)s is its value, %(becomes)s
        # %(new)s that converted.
        message = gettext(
            "Its <strong>Reorder at</strong> would change from %(amount)s "
            "%(old)s to %(becomes)s %(new)s",
            **values,
        )
    elif example.of == "on_order":
        # Translators: follows "…recalculates its amounts from m.",
        # "…recalculates every recorded amount from m." or "The unit is
        # still m."; <strong>On order</strong> is the item's field of that
        # name, kept bold; %(amount)s %(old)s is how much is on order,
        # %(becomes)s %(new)s that converted.
        message = gettext(
            "Its <strong>On order</strong> would change from %(amount)s "
            "%(old)s to %(becomes)s %(new)s",
            **values,
        )
    else:
        # Translators: follows "…recalculates its amounts from m.",
        # "…recalculates every recorded amount from m." or "The unit is
        # still m."; <strong>Units per order</strong> is a vendor offer's
        # field of that name, kept bold; %(name)s is the vendor's name, or
        # the address of its offer when it names none; %(amount)s %(old)s is
        # its value, %(becomes)s %(new)s that converted.
        message = gettext(
            "The <strong>Units per order</strong> from %(name)s would change "
            "from %(amount)s %(old)s to %(becomes)s %(new)s",
            **values,
        )
    return Markup(message)


# A recount's boxes besides Reorder at: what is on order, each current
# vendor offer's units per order, and each package's size and too-short
# length asked again, these then its id.
ON_ORDER, PER_ORDER = "on_order", "per_order_"
SIZE_OF, SHORT_BELOW_OF = "size_", "short_below_"


def recount_amounts(
    form: Mapping[str, str],
    offer_ids: Sequence[int],
    packages: Sequence[ItemPackage],
    *,
    separator: Separator,
) -> tuple[unit_recounts.Amounts | None, dict[str, Words]]:
    """What a guided recount's form sets in the new unit, unless something
    is wrong, and what is, by field: Reorder at and what is on order zero
    or more, each offer's units per order above zero, each blank for none;
    each package's size above zero, and its too-short length, asked only
    of one that has one, blank for none, shorter than its size. A package
    whose size its page did not ask, added since, is left out, so the
    recount is refused as changed (Planned.seen). In the decimal
    `separator` of the page's boxes."""
    problems: dict[str, Words] = {}
    read: dict[str, Decimal | None] = {}
    fields = {"minimum": False, ON_ORDER: False}
    fields |= {f"{PER_ORDER}{offer_id}": True for offer_id in offer_ids}
    for name, positive in fields.items():
        typed = quantity(form.get(name, ""), positive=positive, separator=separator)
        if isinstance(typed, str):
            problems[name] = typed
        else:
            read[name] = typed
    sizes: dict[int, unit_recounts.Sized] = {}
    for package in packages:
        if (sized := _resized(form, package, problems, separator)) is not None:
            sizes[package.id] = sized
    if problems:
        return None, problems
    per_order = {each: read[f"{PER_ORDER}{each}"] for each in offer_ids}
    minimum, on_order = read["minimum"], read[ON_ORDER]
    return unit_recounts.Amounts(minimum, on_order, per_order, sizes), {}


def _resized(
    form: Mapping[str, str],
    package: ItemPackage,
    problems: dict[str, Words],
    separator: Separator,
) -> unit_recounts.Sized | None:
    """A package's size and too-short length as a recount's form gives
    them again, if it asked and both are right; else None, and what is
    wrong added to `problems`."""
    size_box, short_box = f"{SIZE_OF}{package.id}", f"{SHORT_BELOW_OF}{package.id}"
    if size_box not in form:
        return None
    size = quantity(
        form[size_box], positive=True, required=SIZE_REQUIRED, separator=separator
    )
    short = quantity(form.get(short_box, ""), positive=True, separator=separator)
    if isinstance(size, str):
        problems[size_box] = size
    read_size = size if isinstance(size, Decimal) else None
    if (problem := _short_problem(short, read_size)) is not None:
        problems[short_box] = problem
    if not isinstance(size, Decimal) or isinstance(short, str):
        return None
    return unit_recounts.Sized(size, short)


def confirmed(session: Session, form: Mapping[str, str], fields: items.Fields) -> bool:
    """Whether the name was sent before and asked about, then sent again
    unchanged: a different item's, whatever is close."""
    return typed_name(session, form.get(DIFFERENT, ""), form).name == fields.name


def same_name(
    session: Session,
    item: Item,
    form: Mapping[str, str],
    fields: items.Fields,
    typed: Typed,
) -> bool:
    """Whether `item`'s new name asks nothing about close names."""
    # Its own name, or a respelling of it, its units' too, asks
    # nothing: it is the same; nor one of its aliases
    # (items_routes.py's _change_or_note).
    return items.Keys.of(session, fields.name, typed.rules).same(item) or confirmed(
        session, form, fields
    )


def package_fraction(session: Session, item_id: int) -> str | None:
    """Beside the unit: an item changed to each while a current package of
    it holds, or is too short to use below, a fraction
    (items.fractional_package), naming it; None if none does."""
    package = items.fractional_package(session, item_id)
    if package is None:
        return None
    log.debug("unit change refused", item_id=item_id, item_package_id=package.id)
    return fraction_held(package)


def size_has_fraction(package: ItemPackage) -> bool:
    """Whether the package's Holds has a fraction: said before its
    too-short length, which then needs no outline."""
    return not is_whole(package.size)


def _whole_range(package: ItemPackage) -> dict[str, str]:
    """The whole numbers its too-short length may be, its size whole and
    more than 1 (items.fractional_package): more than 0 and less than its
    size."""
    return {"low": amount(Decimal(1)), "high": amount(package.size - 1)}


def fraction_held(package: ItemPackage) -> str:
    """What package_fraction says of the package: its size, if that has a
    fraction, else the whole numbers its too-short length may be, never
    naming Too short to use, a length that no longer applies once the item
    counts pieces (Ali, 2026-11-23, inventory-tng-fa5i)."""
    name = package.name_shown
    if size_has_fraction(package):
        # Translators: refused switching an item's unit to each: a
        # package's size, its "Holds" on the item page, has a fraction;
        # %(package)s is its name, %(amount)s its size.
        return gettext(
            "The %(package)s package holds %(amount)s: change that to a whole "
            "number, or retire the package, before switching the item count "
            "to each.",
            package=name,
            amount=amount(package.size),
        )
    # Translators: refused switching an item's unit to each: a package's
    # "Too short to use" length, outlined on the page, has a fraction;
    # %(package)s is its name, %(amount)s that length, %(low)s and
    # %(high)s the whole numbers it may be.
    return gettext(
        "The %(package)s package has a fraction (%(amount)s): set it to a "
        "whole number from %(low)s to %(high)s before switching the item "
        "count to each.",
        package=name,
        amount=amount(package.short_below),
        **_whole_range(package),
    )


def short_has_fraction(package: ItemPackage) -> bool:
    """Whether the package, holding more than 1, is too short to use below
    a fraction: one holding 1 is cleared instead (items._HOLDS_ONE)."""
    short = package.short_below
    return short is not None and package.size > 1 and not is_whole(short)


def short_to_set(package: ItemPackage) -> str:
    """Beside the Too short to use box of a package brought back to an item
    counted each with a fraction there: the whole numbers it may be (Ali,
    2026-11-23, inventory-tng-fa5i)."""
    # Translators: beside the "Too short to use" box of a package just
    # brought back to an item counted each, whose length has a fraction;
    # %(low)s and %(high)s are the whole numbers it may be.
    return gettext(
        "Set it to a whole number from %(low)s to %(high)s.", **_whole_range(package)
    )


def add_form(form: Mapping[str, str]) -> dict[str, str]:
    """Of `form`, only what the add form sends: ADD_FIELDS, and what its
    name field adds as it asks: the answers saying how the units in the
    name are read (READING), and the name confirmed (DIFFERENT)."""
    kept = {
        key: value
        for key, value in form.items()
        if key in ADD_FIELDS or key == DIFFERENT or key.startswith(READING)
    }
    # How many, not which: a hand-made form's names are free text.
    if ignored := len(form) - len(kept):
        log.debug("add form fields ignored", ignored=ignored)
    return kept


def item_fields(
    session: Session,
    form: Mapping[str, str],
    item: Item | None = None,
    *,
    separator: Separator,
) -> tuple[items.Fields | None, dict[str, Words], Typed]:
    """What to set, unless something is wrong, what is, and the name as
    typed; for `item`'s form, if given, else the add form's, as add_form
    keeps it. Its minimum is in the decimal `separator` of the page's
    boxes."""
    item_id = item.id if item is not None else None
    typed = typed_name(session, form.get("name", ""), form)
    name = typed.name
    problems: dict[str, Words] = {}
    if problem := item_name_problem(name, typed.asked, item_id):
        problems["name"] = problem
    category = chosen(session, Category, form.get("category_id", ""))
    if category is None:
        problems["category_id"] = PICK_A_CATEGORY
    unit = form.get("unit", "")
    if unit not in UNITS:
        problems["unit"] = PICK_A_UNIT
    # Checked again under the lock (items.save).
    elif (
        item is not None
        and becomes_counted_whole(item.unit, unit)
        and (fraction := package_fraction(session, item.id))
    ):
        problems["unit"] = fraction
    minimum = quantity(
        form.get("minimum", ""), positive=False, unit=unit, separator=separator
    )
    if isinstance(minimum, str):
        problems["minimum"] = minimum
    # Each None or str here is a problem already; checked again for the types.
    if problems or category is None or isinstance(minimum, str):
        return None, problems, typed
    texts = {field: form.get(field, "").strip() or None for field in TEXT_FIELDS}
    fields = items.Fields(
        name=name,
        category_id=category.id,
        unit=unit,
        minimum=minimum,
        # A box left unticked is not sent, nor is one the add form has
        # not: a new item does not follow its opened packages.
        track_opened=form.get("track_opened") == "on",
        **texts,
    )
    return fields, problems, typed


# The label of a package's size, on its form and a recount's.
HOLDS = lazy_gettext("Holds")
# A package refused as another of its item's, retired ones too, has its name.
PACKAGE_TAKEN = lazy_gettext("It already has a package by that name.")
SIZE_REQUIRED = lazy_gettext("How many of the item's unit it holds.")
SHORTER_THAN_WHOLE = lazy_gettext("Shorter than a whole one, or blank.")
WHOLE_PACKAGES = lazy_gettext("A whole number of packages, or blank.")


def package_fields(
    item: Item,
    name: str,
    asked: Sequence[units.Found],
    size: str,
    *,
    kept: Decimal | None = None,
    separator: Separator,
) -> tuple[Decimal | None, dict[str, Words]]:
    """A package's size as typed, and what is wrong with its name or size:
    its name compared as items' are, so it needs a letter or a digit, with
    no unit in it `asked` about; its size whole, for an item counted each.
    A blank size is `kept`, if given: a size fixed is not on the form, and
    stays as it is, whatever its item's unit has become since."""
    problems: dict[str, Words] = {}
    if problem := item_name_problem(name, asked, item.id):
        problems["package_name"] = problem
    if kept is not None and not size.strip():
        return kept, problems
    parsed = quantity(
        size,
        positive=True,
        unit=item.unit,
        required=SIZE_REQUIRED,
        separator=separator,
    )
    if isinstance(parsed, str):
        problems["size"] = parsed
    return (parsed if isinstance(parsed, Decimal) else None), problems


def package_settings_fields(
    package: ItemPackage,
    unit: str,
    short_below: str | None,
    minimum_sealed: str,
    *,
    separator: Separator,
) -> tuple[Decimal | None, int | None, dict[str, Words]]:
    """A package's too-short-to-use length and minimum sealed as typed,
    each blank for none, and what is wrong with them: the length shorter
    than a whole one, whole for an item counted each. A length not sent,
    as the page shows no box for it, is the package's own, kept."""
    if short_below is None:
        short: Decimal | str | None = package.short_below
    else:
        short = quantity(short_below, positive=True, unit=unit, separator=separator)
    minimum = whole(minimum_sealed, 0, INTEGER_MAX) if minimum_sealed.strip() else None
    problems = {}
    if (problem := _short_problem(short, package.size)) is not None:
        problems["short_below"] = problem
    if minimum_sealed.strip() and minimum is None:
        problems["minimum_sealed"] = WHOLE_PACKAGES
    return (None if isinstance(short, str) else short), minimum, problems


def _short_problem(short: Decimal | str | None, size: Decimal | None) -> Words | None:
    """What is wrong with a too-short-to-use length as read (quantity), if
    anything: unreadable, or not shorter than a whole `size`, if known."""
    if isinstance(short, str):
        return short
    if short is not None and size is not None and short >= size:
        return SHORTER_THAN_WHOLE
    return None


def alias_problem(
    session: Session, item: Item, typed: Typed, broken: str | None
) -> Words:
    """Words for an alias refused (`broken`, logged) as an item's name or
    another alias, its units read as the form's answers say too, naming
    that item, or what is wrong with the name itself."""
    found = items.alias_taken_by(session, typed.name, typed.rules)
    whose, other = found or ("name", None)
    log.debug(
        "alias refused",
        rule=broken,
        item_id=item.id,
        other_item_id=other and other.id,
    )
    # Its holder changed since, as the refusal let go of the lock: rare.
    if other is None:
        return TAKEN
    if other.id == item.id:
        return ALIAS_ITS_OWN[whose]
    return _retired(other, ALIAS_TAKEN[whose](other.name_shown))


# The seeding page's field for each token's reading: this, then the token.
PROPOSED = "reading:"
CHOOSE = lazy_gettext("Choose how to read each one marked “Choose…” first.")
CHOOSE_ONE = lazy_gettext("Choose one of the readings offered.")


def seed_choices(
    form: Mapping[str, str], made: Sequence[reading_rules.Proposal]
) -> tuple[dict[str, units.Rule], bool]:
    """The readings the seeding page sent, of those it proposed (`made`),
    by token, and whether any was left not chosen."""
    proposals = {proposal.found.key: proposal for proposal in made}
    chosen: dict[str, units.Rule] = {}
    unchosen = False
    for key, value in form.items():
        proposal = proposals.get(key.removeprefix(PROPOSED))
        if not key.startswith(PROPOSED) or proposal is None:
            continue
        rule = chosen_rule(value, proposal.found.offered)
        if rule is None:
            unchosen = True
        else:
            chosen[proposal.found.key] = rule
    return chosen, unchosen


# A category's name another has.
CATEGORY_TAKEN = lazy_gettext(
    "There is already a category by that name, perhaps a retired one: "
    "bring that back instead."
)
