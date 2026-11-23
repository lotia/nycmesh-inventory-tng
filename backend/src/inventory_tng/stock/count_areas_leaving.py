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

from inventory_tng.models.places import Location
from inventory_tng.stock import queries
from inventory_tng.stock.count_forms import Gone
from inventory_tng.stock.moved_out import MovedOut, moved_out


class Leaving(NamedTuple):
    """The areas gone from a count form as they are now: each one's label,
    by id, as the form names it; what left them since the form was first
    shown, by the ledger (moved_out), split at the moment `as_of`, if it
    was; which are still retired; and the order of the places in the
    tree, to say them in."""

    labels: dict[int, str]
    moves: list[MovedOut]
    retired: set[int]
    order: dict[int, int]
    as_of: int | None = None

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

    def moved_by(self, places: Collection[int]) -> list[MovedOut]:
        """Of what left the areas, what went to the `places` up to the
        moment its moves were split at, and what went anywhere else
        whenever: what moved_out reads up to that moment into those
        places."""
        return [
            move
            for move in self.moves
            if not move.later or move.to_id is None or move.to_id not in places
        ]


def areas_leaving(
    session: Session,
    root: Location,
    gone: Gone,
    as_of: int | None = None,
    tree: Sequence[tuple[Location, tuple[str, ...], bool]] | None = None,
) -> Leaving:
    """The areas gone from a count form of `root`, and what left them,
    split at the moment `as_of`, if given. `tree`, the place's
    (queries.place_tree), if read already."""
    if tree is None:
        tree = queries.place_tree(session, root)
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
        moved_out(session, gone.ids, gone.since, split=as_of),
        {
            area_id
            for area_id, (each, _) in found.items()
            if each.retired_at is not None
        },
        order,
        as_of,
    )


def gone_moves(
    session: Session,
    gone: Gone,
    as_of: int,
    places: Collection[int],
    leaving: Leaving | None = None,
) -> list[MovedOut]:
    """What left the areas gone from a count form after it was first
    shown (moved_out): to the `places` it shows, up to its moment
    `as_of`. Of `leaving`'s moves, if they were split at that moment,
    rather than read again."""
    if not gone.ids:
        return []
    if leaving is not None and leaving.as_of == as_of:
        return leaving.moved_by(places)
    return moved_out(session, gone.ids, gone.since, up_to=as_of, into=places)
