"""The unit change's forms: what the item form says beside its unit
when the administrator answers No to changing it, and what a guided
recount's form sets in the new unit. The handlers in items_routes.py and
unit_recount_routes.py read the request and pass the form's fields."""

from collections.abc import Mapping, Sequence
from decimal import Decimal

from markupsafe import Markup, escape

from inventory_tng.catalogue import unit_changes, unit_recounts
from inventory_tng.catalogue.forms import SIZE_REQUIRED, short_problem
from inventory_tng.catalogue.queries import UnitChangeExample
from inventory_tng.core.phrases import Separator, Words, gettext, ngettext
from inventory_tng.models.catalogue import ItemPackage
from inventory_tng.web.formatting import amount
from inventory_tng.web.forms import quantity


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
    until its `labels_printed` labels are revoked. A `retired` item, which
    can't be changed until it is brought back, says only that it is still
    in `old`, with the example (Ali, 2026-11-25). The way to put right a
    unit wrong from the start follows, a link in it, unless it is retired
    (catalogue/item.html). Markup, as the example's field name is bold:
    the values in it escaped."""
    values = {"old": escape(old), "new": escape(new)}
    said = None if example is None else _example(example, values)
    return Markup(_unit_note(why, said, labels_printed, retired, values))


def _unit_note(
    why: unit_changes.UnitFixed,
    said: str | None,
    labels_printed: int,
    retired: bool,
    values: Mapping[str, str],
) -> str:
    """unit_declined_problem's words, `said` its example, if it has one,
    and `values` its units, escaped."""
    if retired:
        if said is None:
            # Translators: answered No to "Were the recorded amounts really in
            # %(old)s?" for a retired item, which can't be changed until it
            # is brought back.
            return gettext("The unit is still %(old)s.", **values)
        # Translators: as "The unit is still %(old)s.", for an item with
        # %(example)s, one of its amounts converted, as "Its Reorder at would
        # change from 100 m to 328.084 ft" or "The stock at Shelf A would
        # change from 305 m to 1000.656 ft".
        return gettext(
            "The unit is still %(old)s. %(example)s.", example=said, **values
        )
    if why == "labelled":
        return _unit_labelled(labels_printed, said, values)
    if why == "moved":
        if said is None:
            # Translators: answered No to "Were the recorded amounts really in
            # %(old)s?" for an item whose stock has moved, though it holds
            # and stores none. Never "converting".
            return gettext(
                "The unit is still %(old)s. Changing it to %(new)s "
                "recalculates every recorded amount from %(old)s.",
                **values,
            )
        # Translators: answered No to "Were the recorded amounts really in
        # %(old)s?" for an item whose stock has moved; %(example)s says one
        # of its amounts converted, as "The stock at Shelf A would change
        # from 305 m to 1000.656 ft", or, with none held anywhere, a stored
        # one, as "Its Reorder at would change from 100 m to 328.084 ft".
        # Never "converting".
        return gettext(
            "The unit is still %(old)s. Changing it to %(new)s recalculates "
            "every recorded amount from %(old)s. %(example)s.",
            example=said,
            **values,
        )
    if said is None:
        # Translators: answered No to "Were the recorded amounts really in
        # %(old)s?" for an item whose amounts have been converted before.
        # Never "converting".
        return gettext(
            "The unit is still %(old)s. Changing it to %(new)s recalculates "
            "its amounts from %(old)s.",
            **values,
        )
    # Translators: answered No to "Were the recorded amounts really in
    # %(old)s?" for an item whose amounts have been converted before;
    # %(example)s says one of them converted, as "The Units per order from
    # Acme would change from 1000.656 ft to 305 m". Never "converting".
    return gettext(
        "The unit is still %(old)s. Changing it to %(new)s recalculates its "
        "amounts from %(old)s. %(example)s.",
        example=said,
        **values,
    )


def _unit_labelled(count: int, said: str | None, values: Mapping[str, str]) -> str:
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
    if said is None:
        # Translators: answered No to "Were the recorded amounts really in
        # %(old)s?" for an item its printed labels fix; %(until)s is as
        # "Until both its printed labels are revoked". Never "converting".
        return gettext(
            "The unit is still %(old)s. %(until)s, changing it recalculates "
            "its amounts from %(old)s.",
            until=escape(until),
            **values,
        )
    # Translators: as "The unit is still %(old)s. %(until)s, changing it
    # recalculates its amounts from %(old)s."; %(example)s says one of
    # them converted, as "Its On order would change from 50 m to 164.042 ft".
    return gettext(
        "The unit is still %(old)s. %(until)s, changing it recalculates its "
        "amounts from %(old)s. %(example)s.",
        until=escape(until),
        example=said,
        **values,
    )


def _example(example: UnitChangeExample, units: Mapping[str, str]) -> str:
    """One amount of an item converted from `old` into `new`, as the note
    after No says it, a sentence but for its full stop (Ali, 2026-11-25,
    inventory-tng-aknv): a stored amount's field name bold, as its label
    on the page; `units`, old and new, escaped."""
    values = {
        "amount": escape(amount(example.amount)),
        "becomes": escape(amount(example.becomes)),
        "name": escape(example.name or ""),
        **units,
    }
    if example.of == "place":
        # Translators: follows "…recalculates every recorded amount from
        # m." or "The unit is still m."; %(name)s is a place of any kind,
        # such as a store, a shelf, a bin or a volunteer's home; %(amount)s
        # %(old)s is what the records hold there, %(becomes)s %(new)s that
        # converted.
        return gettext(
            "The stock at %(name)s would change from %(amount)s %(old)s to "
            "%(becomes)s %(new)s",
            **values,
        )
    if example.of == "minimum":
        # Translators: follows "…recalculates its amounts from m.",
        # "…recalculates every recorded amount from m." or "The unit is
        # still m."; <strong>Reorder at</strong> is the item's field of that
        # name, kept bold; %(amount)s %(old)s is its value, %(becomes)s
        # %(new)s that converted.
        return gettext(
            "Its <strong>Reorder at</strong> would change from %(amount)s "
            "%(old)s to %(becomes)s %(new)s",
            **values,
        )
    if example.of == "on_order":
        # Translators: follows "…recalculates its amounts from m.",
        # "…recalculates every recorded amount from m." or "The unit is
        # still m."; <strong>On order</strong> is the item's field of that
        # name, kept bold; %(amount)s %(old)s is how much is on order,
        # %(becomes)s %(new)s that converted.
        return gettext(
            "Its <strong>On order</strong> would change from %(amount)s "
            "%(old)s to %(becomes)s %(new)s",
            **values,
        )
    # Translators: follows "…recalculates its amounts from m.",
    # "…recalculates every recorded amount from m." or "The unit is
    # still m."; <strong>Units per order</strong> is a vendor offer's
    # field of that name, kept bold; %(name)s is the vendor's name, or
    # the address of its offer when it names none; %(amount)s %(old)s is
    # its value, %(becomes)s %(new)s that converted.
    return gettext(
        "The <strong>Units per order</strong> from %(name)s would change "
        "from %(amount)s %(old)s to %(becomes)s %(new)s",
        **values,
    )


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
    if (problem := short_problem(short, read_size)) is not None:
        problems[short_box] = problem
    if not isinstance(size, Decimal) or isinstance(short, str):
        return None
    return unit_recounts.Sized(size, short)
