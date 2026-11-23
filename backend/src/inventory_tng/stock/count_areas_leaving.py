"""The areas gone from a count form (count_forms.Gone) as they are now:
how the form names each, which are still retired, and what left them
since the form was first shown, by the ledger (moved_out), of which the
form shows, and its save sets aside, what went to its places up to its
moment (Ali, 2026-11-22, inventory-tng-ygk5). What the form says of them
is stock/count_gone_areas.py's; what its save does with what arrived,
stock/count_arrivals.py's. It only reads, taking no lock."""

from collections.abc import Collection, Sequence
from typing import NamedTuple

from sqlalchemy.orm import Session

from inventory_tng.stock import queries
from inventory_tng.stock.count_forms import Gone
from inventory_tng.stock.moved_out import MovedOut, moved_out
from inventory_tng.stock.queries import PlaceTree


class Leaving(NamedTuple):
    """The areas gone from a count form as they are now: each one's label,
    by id, as the form names it; what left them since the form was first
    shown (moves_since); which are still retired; and the order of the
    places in the tree, to say them in."""

    labels: dict[int, str]
    moves: list[MovedOut]
    retired: set[int]
    order: dict[int, int]

    @property
    def emptied(self) -> set[int]:
        """The areas anything left."""
        return {move.from_id for move in self.moves}

    def ordered(self, ids: Collection[int]) -> list[int]:
        """The areas of `ids` in the tree's order."""
        return sorted(ids, key=lambda each: self.order.get(each, 0))

    def said(self, ids: Collection[int]) -> list[str]:
        """The labels of the areas of `ids`, in the tree's order."""
        return [self.labels[area_id] for area_id in self.ordered(ids)]


def moves_since(session: Session, gone: Gone, as_of: int) -> list[MovedOut]:
    """What left the areas gone from a count form after it was first
    shown (moved_out), split at its moment `as_of`: one read, of which the
    form shows, and its save sets aside, what went to its places up to
    that moment (as_shown). None if no area is gone."""
    if not gone.ids:
        return []
    return moved_out(session, gone.ids, gone.since, split=as_of)


def as_shown(moves: Sequence[MovedOut], places: Collection[int]) -> list[MovedOut]:
    """Of `moves` (moves_since), what a count form of the `places` shows:
    what went to them up to the moment the moves were split at, and what
    went anywhere else whenever."""
    return [
        move
        for move in moves
        if not move.later or move.to_id is None or move.to_id not in places
    ]


def areas_leaving(
    session: Session,
    gone: Gone,
    tree: PlaceTree,
    moves: list[MovedOut],
) -> Leaving:
    """The areas gone from a count form, of its place's `tree`
    (queries.place_tree), and `moves`, what left them (moves_since)."""
    order = {each.id: n for n, (each, _, _) in enumerate(tree)}
    found = {
        each.id: (each, queries.place_label(each, path))
        for each, path, _ in tree
        if each.id in gone.ids
    }
    if missing := set(gone.ids) - found.keys():
        # One moved out of the place since: under its own name.
        for each in queries.places_with_ids(session, missing):
            found[each.id] = (each, each.name)
    return Leaving(
        {area_id: said for area_id, (_, said) in found.items()},
        moves,
        {
            area_id
            for area_id, (each, _) in found.items()
            if each.retired_at is not None
        },
        order,
    )
