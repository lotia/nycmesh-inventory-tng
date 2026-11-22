"""Where what left some places between two moments went, by the ledger,
and the areas gone from a count form as they are now (areas_leaving):
what a count form says of a retired area emptied while it was counted
(stock/count_gone_areas.py), and what its save sets aside and fixes where
it went (stock/count_arrivals.py; Ali, 2026-11-22, inventory-tng-ygk5). It
only reads, taking no lock."""

from collections.abc import Collection
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from inventory_tng.core.phrases import PATH_BETWEEN
from inventory_tng.models.places import Location
from inventory_tng.models.stock import LedgerTransaction, Movement
from inventory_tng.stock import queries
from inventory_tng.stock.count_forms import Gone
from inventory_tng.stock.ledger import LOOSE, Form


class MovedOut(NamedTuple):
    """So much of an item that left a place for another, or for none (used,
    lost or written off), and the form it arrived in there (loose for
    none)."""

    from_id: int
    to_id: int | None
    item_id: int
    quantity: Decimal
    arrives: Form = LOOSE


def moved_out(
    session: Session,
    location_ids: Collection[int],
    after: int,
    *,
    up_to: int | None = None,
    into: Collection[int] = (),
) -> list[MovedOut]:
    """What left each of the places in the ledger transactions after
    `after`, by where it went, item and the form it arrived in, in all:
    an opening, which changes only the form of stock at a place, is no
    move. What went to the places `into` only up to the moment `up_to`,
    if given. In order of place, then where it went, then
    item, then form, by id."""
    went = LedgerTransaction.from_location_id
    to = LedgerTransaction.to_location_id
    package, opened = Movement.to_package_id, Movement.to_open_package_id
    query = (
        select(went, to, Movement.item_id, package, opened, func.sum(Movement.quantity))
        .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
        .where(
            went.in_(list(location_ids)),
            LedgerTransaction.id > after,
            LedgerTransaction.kind != "open",
        )
        .group_by(went, to, Movement.item_id, package, opened)
        .order_by(
            went,
            to.nulls_last(),
            Movement.item_id,
            package.nulls_first(),
            opened.nulls_first(),
        )
    )
    if up_to is not None:
        query = query.where(
            or_(LedgerTransaction.id <= up_to, to.is_(None), to.not_in(list(into)))
        )
    # Each from one of the places, so from somewhere.
    return [
        MovedOut(from_id, to_id, item_id, quantity, Form(package_id, open_id))
        for from_id, to_id, item_id, package_id, open_id, quantity in session.execute(
            query
        )
        if from_id is not None
    ]


class Leaving(NamedTuple):
    """The areas gone from a count form as they are now: each one's label,
    by id, as the form names it; what left them since the form was first
    shown, by the ledger (moved_out); which are still retired; and the
    order of the places in the tree, to say them in."""

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


def areas_leaving(session: Session, root: Location, gone: Gone) -> Leaving:
    """The areas gone from a count form of `root`, and what left them."""
    tree = queries.place_tree(session, root)
    order = {each.id: n for n, (each, _, _) in enumerate(tree)}
    # As the count form names it (count_groups.label): by its path.
    found = {
        each.id: (each, PATH_BETWEEN.join(path or (each.name,)))
        for each, path, _ in tree
        if each.id in gone.ids
    }
    if missing := set(gone.ids) - found.keys():
        # One moved out of the place since: under its own name.
        for each in queries.places_with_ids(session, missing):
            found[each.id] = (each, each.name)
    return Leaving(
        {area_id: said for area_id, (_, said) in found.items()},
        moved_out(session, gone.ids, gone.since),
        {
            area_id
            for area_id, (each, _) in found.items()
            if each.retired_at is not None
        },
        order,
    )
