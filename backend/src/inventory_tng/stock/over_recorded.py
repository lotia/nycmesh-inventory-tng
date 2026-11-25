"""Lines recorded with more than was moved (docs/decisions/0020,
inventory-tng-0gg): a scan page's batch, changed after another tab recorded
it under the same key, is refused (409 key_reused), and what it took off a
line already recorded is kept here, for an administrator to correct. The
ledger never changes, so the volunteer cannot take it back; an
administrator corrects it with adjustments that undo the difference on
each side, or marks it checked, as "Needs your action" on the home page
asks. A count of its item at one of its ends, as of a moment after it,
puts that end right; one counted at every end it has closes as counted
(counted(); Ali, 2026-11-15, inventory-tng-qs8). Every write to the
over_recorded table, and what a save reads to make one (the places to
lock, the openings a count asks after); what the over-recorded pages
list and show is in stock/over_recorded_queries.py."""

import uuid
from collections.abc import Collection, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Literal, NamedTuple

import structlog
from opentelemetry import metrics
from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from inventory_tng.core.db_errors import RuleBroken
from inventory_tng.core.db_locking import get_locked, locked
from inventory_tng.core.db_once_committed import once_committed
from inventory_tng.core.phrases import lazy_gettext
from inventory_tng.models.stock import (
    LedgerTransaction,
    Movement,
    OpenPackage,
    OverRecorded,
)
from inventory_tng.stock import adjustment_reasons, counts, ledger, moments
from inventory_tng.web.formatting import plain_amount

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

# The reason the adjustments correcting one give.
REASON = "Recorded more than moved"

Settled = Literal["corrected", "checked", "counted"]

# Why one marked checked is right as it is, as the page says each
# (models.stock.OVER_RECORDED_REASONS; Ali, 2026-11-15, inventory-tng-zgy).
# Mark checked is the only way of settling one that writes nothing, so a bare
# one could mean fixed elsewhere, nothing fixed, or one end fixed.
CHECKED_REASONS = {
    "left": lazy_gettext("Counted where it left"),
    "arrived": lazy_gettext("Counted where it arrived"),
    "both": lazy_gettext("Counted both ends"),
    # Translators: followed by the administrator's own words.
    "other": lazy_gettext("Put right another way:"),
}

raised_counter = meter.create_counter(
    "inventory.over_recorded.raised",
    unit="{line}",
    description="Lines recorded with more than was moved, as a batch changed "
    "after another tab recorded it said, raised or said again for an "
    "administrator to correct",
)
settled_counter = meter.create_counter(
    "inventory.over_recorded.settled",
    unit="{line}",
    description="Lines recorded with more than was moved that were settled, "
    "by how: corrected with adjustments, marked checked, then by why it was "
    "right as it was, or counted at every end",
)
brought_back_counter = meter.create_counter(
    "inventory.over_recorded.places_brought_back",
    unit="{location}",
    description="Retired places an administrator brought back from Recorded "
    "more than moved, to correct a line there",
)

WAITING = OverRecorded.settled_at.is_(None)


def raise_checks(session: Session, over: Mapping[int, Decimal]) -> None:
    """Keep, for each movement, how much more of it was recorded than moved:
    a new check, or one waiting told the newest amount; one settled stays
    settled. The caller commits."""
    if not over:
        return
    rows = [
        {"movement_id": movement_id, "quantity": quantity}
        for movement_id, quantity in sorted(over.items())
    ]
    statement = insert(OverRecorded).values(rows)
    changed = session.execute(
        statement.on_conflict_do_update(
            index_elements=[OverRecorded.movement_id],
            set_={"quantity": statement.excluded.quantity},
            where=WAITING,
        ).returning(OverRecorded.id)
    ).all()
    for (check_id,) in changed:
        once_committed(
            session, log.info, "over recorded raised", over_recorded_id=check_id
        )
    if changed:
        once_committed(session, raised_counter.add, len(changed))


def _measure_settled(
    session: Session, how: Settled, count: int = 1, reason: str | None = None
) -> None:
    """Those settled, measured once committed; marked checked, by its
    reason (CHECKED_REASONS), never its words."""
    if count:
        attributes = {"how": how} | ({"reason": reason} if reason else {})
        once_committed(session, settled_counter.add, count, attributes)


def brought_back(
    session: Session, check_id: int, location_id: int, admin_id: int
) -> None:
    """A retired place brought back to correct one there: measured and
    logged once committed."""
    once_committed(session, brought_back_counter.add, 1)
    once_committed(
        session,
        log.info,
        "over recorded place brought back",
        over_recorded_id=check_id,
        location_id=location_id,
        admin_id=admin_id,
    )


def seen(row: OverRecorded) -> str:
    """What an administrator saw of a check, to settle it only as seen: a
    later batch may say another amount meanwhile, and a count may put one
    of its ends right, so Correct would write only the other."""
    ends = (("left", row.left_count_id), ("arrived", row.arrived_count_id))
    return " ".join([plain_amount(row.quantity), *(end for end, by in ends if by)])


# The open package an opening arrived in, outer-joined to say if it closed
# since: closing it, however, wrote off what it showed, so put that end
# right (Ali, 2026-11-15, inventory-tng-nyl). A move's is never joined.
OPENED_INTO = and_(
    LedgerTransaction.kind == "open", OpenPackage.id == Movement.to_open_package_id
)

# The transaction that made what was left on OpenPackage loose units at its
# place, as turning following off does (open_packages.close_unfollowed), or
# NULL. That writes nothing off, so puts no end right: a count of the item
# there after it does (Ali, 2026-11-24, inventory-tng-n7dj). One made loose
# with nothing left on it has none, so reads as closed: nothing became loose.
_LOOSENING, _LOOSENED = aliased(LedgerTransaction), aliased(Movement)
LOOSENED_IN = (
    select(_LOOSENING.id)
    .join(_LOOSENED, _LOOSENED.transaction_id == _LOOSENING.id)
    .where(
        _LOOSENING.kind == "loosen",
        or_(
            _LOOSENED.from_open_package_id == OpenPackage.id,
            _LOOSENED.to_open_package_id == OpenPackage.id,
        ),
    )
    .correlate(OpenPackage)
    .scalar_subquery()
)
# When an opening's open package (OPENED_INTO) was closed by a write-off
# (used up, lost: open_packages.close), and when made loose instead; each
# NULL otherwise.
WRITTEN_OFF_ON = case((LOOSENED_IN.is_(None), OpenPackage.closed_at)).label(
    "written_off_on"
)
MADE_LOOSE_ON = case((LOOSENED_IN.is_not(None), OpenPackage.closed_at)).label(
    "made_loose_on"
)


def moved_whole(from_open: int | None, to_open: int | None) -> bool:
    """Whether a movement moved an open package whole: it left one and
    arrived in the same one."""
    return from_open is not None and from_open == to_open


def _all_right(
    left_at: int | None,
    arrived_at: int | None,
    whole: bool,
    left_count_id: int | None,
    arrived_count_id: int | None,
    closed: bool,
) -> bool:
    """Whether every end a check has is put right: one it has not is no
    end to put right, nor where an open package moved whole left; an end
    is put right by a count, or an opening's open end by its open package
    closed since (inventory-tng-nyl)."""
    return (left_at is None or whole or left_count_id is not None) and (
        arrived_at is None or arrived_count_id is not None or closed
    )


def _settle(
    session: Session, check_id: int, seen_as: str, admin_id: int, how: Settled
) -> OverRecorded:
    """Settle one waiting, still as seen, holding its row. RuleBroken
    as waiting()."""
    row = waiting(session, check_id, seen_as)
    _settled(row, admin_id, how)
    return row


def _settled(
    row: OverRecorded, admin_id: int, how: Settled, at: datetime | None = None
) -> None:
    """Mark one settled, by an administrator, how, and when: `at`, else
    now."""
    row.settled_at = func.now() if at is None else at
    row.settled_by_admin_id = admin_id
    row.settled_how = how


def waiting(session: Session, check_id: int, seen_as: str) -> OverRecorded:
    """One still waiting as seen, its row held: taken after its places, as
    answer() and a place brought back for it take them. RuleBroken
    (over_recorded_unchanged) if it was settled or said again meanwhile."""
    row = get_locked(session, OverRecorded, check_id)
    if row is None or row.settled_at is not None or seen(row) != seen_as:
        raise RuleBroken("over_recorded_unchanged")
    return row


def ends(session: Session, check_id: int) -> tuple[int | None, int | None]:
    """Where a check's transaction moved stock from, and to."""
    found = session.execute(
        select(LedgerTransaction.from_location_id, LedgerTransaction.to_location_id)
        .join(Movement, Movement.transaction_id == LedgerTransaction.id)
        .join(OverRecorded, OverRecorded.movement_id == Movement.id)
        .where(OverRecorded.id == check_id)
    ).first()
    return (None, None) if found is None else (found[0], found[1])


def places_of(session: Session, check_id: int) -> list[int]:
    """The places a check's transaction moved stock between, to lock first,
    as saves take places (ledger.lock_places)."""
    return [place for place in ends(session, check_id) if place is not None]


def mark_checked(
    session: Session,
    check_id: int,
    seen_as: str,
    admin_id: int,
    reason: str,
    words: str | None = None,
) -> None:
    """Settle one, as checked, saying why it is right as it is (a key of
    CHECKED_REASONS) and, put right another way, how in `words`: nothing
    written to the ledger. RuleBroken as _settle."""
    row = _settle(session, check_id, seen_as, admin_id, "checked")
    row.checked_reason = reason
    row.checked_words = words
    _measure_settled(session, "checked", reason=reason)
    once_committed(
        session,
        log.info,
        "over recorded checked",
        over_recorded_id=check_id,
        admin_id=admin_id,
        reason=reason,
    )


def correction_key(
    transaction_key: uuid.UUID, row: OverRecorded, *, found: bool
) -> uuid.UUID:
    """The key Correct writes an end of a check under: found again where
    it left, or written off where it arrived; the other end only, after a
    count put one right, a key of its own."""
    only = " only" if row.left_count_id or row.arrived_count_id else ""
    return uuid.uuid5(
        transaction_key, f"over recorded {row.id} {'found' if found else 'off'}{only}"
    )


class _Read(NamedTuple):
    """A check's movement and transaction, and, for an opening whose open
    end no count put right, whether its open package was closed since by
    a write-off (`closed`), or made loose (`loose`)."""

    movement: Movement
    transaction: LedgerTransaction
    closed: bool
    loose: bool


def _read(session: Session, row: OverRecorded) -> _Read:
    """A check as _Read says. Closing its open package (used up, Close, a
    count) wrote off what it showed, so put that end right (Ali,
    2026-11-15, inventory-tng-nyl); making it loose did not, so that end
    waits for a count, and Correct writes it off the loose units there
    (Ali, 2026-11-24, inventory-tng-n7dj). Read under the place's lock: a
    close and a loosening take their open package's place first
    (open_packages), and an opening's open package is at its place unless
    moved since, where writing off in it is refused anyway
    (movement_open_package_place). Closed is never undone."""
    movement = session.get_one(Movement, row.movement_id)
    transaction = session.get_one(LedgerTransaction, movement.transaction_id)
    open_id = movement.to_open_package_id
    closed = loose = False
    if (
        transaction.kind == "open"
        and open_id is not None
        and row.arrived_count_id is None
    ):
        closed_on, loosened_in = session.execute(
            select(OpenPackage.closed_at, LOOSENED_IN).where(OpenPackage.id == open_id)
        ).one()
        loose = loosened_in is not None
        closed = closed_on is not None and not loose
    return _Read(movement, transaction, closed, loose)


def _right(row: OverRecorded, read: _Read) -> bool:
    """Whether every end a check has is put right already (_all_right)."""
    movement, transaction = read.movement, read.transaction
    return _all_right(
        transaction.from_location_id,
        transaction.to_location_id,
        moved_whole(movement.from_open_package_id, movement.to_open_package_id),
        row.left_count_id,
        row.arrived_count_id,
        read.closed,
    )


def _closed_right(session: Session, row: OverRecorded, admin_id: int) -> None:
    """Settle one whose every end is put right already as counted, writing
    nothing (Ali, 2026-11-15, inventory-tng-dro)."""
    _settled(row, admin_id, "counted")
    _measure_settled(session, "counted")
    once_committed(
        session,
        log.info,
        "over recorded closed as right",
        over_recorded_id=row.id,
        admin_id=admin_id,
    )


def close(session: Session, check_id: int, seen_as: str, admin_id: int) -> None:
    """Close it: settle one waiting, still as seen, both of whose ends are
    put right already (over_recorded_queries.Shown.both_right), as
    counted, writing nothing (Ali, 2026-11-15, inventory-tng-dro). Call it
    holding its places (places_of), locked first, as Correct. RuleBroken
    as waiting(), or (over_recorded_unchanged) if an end is still to put
    right."""
    row = waiting(session, check_id, seen_as)
    if not _right(row, _read(session, row)):
        raise RuleBroken("over_recorded_unchanged")
    _closed_right(session, row, admin_id)


def correct(session: Session, check_id: int, seen_as: str, admin_id: int) -> Settled:
    """Settle one, as corrected: an adjustment on each side its transaction
    has, but one a count put right (counted), undoes what was recorded more
    than moved, in the forms it moved in, with the reason REASON. Found
    again where it left, written off where it arrived. An opening's open
    package closed since, however, put its end right (_read), so its end is
    skipped: the sealed end alone is written (Ali, 2026-11-15,
    inventory-tng-nyl). One made loose since did not, so its end is written
    off the loose units there (Ali, 2026-11-24, inventory-tng-n7dj). One with every end put right already has nothing
    to write, so is settled as counted, as close() settles it (Ali,
    2026-11-15, inventory-tng-dro). Call it holding its places
    (places_of), locked first. Each under a key of the check's, so it is
    written once; with one end counted, a key of its own. How it was
    settled. RuleBroken as waiting(), or as
    counts.record_adjustment."""
    row = waiting(session, check_id, seen_as)
    read = _read(session, row)
    movement, transaction, closed = read.movement, read.transaction, read.closed
    if _right(row, read):
        _closed_right(session, row, admin_id)
        return "counted"
    _settled(row, admin_id, "corrected")
    reason = adjustment_reasons.seeded(session, REASON)
    if closed:
        once_committed(
            session,
            log.info,
            "over recorded closed end skipped",
            over_recorded_id=check_id,
            open_package_id=movement.to_open_package_id,
        )
    sides = (
        (
            None if row.left_count_id else transaction.from_location_id,
            True,
            ledger.Form(movement.from_package_id, movement.from_open_package_id),
        ),
        (
            None if row.arrived_count_id or closed else transaction.to_location_id,
            False,
            # Made loose, what it showed is loose units there now.
            ledger.LOOSE
            if read.loose
            else ledger.Form(movement.to_package_id, movement.to_open_package_id),
        ),
    )
    for place, found, form in sides:
        if place is None:
            continue
        counts.record_adjustment(
            session,
            correction_key(transaction.idempotency_key, row, found=found),
            admin_id,
            place,
            movement.item_id,
            row.quantity,
            found=found,
            reason_id=reason.id,
            form=form,
        )
    _measure_settled(session, "corrected")
    once_committed(
        session,
        log.info,
        "over recorded corrected",
        over_recorded_id=check_id,
        admin_id=admin_id,
    )
    return "corrected"


def confirm_count(
    session: Session, check_id: int, seen_as: str, admin_id: int, count: Decimal
) -> Settled:
    """Settle one of an open package moved whole
    (over_recorded_queries.Shown.moved_whole), given the count an
    administrator says it held when it moved: as it was, as checked,
    writing nothing; else as corrected, with an adjustment at the
    place it arrived, in it, for the difference, with the reason REASON,
    under a key of the check's. How it was settled.
    Call it holding its places (places_of), locked first. RuleBroken as
    _settle, if it is not such a move (over_recorded_not_whole), or as
    counts.record_adjustment."""
    row = _settle(session, check_id, seen_as, admin_id, "checked")
    movement = session.get_one(Movement, row.movement_id)
    transaction = session.get_one(LedgerTransaction, movement.transaction_id)
    open_id = movement.to_open_package_id
    if (
        open_id is None
        or open_id != movement.from_open_package_id
        or transaction.to_location_id is None
    ):
        raise RuleBroken("over_recorded_not_whole")
    if count == movement.quantity:
        _measure_settled(session, "checked")
        once_committed(
            session,
            log.info,
            "over recorded confirmed",
            over_recorded_id=check_id,
            admin_id=admin_id,
        )
        return "checked"
    row.settled_how = "corrected"
    counts.record_adjustment(
        session,
        uuid.uuid5(transaction.idempotency_key, f"over recorded {row.id} count"),
        admin_id,
        transaction.to_location_id,
        movement.item_id,
        abs(count - movement.quantity),
        found=count > movement.quantity,
        reason_id=adjustment_reasons.seeded(session, REASON).id,
        form=ledger.Form(movement.to_package_id, open_id),
    )
    _measure_settled(session, "corrected")
    once_committed(
        session,
        log.info,
        "over recorded count corrected",
        over_recorded_id=check_id,
        admin_id=admin_id,
    )
    return "corrected"


def openings_waiting_at(
    session: Session, place_ids: Collection[int], as_of: int
) -> dict[int, dict[int, set[int]]]:
    """Of those waiting, the openings at each of the places as of moment
    `as_of`, by place: by item, the packages each opened. Its count asks
    each such item by form, with a line of those packages sealed though the
    records hold none, so the sealed ones really there can be counted and
    each end put right (Ali, 2026-11-15, inventory-tng-he9,
    inventory-tng-dd8). One query."""
    found: dict[int, dict[int, set[int]]] = {}
    for place_id, item_id, package_id in session.execute(
        select(
            LedgerTransaction.from_location_id,
            Movement.item_id,
            Movement.from_package_id,
        )
        .join(OverRecorded, OverRecorded.movement_id == Movement.id)
        .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
        .where(
            WAITING,
            LedgerTransaction.kind == "open",
            LedgerTransaction.from_location_id.in_(list(place_ids)),
            LedgerTransaction.id <= as_of,
        )
    ):
        # An opening leaves sealed, from where it is (ledger._opening_breaks).
        assert package_id is not None
        assert place_id is not None
        found.setdefault(place_id, {}).setdefault(item_id, set()).add(package_id)
    return found


def hold_waiting(
    session: Session, places: Collection[int], as_of: int, item_ids: Collection[int]
) -> None:
    """Hold, by id, every check waiting at any of `places` of these items,
    moved by moment `as_of`: those counted() may settle at each of them, for
    a change counting several places or open packages (a store and its
    areas, length checks answered together) to take before it writes the
    first (stock/ledger.py's lock order, inventory-tng-nfia)."""
    if not item_ids:
        return
    query = _waiting_at(select(OverRecorded.id), places, as_of, item_ids)
    session.execute(locked(query, of=OverRecorded))


def _waiting_at[*Columns](
    query: Select[*Columns],
    places: Collection[int],
    as_of: int,
    item_ids: Collection[int],
) -> Select[*Columns]:
    """`query` of the checks waiting at any of `places` of these items,
    moved by moment `as_of`, by id: what hold_waiting holds and counted()
    reads."""
    return (
        query.join(Movement, Movement.id == OverRecorded.movement_id)
        .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
        .where(
            WAITING,
            Movement.item_id.in_(item_ids),
            LedgerTransaction.id <= as_of,
            moments.at_places(places),
        )
        .order_by(OverRecorded.id)
    )


def counted(
    session: Session,
    admin_id: int,
    place_id: int,
    as_of: int,
    written: Sequence[counts.Written],
    put_right: Collection[tuple[int, ledger.Form | None]],
    emptied: Collection[tuple[int, ledger.Form]] = (),
) -> int:
    """Put right the ends at a place of those waiting that a count there
    has, as of moment `as_of`, after their movement (Ali, 2026-11-15,
    inventory-tng-qs8): `put_right` is what it counted, each item by its
    total (no form) or a form of it (counts.Counted.put_right), and
    `written` its transactions, the first of which each end keeps. An end
    is put right by its item counted by its total, or by the form it is in
    at that end counted (docs/decisions/0020); an open package moved whole
    has one end, where it arrived, which only that open package's own count
    puts right; an opening's ends, both at one place, only a count of each
    one's form, as a total cannot show the sealed and open split (Ali,
    2026-11-15, inventory-tng-r5z). An open package the count closed
    (`emptied`: used up, or not there) puts right an opening's end that
    arrived in it, as a count of it at 0 would, so Correct writes only the
    sealed end (Ali, 2026-11-15, inventory-tng-he9); a move's it does not.
    One closed before, however, put that end right already, though no count
    did (OPENED_INTO); one made loose before did not, and a count of the
    item's total or its loose units puts that end right, made by `as_of`
    (LOOSENED_IN; Ali, 2026-11-24, inventory-tng-n7dj). One with every end it has put right closes as
    counted, by the count's administrator, then. How many closed.

    Call it in the count's transaction, holding the place (ledger.lock_place):
    it holds the rows it reads, by id, as Correct and Mark checked take a
    check after its places, so none waits on another the wrong way round.
    A change counting several places or open packages holds them all
    first (hold_waiting).
    A send that wrote nothing (sent before) puts nothing right."""
    if not written or not (put_right or emptied):
        return 0
    count_id = written[0].id
    totals = {item_id for item_id, form in put_right if form is None}
    forms = {(item_id, form) for item_id, form in put_right if form is not None}
    query = _waiting_at(
        select(
            OverRecorded,
            Movement,
            LedgerTransaction.kind,
            LedgerTransaction.from_location_id,
            LedgerTransaction.to_location_id,
            WRITTEN_OFF_ON,
            LOOSENED_IN,
        ),
        [place_id],
        as_of,
        {item_id for item_id, _ in [*put_right, *emptied]},
    ).outerjoin(OpenPackage, OPENED_INTO)
    found = session.execute(locked(query, of=OverRecorded)).all()
    closed = 0
    for row, movement, kind, left_at, arrived_at, closed_on, loosened_in in found:
        whole = moved_whole(movement.from_open_package_id, movement.to_open_package_id)
        item_id = movement.item_id
        by_total = item_id in totals and not whole and kind != "open"
        # Its open package made loose by the moment counted: what it showed
        # is loose units, which the item's total or its loose units counted
        # put right (Ali, 2026-11-24, inventory-tng-n7dj).
        loose_counted = (
            loosened_in is not None
            and loosened_in <= as_of
            and (item_id in totals or (item_id, ledger.LOOSE) in forms)
        )
        left = ledger.Form(movement.from_package_id, movement.from_open_package_id)
        arrived = ledger.Form(movement.to_package_id, movement.to_open_package_id)
        if (
            left_at == place_id
            and not whole
            and row.left_count_id is None
            and (by_total or (item_id, left) in forms)
        ):
            row.left_count_id = count_id
            _end_counted(session, row.id, "left", count_id, admin_id)
        if (
            arrived_at == place_id
            and row.arrived_count_id is None
            and (
                by_total
                or (item_id, arrived) in forms
                or (kind == "open" and (item_id, arrived) in emptied)
                or loose_counted
            )
        ):
            row.arrived_count_id = count_id
            _end_counted(session, row.id, "arrived", count_id, admin_id)
        if _all_right(
            left_at,
            arrived_at,
            whole,
            row.left_count_id,
            row.arrived_count_id,
            closed_on is not None,
        ):
            _settled(row, admin_id, "counted")
            closed += 1
            once_committed(
                session,
                log.info,
                "over recorded counted",
                over_recorded_id=row.id,
                ledger_transaction_id=count_id,
                admin_id=admin_id,
            )
    _measure_settled(session, "counted", closed)
    return closed


def open_package_closed(
    session: Session, open_id: int, admin_id: int, at: datetime
) -> None:
    """Close as counted those waiting for an opening into an open package
    just closed (open_packages.close, however: Confirm used up, Close, a
    count) whose sealed end a count put right already: the close wrote off
    what the open package showed, so put the other end right (Ali,
    2026-11-15, inventory-tng-die). By the close's administrator, at its
    time; the open end keeps no count (arrived_count_id stays NULL), as a
    close is none.

    Call it in the close's transaction, after its place and open package
    (stock/ledger.py's order), holding the rows it closes by id. One taken by
    another is left out, not waited for: all else that holds one holds its
    opening's place first, which the close holds too unless the open
    package moved whole since, and Correct may then wait on the open
    package this close holds. One so left waits with both ends right, for
    Close it (close(); Ali, 2026-11-15, inventory-tng-dro)."""
    found = session.scalars(
        locked(
            select(OverRecorded)
            .join(Movement, Movement.id == OverRecorded.movement_id)
            .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
            .where(
                WAITING,
                LedgerTransaction.kind == "open",
                Movement.to_open_package_id == open_id,
                OverRecorded.left_count_id.is_not(None),
                OverRecorded.arrived_count_id.is_(None),
            )
            .order_by(OverRecorded.id),
            of=OverRecorded,
            skip_locked=True,
        )
    ).all()
    for row in found:
        _settled(row, admin_id, "counted", at)
        once_committed(
            session,
            log.info,
            "over recorded closed",
            over_recorded_id=row.id,
            open_package_id=open_id,
            admin_id=admin_id,
        )
    _measure_settled(session, "counted", len(found))


def _end_counted(
    session: Session, check_id: int, end: str, count_id: int, admin_id: int
) -> None:
    once_committed(
        session,
        log.info,
        "over recorded end counted",
        over_recorded_id=check_id,
        end=end,
        ledger_transaction_id=count_id,
        admin_id=admin_id,
    )
