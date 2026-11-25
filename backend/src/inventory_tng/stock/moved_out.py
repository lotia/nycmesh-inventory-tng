"""Where what left some places between two moments went, by the ledger.
It only reads, taking no lock."""

from collections.abc import Collection
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng.models.catalogue import Item
from inventory_tng.models.stock import AT_ONE_PLACE, LedgerTransaction, Movement
from inventory_tng.stock.ledger import LOOSE, Form
from inventory_tng.stock.line_amounts import in_items_unit


class MovedOut(NamedTuple):
    """So much of an item that left a place for another, or for none (used,
    lost or written off), and the form it arrived in there (loose for
    none); and whether it left after the moment the read was split at
    (moved_out's `split`)."""

    from_id: int
    to_id: int | None
    item_id: int
    quantity: Decimal
    arrives: Form = LOOSE
    later: bool = False


def moved_out(
    session: Session,
    location_ids: Collection[int],
    after: int,
    *,
    split: int | None = None,
) -> list[MovedOut]:
    """What left each of the places in the ledger transactions after
    `after`, by where it went, item and the form it arrived in, in all,
    in its unit (line_amounts):
    an opening or a loosening, which changes only the form of stock at a
    place, is no move. Split at the moment `split`, if given, into what left up to it
    and what left after it (MovedOut.later). In order of place, then
    where it went, then item, then form, by id, then up to the moment
    first."""
    went = LedgerTransaction.from_location_id
    to = LedgerTransaction.to_location_id
    package, opened = Movement.to_package_id, Movement.to_open_package_id
    by = [went, to, Movement.item_id, package, opened]
    if split is not None:
        by.append(LedgerTransaction.id > split)
    query = (
        select(*by, func.sum(in_items_unit()))
        .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
        .join(Item, Item.id == Movement.item_id)
        .where(
            went.in_(list(location_ids)),
            LedgerTransaction.id > after,
            LedgerTransaction.kind.not_in(AT_ONE_PLACE),
        )
        .group_by(*by)
        .order_by(
            went,
            to.nulls_last(),
            Movement.item_id,
            package.nulls_first(),
            opened.nulls_first(),
            *by[5:],
        )
    )
    # Each from one of the places, so from somewhere.
    return [
        MovedOut(from_id, to_id, item_id, quantity, Form(package_id, open_id), *later)
        for from_id, to_id, item_id, package_id, open_id, *later, quantity in (
            session.execute(query)
        )
        if from_id is not None
    ]
