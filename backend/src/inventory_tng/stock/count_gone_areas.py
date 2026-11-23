"""What a count form says of the areas gone from it (count_forms.Gone):
retired, or retired and emptied, while it was counted, so no longer on it
(Ali, 2026-11-22, inventory-tng-ygk5). Why the save was refused, and,
until the form is saved, what left them since it was first shown
(count_areas_leaving.areas_leaving): what went to a place on it under
"Moved to …", a part for each area it came from, each item with the
amount moved and a box to fix it (what the save does with it is
stock/count_arrivals.py's); what went elsewhere, with nothing to type;
which may be brought back; and what Found at says of those it leaves
out. The routes are stock/count_routes.py's."""

from collections.abc import Collection, Mapping, Sequence
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy.orm import Session

from inventory_tng.core.phrases import gettext, joined, ngettext
from inventory_tng.models.catalogue import Item, ItemPackage
from inventory_tng.models.places import Location
from inventory_tng.stock import queries
from inventory_tng.stock.count_areas_leaving import Leaving
from inventory_tng.stock.count_forms import ARRIVED, LOOSE_FIELD
from inventory_tng.stock.count_groups import (
    FormRow,
    Group,
    form_order,
    form_row,
)
from inventory_tng.stock.ledger import Form
from inventory_tng.stock.moved_out import MovedOut


def emptied_meanwhile(places: Sequence[str]) -> str:
    """A count refused whole as retired areas on it were emptied while it
    was counted, so they may no longer be counted."""
    # Translators: %(places)s names one or more places, each of any kind,
    # such as a store, a shelf, a bin or a volunteer's home.
    return ngettext(
        "Not saved: %(places)s was emptied while you counted, so the records "
        "show no items there now. Move details are shown below, and your other "
        "counts are kept. Confirm and save again.",
        "Not saved: %(places)s were emptied while you counted, so the records "
        "show no items there now. Move details are shown below, and your other "
        "counts are kept. Confirm and save again.",
        len(places),
        places=joined(places),
    )


def retired_meanwhile(places: Sequence[str]) -> str:
    """A count refused whole as empty areas on it were retired while it was
    counted, nothing moving out of them."""
    # Translators: %(places)s names one or more places, each of any kind,
    # such as a store, a shelf, a bin or a volunteer's home.
    return ngettext(
        "Not saved: %(places)s was retired while you counted, so it is no "
        "longer on this form. What you typed for the other areas is kept: check "
        "it and save again.",
        "Not saved: %(places)s were retired while you counted, so they are no "
        "longer on this form. What you typed for the other areas is kept: check "
        "it and save again.",
        len(places),
        places=joined(places),
    )


def moved_to(place: str) -> str:
    """The heading of what went to a place on a count form from the areas
    gone from it."""
    # Translators: %(place)s is a place of any kind, such as a store, a shelf,
    # a bin or a volunteer's home.
    return gettext("Moved to %(place)s", place=place)


def came_from(place: str) -> str:
    """Under Moved to: the heading of what came from one area gone from
    the form."""
    # Translators: %(place)s is a place of any kind, such as a store, a shelf,
    # a bin or a volunteer's home.
    return gettext("From %(place)s", place=place)


def taken_out(place: str) -> str:
    """The heading of what left areas gone from a count form for no place
    on it."""
    # Translators: %(place)s is a place of any kind, such as a store, a shelf,
    # a bin or a volunteer's home.
    return gettext("Taken out of %(place)s", place=place)


def removed(place: str) -> str:
    """The heading of an empty area retired while it was counted."""
    # Translators: %(place)s is a place of any kind, such as a store, a shelf,
    # a bin or a volunteer's home.
    return gettext("%(place)s (retired): removed from this form.", place=place)


def not_offered(places: Sequence[str]) -> str:
    """Beside where something was found: the areas gone from the form, not
    offered."""
    # Translators: %(places)s names one or more places, each of any kind,
    # such as a store, a shelf, a bin or a volunteer's home.
    return ngettext(
        "%(places)s isn't offered: it's retired.",
        "%(places)s aren't offered: they're retired.",
        len(places),
        places=joined(places),
    )


def gone_words(leaving: Leaving, ids: Collection[int]) -> list[str]:
    """What a count form says of the areas of `ids`, just gone from it, one
    alert under another: those anything left, emptied while it was
    counted; the others, retired (Ali, 2026-11-22)."""
    emptied = [area_id for area_id in ids if area_id in leaving.emptied]
    retired = [area_id for area_id in ids if area_id not in leaving.emptied]
    return [
        *([emptied_meanwhile(leaving.said(emptied))] if emptied else []),
        *([retired_meanwhile(leaving.said(retired))] if retired else []),
    ]


class MovedItem(NamedTuple):
    """An item moved from an area gone from a count form to a place on it:
    how much was moved, in its unit; the prefix of its boxes of how many
    arrived (count_forms.ARRIVED); and a row for each form it arrived in, as the
    count form asks for that form (count_groups.form_row), its amount what
    arrived in it as moved."""

    item: Item
    moved: Decimal
    prefix: str
    rows: list[FormRow]

    @property
    def worded(self) -> bool:
        """Whether each box says its form: not when it all arrived loose."""
        return any(not row.field.startswith(LOOSE_FIELD) for row in self.rows)


class MovedTo(NamedTuple):
    """What went to one place on a count form from the areas gone from it:
    its heading; the place, and its label as the form names it; a part of
    each area it came from, its heading and the items; and, of each item,
    what the place holds once saved if nothing else is typed, the running
    total's start."""

    heading: str
    place: Location
    label: str
    parts: list[tuple[str, list[MovedItem]]]
    totals: list[tuple[Item, Decimal]]


class Moves(NamedTuple):
    """What a count form shows of what left the areas gone from it: a
    section of each place on it that something went to (MovedTo); what
    went nowhere on it, with its heading; and, of the areas gone, those
    still retired that anything left, each with its label, to bring back;
    the headings of those nothing left; what Found at says of those it
    leaves out; and those back now."""

    sections: list[MovedTo]
    taken: tuple[str, list[tuple[Item, Decimal]]] | None
    to_bring_back: list[tuple[int, str]]
    removed: list[str]
    not_offered: str
    back: set[int]

    def adds_to(self, place_id: int, item_id: int) -> str:
        """What ties the running total of an item at a place to the boxes
        it adds up, in the page: none if it has none."""
        return (
            f"{place_id}-{item_id}"
            if any(
                section.place.id == place_id and item.id == item_id
                for section in self.sections
                for item, _ in section.totals
            )
            else ""
        )


def _by_name(rows: Collection[tuple[Item, Decimal]]) -> list[tuple[Item, Decimal]]:
    return sorted(rows, key=lambda row: row[0].name.lower())


def moved_sections(
    session: Session,
    root: Location,
    groups: list[Group],
    leaving: Leaving,
    moves: Sequence[MovedOut],
    named: Mapping[int, Item],
    packages: Mapping[int, ItemPackage],
) -> Moves:
    """What a count form shows of what left the areas gone from it
    (Moves): `moves`, of the items it shows, as of its moment, and its
    groups, whose lines count what each place held before them; `packages`,
    by id, every package of the items moved, at least."""
    by_id = {group.place.id: group for group in groups}
    went: dict[int, dict[int, dict[int, dict[Form, Decimal]]]] = {}
    taken: dict[int, Decimal] = {}
    for move in moves:
        if move.to_id in by_id:
            items = went.setdefault(move.to_id, {}).setdefault(move.from_id, {})
            forms = items.setdefault(move.item_id, {})
            forms[move.arrives] = forms.get(move.arrives, Decimal(0)) + move.quantity
        else:
            taken[move.item_id] = taken.get(move.item_id, Decimal(0)) + move.quantity
    open_ids = {move.arrives.open_package_id for move in moves}
    opened = {
        row.id: row
        for row in queries.open_with_ids(
            session, [open_id for open_id in open_ids if open_id is not None]
        )
    }

    def moved_item(prefix: str, item: Item, forms: dict[Form, Decimal]) -> MovedItem:
        rows = [
            form_row(item.id, form, forms[form], packages, opened)
            for form in sorted(forms, key=lambda form: form_order(form, packages))
        ]
        return MovedItem(item, sum(forms.values(), Decimal(0)), prefix, rows)

    sections: list[MovedTo] = []
    for group in groups:
        if group.place.id not in went:
            continue
        sources = went[group.place.id]
        parts = [
            (
                came_from(leaving.labels[from_id]),
                sorted(
                    (
                        moved_item(
                            f"{group.prefix}{ARRIVED}{from_id}_", named[item_id], forms
                        )
                        for item_id, forms in sources[from_id].items()
                    ),
                    key=lambda each: each.item.name.lower(),
                ),
            )
            for from_id in leaving.ordered(sources)
        ]
        held = {item.id: n for item, n in group.rows}
        moved: dict[int, Decimal] = {}
        for items in sources.values():
            for item_id, forms in items.items():
                n = sum(forms.values(), Decimal(0))
                moved[item_id] = moved.get(item_id, Decimal(0)) + n
        totals = _by_name(
            [
                (named[item_id], held.get(item_id, Decimal(0)) + n)
                for item_id, n in moved.items()
            ]
        )
        sections.append(
            MovedTo(moved_to(group.label), group.place, group.label, parts, totals)
        )
    gone = set(leaving.labels)
    still_retired = gone & leaving.retired
    emptied = leaving.emptied
    taken_rows = _by_name([(named[item_id], n) for item_id, n in taken.items()])
    return Moves(
        sections,
        (taken_out(root.name), taken_rows) if taken else None,
        [
            (area_id, leaving.labels[area_id])
            for area_id in leaving.ordered(still_retired & emptied)
        ],
        [removed(each) for each in leaving.said(still_retired - emptied)],
        not_offered(leaving.said(still_retired)) if still_retired else "",
        gone - leaving.retired,
    )
