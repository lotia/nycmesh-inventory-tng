"""What the ledger says, as of a moment: a place's latest ledger
transaction, read with no save there under way (moment()), and what the
records said was at a place, or left in an open package, as of one.

The ledger's core (stock/ledger.py) writes and locks; these only read it,
and import the core, never the reverse.
"""

from collections.abc import Collection, Sequence
from decimal import Decimal

from sqlalchemy import ColumnElement, case, func, or_, select
from sqlalchemy.orm import Session

from inventory_tng.models.stock import LedgerTransaction, Movement
from inventory_tng.stock.balance import balance
from inventory_tng.stock.ledger import Form, lock_place


def moment(session: Session, location_id: int) -> int:
    """Now, at a place, as a count form shows it: its latest ledger
    transaction, with no save there under way, so none can commit later
    with a lower id. The lock is held only for the read."""
    with session.begin_nested() as reading:
        lock_place(session, location_id)
        latest = session.scalar(
            select(func.max(LedgerTransaction.id)).where(at_place(location_id))
        )
        # Rolling back to the savepoint releases the lock.
        reading.rollback()
    return latest or 0


# A ledger transaction that puts right what the records show.
_CORRECTS = LedgerTransaction.kind.in_(("count", "adjustment"))


def left_at(session: Session, open_id: int, as_of: int) -> Decimal:
    """What an open package showed just after the ledger transaction
    `as_of`, wherever it was, carried forward by the counts and adjustments
    of it since, which put right what it showed: all that went into it less
    all that left it. A save holds the open packages it names before it
    draws its id (ledger._hold_forms), so none naming it commits later with a
    lower one."""
    return _into_open(session, open_id, or_(LedgerTransaction.id <= as_of, _CORRECTS))


def corrected_since(session: Session, open_id: int, after: int) -> Decimal:
    """What the counts and adjustments of an open package after the ledger
    transaction `after` put right of what it showed."""
    return _into_open(session, open_id, LedgerTransaction.id > after, _CORRECTS)


def _into_open(session: Session, open_id: int, *where: ColumnElement[bool]) -> Decimal:
    """All that went into an open package less all that left it, in the
    ledger transactions `where` picks."""
    # A whole move names it on both sides: in and out, nothing changed.
    signed = case(
        (Movement.to_open_package_id == open_id, Movement.quantity), else_=0
    ) - case((Movement.from_open_package_id == open_id, Movement.quantity), else_=0)
    total = session.scalar(
        select(func.coalesce(func.sum(signed), 0))
        .select_from(Movement)
        .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
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
    return or_(
        LedgerTransaction.to_location_id == location_id,
        LedgerTransaction.from_location_id == location_id,
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
    that window, of `items` only, if given. One query, however many."""
    into = LedgerTransaction.to_location_id == location_id
    out = LedgerTransaction.from_location_id == location_id
    windows = (window, *more)
    # An opening changes the form of stock at a place, not how much is there.
    where = [at_place(location_id), or_(*windows), LedgerTransaction.kind != "open"]
    if items is not None:
        where.append(Movement.item_id.in_(items))
    rows = session.execute(
        select(
            Movement.item_id,
            *(
                func.sum(Movement.quantity).filter(each, side)
                for each in windows
                for side in (into, out)
            ),
        )
        .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
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


def expected(
    session: Session,
    location_id: int,
    *,
    as_of: int | None = None,
    items: Collection[int] | None = None,
) -> dict[int, Decimal]:
    """What the records say is at a place, by item: only what is not zero,
    as balance says now; with as_of, what they said at that moment
    (see moment()), of `items` only if given."""
    if as_of is not None:
        (moved,) = flows(
            session, location_id, LedgerTransaction.id <= as_of, items=items
        )
        return {
            item: came - went for item, (came, went) in moved.items() if came != went
        }
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
                *(func.sum(Movement.quantity).filter(each) for each in windows),
            )
            .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
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


def expected_forms(
    session: Session, location_id: int, as_of: int, items: Collection[int]
) -> dict[tuple[int, Form], Decimal]:
    """What the records said was at a place at moment `as_of`, by item and
    form, of `items`: only what was not zero."""
    (moved,) = form_flows(session, location_id, (LedgerTransaction.id <= as_of,), items)
    return {form: came - went for form, (came, went) in moved.items() if came != went}
