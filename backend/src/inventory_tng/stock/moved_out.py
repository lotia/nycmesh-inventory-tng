"""Where what left some places after a moment went, by the ledger: what a
count form says of a retired area emptied while it was counted
(stock/counts_routes.py, Ali, 2026-11-22, inventory-tng-ygk5). It only
reads the ledger, taking no lock."""

from collections.abc import Collection
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng.models.stock import LedgerTransaction, Movement


class MovedOut(NamedTuple):
    """So much of an item that left a place for another, or for none (used,
    lost or written off)."""

    from_id: int
    to_id: int | None
    item_id: int
    quantity: Decimal


def moved_out(
    session: Session, location_ids: Collection[int], after: int
) -> list[MovedOut]:
    """What left each of the places in the ledger transactions after
    `after`, by where it went and item, in all: an opening, which changes
    only the form of stock at a place, is no move. In order of place,
    then where it went, then item, by id."""
    went = LedgerTransaction.from_location_id
    to = LedgerTransaction.to_location_id
    rows = session.execute(
        select(went, to, Movement.item_id, func.sum(Movement.quantity))
        .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
        .where(
            went.in_(list(location_ids)),
            LedgerTransaction.id > after,
            LedgerTransaction.kind != "open",
        )
        .group_by(went, to, Movement.item_id)
        .order_by(went, to.nulls_last(), Movement.item_id)
    )
    # Each from one of the places, so from somewhere.
    return [
        MovedOut(from_id, to_id, item_id, quantity)
        for from_id, to_id, item_id, quantity in rows
        if from_id is not None
    ]
