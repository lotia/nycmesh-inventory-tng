"""What the ledger says, as of a moment: the latest ledger transaction at
a place, or at several (a store and its areas), and how many there it
saw (moment()), and what the records said was at a place, or left in an
open package, as of one.

The ledger's core (stock/ledger.py) writes; these only read it, taking no
lock. They import the core.
"""

from collections.abc import Collection, Sequence
from decimal import Decimal
from typing import Any, NamedTuple

from sqlalchemy import (
    ColumnElement,
    Result,
    case,
    func,
    literal,
    or_,
    select,
    union_all,
)
from sqlalchemy.orm import Session

from inventory_tng.catalogue.unit_conversion import at_scale_sql
from inventory_tng.models.catalogue import Item
from inventory_tng.models.stock import AT_ONE_PLACE, LedgerTransaction, Movement
from inventory_tng.stock.balance import balance
from inventory_tng.stock.ledger import Form
from inventory_tng.stock.line_amounts import in_items_unit


class Moment(NamedTuple):
    """Now, at the places a count form shows (one, or a store and its
    areas): their latest ledger transaction, `as_of`, and how many of their
    ledger transactions up to that one it saw, `seen`."""

    as_of: int
    seen: int


def moment(session: Session, *location_ids: int) -> Moment:
    """Now, at the places, read without their locks, so the form holds up
    no save there (inventory-tng-0um). A save under way as it is read may
    commit after it with a lower id, unseen: the count, holding the
    places, finds it by seen_up_to and is refused (counts.record_count)."""
    latest, seen = session.execute(
        select(func.max(LedgerTransaction.id), func.count()).where(
            at_places(location_ids)
        )
    ).one()
    return Moment(latest or 0, seen)


def seen_up_to(session: Session, location_ids: Collection[int], as_of: int) -> int:
    """How many ledger transactions at the places there are up to `as_of`,
    as Moment.seen counts them: a move between two of them once."""
    return session.execute(
        select(func.count()).where(
            at_places(location_ids), LedgerTransaction.id <= as_of
        )
    ).scalar_one()


# A ledger transaction that puts right what the records show.
_CORRECTS = LedgerTransaction.kind.in_(("count", "adjustment"))


def left_at(session: Session, open_id: int, as_of: int) -> Decimal:
    """What an open package showed just after the ledger transaction
    `as_of`, wherever it was, carried forward by the counts and adjustments
    of it since, which put right what it showed: all that went into it less
    all that left it. A save holds the open packages it names before it
    draws its id (ledger.hold_forms), so none naming it commits later with a
    lower one."""
    return _into_open(session, open_id, or_(LedgerTransaction.id <= as_of, _CORRECTS))


def corrected_since(session: Session, open_id: int, after: int) -> Decimal:
    """What the counts and adjustments of an open package after the ledger
    transaction `after` put right of what it showed."""
    return _into_open(session, open_id, LedgerTransaction.id > after, _CORRECTS)


def _into_open(session: Session, open_id: int, *where: ColumnElement[bool]) -> Decimal:
    """All that went into an open package less all that left it, in the
    ledger transactions `where` picks, in its item's unit (line_amounts)."""
    # A whole move names it on both sides: in and out, nothing changed.
    amount = in_items_unit()
    signed = case((Movement.to_open_package_id == open_id, amount), else_=0) - case(
        (Movement.from_open_package_id == open_id, amount), else_=0
    )
    total = session.scalar(
        select(func.coalesce(at_scale_sql(func.sum(signed)), 0))
        .select_from(Movement)
        .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
        .join(Item, Item.id == Movement.item_id)
        .where(
            or_(
                Movement.to_open_package_id == open_id,
                Movement.from_open_package_id == open_id,
            ),
            *where,
        )
    )
    return Decimal(total or 0)


def at_place(location_id: int) -> ColumnElement[bool]:
    """A ledger transaction into or out of a place."""
    return at_places([location_id])


def at_places(location_ids: Collection[int]) -> ColumnElement[bool]:
    """A ledger transaction into or out of any of the places."""
    ids = list(location_ids)
    return or_(
        LedgerTransaction.to_location_id.in_(ids),
        LedgerTransaction.from_location_id.in_(ids),
    )


def flows(
    session: Session,
    location_id: int,
    window: ColumnElement[bool],
    *more: ColumnElement[bool],
    items: Collection[int] | None = None,
) -> list[dict[int, tuple[Decimal, Decimal]]]:
    """For each window (which ledger transactions it takes), what came into
    a place and what went out of it, by item: each item that moved there in
    that window, of `items` only, if given, in its unit (line_amounts). One
    query, however many."""
    into = LedgerTransaction.to_location_id == location_id
    out = LedgerTransaction.from_location_id == location_id
    windows = (window, *more)
    # An opening or a loosening changes the form of stock at a place, not
    # how much is there.
    where = [
        at_place(location_id),
        or_(*windows),
        LedgerTransaction.kind.not_in(AT_ONE_PLACE),
    ]
    if items is not None:
        where.append(Movement.item_id.in_(items))
    rows = session.execute(
        select(
            Movement.item_id,
            *(
                at_scale_sql(func.sum(in_items_unit()).filter(each, side))
                for each in windows
                for side in (into, out)
            ),
        )
        .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
        .join(Item, Item.id == Movement.item_id)
        .where(*where)
        .group_by(Movement.item_id)
    )
    by_window: list[dict[int, tuple[Decimal, Decimal]]] = [{} for _ in windows]
    for item_id, *sums in rows:
        # A sum over no movements is NULL: the item did not move in that one.
        for flow, came, went in zip(by_window, sums[::2], sums[1::2], strict=True):
            if came is not None or went is not None:
                flow[item_id] = (came or Decimal(0), went or Decimal(0))
    return by_window


def expected(session: Session, location_id: int) -> dict[int, Decimal]:
    """What the records say is at a place now, by item: only what is not
    zero, as balance says; expected_at says what they said at a moment."""
    query = select(balance.c.item_id, balance.c.quantity).where(
        balance.c.location_id == location_id, balance.c.quantity != 0
    )
    return {item_id: amount for item_id, amount in session.execute(query)}


def form_flows(
    session: Session,
    location_id: int,
    windows: Sequence[ColumnElement[bool]],
    items: Collection[int],
) -> list[dict[tuple[int, Form], tuple[Decimal, Decimal]]]:
    """flows(), by form too: for each window, what came into a place and
    went out of it, by item and the form it was in there. Openings are
    moves here, as they change forms."""
    by_window: list[dict[tuple[int, Form], tuple[Decimal, Decimal]]] = [
        {} for _ in windows
    ]
    if not items:
        return by_window
    sides = (
        (
            True,
            LedgerTransaction.to_location_id,
            Movement.to_package_id,
            Movement.to_open_package_id,
        ),
        (
            False,
            LedgerTransaction.from_location_id,
            Movement.from_package_id,
            Movement.from_open_package_id,
        ),
    )
    for into, place, package, opened in sides:
        rows = session.execute(
            select(
                Movement.item_id,
                package,
                opened,
                *(
                    at_scale_sql(func.sum(in_items_unit()).filter(each))
                    for each in windows
                ),
            )
            .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
            .join(Item, Item.id == Movement.item_id)
            .where(place == location_id, or_(*windows), Movement.item_id.in_(items))
            .group_by(Movement.item_id, package, opened)
        )
        for item_id, package_id, open_id, *sums in rows:
            for flow, total in zip(by_window, sums, strict=True):
                if total is None:
                    continue
                form = (item_id, Form(package_id, open_id))
                came, went = flow.get(form, (Decimal(0), Decimal(0)))
                flow[form] = (came + total, went) if into else (came, went + total)
    return by_window


def _signed_at(
    location_ids: Collection[int], as_of: int, *columns: str
) -> tuple[ColumnElement[Any], ...]:
    """Each movement up to the moment `as_of` at the places, once at each
    end there: the place, the item, its `columns` on that side (to_ or
    from_), and its quantity in its item's unit (line_amounts), more for
    in, less for out. A subquery's
    columns, by name: place, item_id, the columns unprefixed, and quantity.
    An opening, both ends at one place, nets to nothing but by form."""
    ids = list(location_ids)
    sides = []
    for side, sign in (("to", 1), ("from", -1)):
        place = getattr(LedgerTransaction, f"{side}_location_id")
        sides.append(
            select(
                place.label("place"),
                Movement.item_id,
                *(
                    getattr(Movement, f"{side}_{column}").label(column)
                    for column in columns
                ),
                (in_items_unit() * literal(sign)).label("quantity"),
            )
            .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
            .join(Item, Item.id == Movement.item_id)
            .where(place.in_(ids), LedgerTransaction.id <= as_of)
        )
    return tuple(union_all(*sides).subquery().c)


def expected_at(
    session: Session,
    location_ids: Collection[int],
    as_of: int,
    items: Collection[int] | None = None,
) -> dict[int, dict[int, Decimal]]:
    """What the records said was at each place at moment `as_of`, by place
    and item, of `items` only if given: only what was not zero. One query,
    however many places."""
    found: dict[int, dict[int, Decimal]] = {}
    for place_id, item, amount in _netted_at(session, location_ids, as_of, items):
        found.setdefault(place_id, {})[item] = amount
    return found


def expected_forms_at(
    session: Session, location_ids: Collection[int], as_of: int, items: Collection[int]
) -> dict[int, dict[tuple[int, Form], Decimal]]:
    """expected_at, by form too: what the records said was at each place at
    moment `as_of`, by place, item and form, of `items`. One query."""
    if not items:
        return {}
    rows = _netted_at(
        session, location_ids, as_of, items, "package_id", "open_package_id"
    )
    found: dict[int, dict[tuple[int, Form], Decimal]] = {}
    for place_id, item, package_id, open_id, amount in rows:
        found.setdefault(place_id, {})[(item, Form(package_id, open_id))] = amount
    return found


def _netted_at(
    session: Session,
    location_ids: Collection[int],
    as_of: int,
    items: Collection[int] | None,
    *columns: str,
) -> Result[*tuple[Any, ...]]:
    """_signed_at summed by place, item and `columns`, of `items` only if
    given: each row's place, item, columns and amount, where not zero."""
    place, item_id, *forms, quantity = _signed_at(location_ids, as_of, *columns)
    total = at_scale_sql(func.sum(quantity))
    query = (
        select(place, item_id, *forms, total)
        .group_by(place, item_id, *forms)
        .having(total != 0)
    )
    if items is not None:
        query = query.where(item_id.in_(list(items)))
    return session.execute(query)
