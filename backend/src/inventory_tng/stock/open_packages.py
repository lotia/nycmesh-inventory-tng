"""Open packages (docs/decisions/0020): a package opened at a place and
followed until used up, for an item that tracks its opened packages. What
is left on one is its balance in the ledger (balance.by_form); this module
makes them, and the rows say where each is, who reported it used up, and
which administrator closed it.
"""

import uuid
from collections.abc import Callable, Collection, Iterable, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Literal, NamedTuple

import structlog
from opentelemetry import metrics
from sqlalchemy import ColumnElement, exc, or_, select
from sqlalchemy.orm import Session

from inventory_tng.core.db_errors import enforce
from inventory_tng.core.db_locking import get_locked, lock_catalogue, locked
from inventory_tng.core.db_once_committed import once_committed
from inventory_tng.models.catalogue import Item, ItemPackage
from inventory_tng.models.places import Location
from inventory_tng.models.stock import LedgerTransaction, OpenPackage
from inventory_tng.models.volunteers import Volunteer
from inventory_tng.stock import (
    adjustment_reasons,
    balance,
    counts,
    ledger,
    over_recorded,
)
from inventory_tng.web.forms import CHANGED

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

closed = meter.create_counter(
    "inventory.open_packages.closed",
    unit="{package}",
    description="Open packages an administrator closed, by how: confirming a "
    "volunteer's report, directly, counting, no longer following its "
    "package's opened ones, or a guided recount of its item's unit",
)
cleared = meter.create_counter(
    "inventory.open_packages.cleared",
    unit="{package}",
    description="Volunteers' reports of open packages used up that an "
    "administrator cleared, as not used up",
)


# How a check of what an open package has was raised: a volunteer's
# report, or a save that took it below zero (docs/decisions/0020,
# 2026-11-12); and how an administrator settled it.
CheckRaised = Literal["volunteer", "below_zero"]
CheckSettled = Literal["set", "left", "closed"]

checks_raised = meter.create_counter(
    "inventory.open_packages.length_checks.raised",
    unit="{check}",
    description="Checks of what an open package really has, by how raised: a "
    "volunteer's report, or a save taking it below zero",
)
checks_settled = meter.create_counter(
    "inventory.open_packages.length_checks.settled",
    unit="{check}",
    description="Checks of what an open package really has that an "
    "administrator settled, by how: set to a length, left as it is, or the "
    "package closed",
)


def _check_raised(session: Session, how: CheckRaised) -> None:
    once_committed(session, checks_raised.add, 1, {"how": how})


def _check_settled(session: Session, how: CheckSettled) -> None:
    once_committed(session, checks_settled.add, 1, {"how": how})


def make(
    session: Session, key: uuid.UUID, package_id: int, location_id: int
) -> OpenPackage:
    """The open package made under `key` for a package opened at a place,
    or the one made under it already. RuleBroken (open_package_untracked)
    unless the package's item tracks opened ones, and (open_package_key)
    for a key used for another package or place. Call it holding the
    opening's places and items (ledger.hold), as the ledger's lock order
    says; it holds the package."""
    made = session.scalar(select(OpenPackage).where(OpenPackage.key == key))
    if made is None:
        row = session.execute(
            locked(
                select(ItemPackage, Item.track_opened)
                .join(Item, Item.id == ItemPackage.item_id)
                .where(ItemPackage.id == package_id),
                key_share=True,
                shared=True,
                of=ItemPackage,
            )
        ).one_or_none()
        tracked = row is not None and ledger.tracks_opened(row[1], row[0].track_opened)
        enforce("open_package_untracked", when=not tracked)
        assert row is not None
        package = row[0]
        made = OpenPackage(
            key=key,
            item_id=package.item_id,
            package_id=package.id,
            location_id=location_id,
        )
        try:
            with session.begin_nested():
                session.add(made)
        except exc.IntegrityError:
            # The same key, made by a request at the same time, now committed;
            # anything else is not this function's to answer.
            found = session.scalar(select(OpenPackage).where(OpenPackage.key == key))
            if found is None:
                raise
            made = found
        else:
            once_committed(
                session,
                log.info,
                "open package made",
                open_package_id=made.id,
                package_id=package_id,
                location_id=location_id,
            )
            return made
    enforce(
        "open_package_key",
        when=(made.package_id, made.location_id) != (package_id, location_id),
    )
    return made


def report(
    session: Session, open_ids: Collection[int], volunteer_id: int, at: datetime
) -> list[int]:
    """Open packages a volunteer says are used up: shown as reported, no
    longer offered to take from, until an administrator confirms or clears
    it (docs/decisions/0020). One reported already, or closed, is left as
    it is. The ids of those it reported. Locked as a save's open packages
    are (stock/ledger.py's lock order), after its places and items."""
    rows = session.scalars(
        locked(
            select(OpenPackage)
            .where(OpenPackage.id.in_(list(open_ids)))
            .order_by(OpenPackage.id),
            key_share=True,
        )
    ).all()
    reported = []
    for row in rows:
        if row.reported_at is None and row.closed_at is None:
            row.reported_at, row.reported_by_volunteer_id = at, volunteer_id
            reported.append(row.id)
            once_committed(
                session,
                log.info,
                "open package reported used up",
                open_package_id=row.id,
                volunteer_id=volunteer_id,
            )
    return reported


def report_length(
    session: Session,
    row: OpenPackage,
    left: Decimal,
    unit: str,
    volunteer_id: int,
    transaction: LedgerTransaction,
) -> None:
    """What a volunteer says is really on an open package, `left` in
    `unit`, for an administrator to check: a report, never an adjustment
    (0003). A later
    report replaces one not yet checked; one of a package closed is left,
    as nobody would check it. Call it holding the row, as a save holds its
    open packages (stock/ledger.py's lock order)."""
    if row.closed_at is not None:
        return
    row.length_reported_at = transaction.created_at
    row.length_reported_by_volunteer_id = volunteer_id
    row.length_reported = left
    row.length_reported_unit = unit
    row.length_reported_in_id = transaction.id
    _check_raised(session, "volunteer")
    once_committed(
        session,
        log.info,
        "open package length reported",
        open_package_id=row.id,
        volunteer_id=volunteer_id,
    )


def went_below_zero(
    session: Session, row: OpenPackage, before: Decimal, cut: Decimal, at: datetime
) -> None:
    """Ask an administrator to check an open package a save's `cut` took
    from nothing or more (`before`) to below zero: its records are wrong,
    whether or not anyone reported it. Only on going below, so one left
    below zero, checked and left, is not asked about at every cut. Call it
    holding the row, as report_length."""
    if row.below_zero_at is not None or not before - cut < 0 <= before:
        return
    row.below_zero_at = at
    _check_raised(session, "below_zero")
    once_committed(
        session, log.info, "open package went below zero", open_package_id=row.id
    )


def left_below_zero(
    session: Session, row: OpenPackage, left: Decimal, at: datetime
) -> None:
    """Ask again about an open package an administrator's Set left below
    zero, as cuts since took more than it had: its records are wrong still.
    Call it holding the row, its check just settled, as went_below_zero."""
    if left >= 0:
        return
    row.below_zero_at = at
    _check_raised(session, "below_zero")
    once_committed(
        session,
        log.info,
        "open package left below zero by a set",
        open_package_id=row.id,
    )


def _clear_check(row: OpenPackage) -> bool:
    """Whether there was a check to clear."""
    checked = row.length_reported_at is not None or row.below_zero_at is not None
    row.length_reported_at = None
    row.length_reported_by_volunteer_id = None
    row.length_reported = None
    row.length_reported_unit = None
    row.length_reported_in_id = None
    row.below_zero_at = None
    return checked


def check_seen(row: OpenPackage, unit: str) -> str:
    """What an administrator saw of a check: the report and the fall below
    zero it was raised by, and `unit`, its item's, which the page said its
    amounts in. A page sends it back, so a check raised again since, as by
    a newer report, is not settled unseen, nor one whose item changed unit
    since, converting what it showed (inventory-tng-y50b): what the page
    typed is in the old."""
    below = row.below_zero_at.isoformat() if row.below_zero_at else ""
    return f"{row.length_reported_in_id or ''}/{below}/{unit}"


def settle_check(
    session: Session, open_id: int, seen: str, *, how: Literal["set", "left"]
) -> bool:
    """A check of what an open package has, settled `how`: cleared, whoever
    raised it, if it is still as `seen` (check_seen). False if there is no
    such package, nothing to settle, or it changed since it was seen. Call
    it holding its item, as a change of its unit waits for (stock/ledger.py
    hold_across)."""
    row = get_locked(session, OpenPackage, open_id, key_share=True)
    if row is None or (row.length_reported_at is None and row.below_zero_at is None):
        return False
    # Held already (hold_across), so no query.
    item = session.get(Item, row.item_id)
    if item is None or check_seen(row, item.unit) != seen:
        return False
    _clear_check(row)
    _check_settled(session, how)
    once_committed(
        session, log.info, "open package length check settled", open_package_id=row.id
    )
    return True


Closing = Literal["confirmed", "direct", "count", "unfollowed", "unit_recount"]


def close(
    session: Session,
    open_id: int,
    admin_id: int,
    at: datetime,
    *,
    how: Closing,
    reason: str = adjustment_reasons.USED_UP,
) -> bool:
    """Close an open package as used up (docs/decisions/0020): an
    adjustment, under the administrator's name, for whatever it still
    showed, with the reason Used up, so it shows nothing; then closed.
    False if it did not close it: if there is no such package, it is closed
    already, it moved since it was read, or, confirming a report, it is no
    longer reported (a report cleared meanwhile). A count closes one not
    there with the reason Lost or stolen (`reason`). An opening into it
    waiting as recorded more than moved, its sealed end counted, closes as
    counted (over_recorded.open_package_closed). Its place is locked first,
    then it, then those, as a save's are (stock/ledger.py). RuleBroken as
    counts.record_adjustment refuses. Measured `how` it was closed: by
    confirming a volunteer's report, directly, by a count, or by a guided
    recount of its item's unit (catalogue/unit_recounts.py)."""
    found = session.get(OpenPackage, open_id)
    if found is None:
        return False
    place = found.location_id
    # In the ledger's order: its place, its item, then it.
    ledger.lock_place(session, place)
    ledger.hold(session, {"from_location_id": place}, [found.item_id])
    row = get_locked(session, OpenPackage, open_id, key_share=True)
    assert row is not None  # Read just now; open packages are never deleted.
    if row.closed_at is not None or row.location_id != place:
        return False
    if how == "confirmed" and row.reported_at is None:
        return False
    left = balance.left_on(session, [row])[row.id]
    if left != 0:
        counts.record_adjustment(
            session,
            uuid.uuid5(row.key, "closed"),
            admin_id,
            row.location_id,
            row.item_id,
            abs(left),
            found=left < 0,
            reason_id=adjustment_reasons.seeded(session, reason).id,
            form=ledger.Form(open_package_id=row.id),
        )
    _closed(session, row, admin_id, at, how)
    over_recorded.open_package_closed(session, row.id, admin_id, at)
    return True


def _closed(
    session: Session, row: OpenPackage, admin_id: int, at: datetime, how: Closing
) -> None:
    """The row marked closed, never deleted, measured and logged `how`."""
    row.closed_at, row.closed_by_admin_id = at, admin_id
    # Nobody checks what a closed package has: closing settles its check.
    settled = _clear_check(row)
    once_committed(session, closed.add, 1, {"how": how})
    if settled:
        _check_settled(session, "closed")
    once_committed(
        session,
        log.info,
        "open package closed",
        open_package_id=row.id,
        item_id=row.item_id,
        admin_id=admin_id,
        how=how,
    )


def unfollowing[T](
    session: Session,
    admin_id: int,
    at: datetime,
    change: Callable[[], T],
    *,
    item_ids: Collection[int] = (),
    package_ids: Collection[int] = (),
) -> tuple[T, int]:
    """Make `change`, which may stop following the opened packages of
    these items or packages (catalogue/items.py), then close each open
    package of them no longer followed, in the same transaction
    (close_unfollowed): what it did, and how many it closed.

    `change` locks the items or packages FOR UPDATE, which a save at
    their open packages' places holds after those places (stock/ledger.py's
    order). So their places are locked first, after the catalogue lock
    that every catalogue write takes first (db_locking.locked_row): a cut
    under way there is counted before the close, and one sent after it
    waits, then is refused, as its open package is closed."""
    lock_catalogue(session)
    if not item_ids and not package_ids:
        return change(), 0
    query = select(OpenPackage.location_id).where(*_open_of(item_ids, package_ids))
    places = set(session.scalars(query.distinct()))
    ledger.lock_places(session, places)
    done = change()
    return done, close_unfollowed(
        session, places, admin_id, at, item_ids=item_ids, package_ids=package_ids
    )


def close_unfollowed(
    session: Session,
    places: Collection[int],
    admin_id: int,
    at: datetime,
    *,
    item_ids: Collection[int] = (),
    package_ids: Collection[int] = (),
) -> int:
    """Close each open package of these items or packages whose package's
    opened ones are no longer followed (ledger.tracks_opened), as Ali
    decided on 2026-11-23 (inventory-tng-j89e): what is left on it becomes
    loose units at its place, by a loosening under the administrator's
    name, so the place holds what it held; the row is marked closed, never
    deleted, in history. How many it closed. Nothing is written off, so,
    unlike close, it puts no end of an opening recorded more than moved
    right (over_recorded.open_package_closed): that end, or the one end of
    an open package recorded as moved whole, waits for a count of the item
    there (over_recorded.MADE_LOOSE_IN; Ali, 2026-11-24,
    inventory-tng-n7dj, inventory-tng-04z7).

    Call it holding `places` (ledger.lock_places) from before the setting
    was changed, as unfollowing does. One at a place not held, as moved
    whole there or opened there meanwhile, is refused as changed
    (web.forms.CHANGED): its place could only be locked now out of the
    ledger's order."""
    query = (
        select(OpenPackage.id)
        .join(ItemPackage, ItemPackage.id == OpenPackage.package_id)
        .join(Item, Item.id == OpenPackage.item_id)
        .where(*_open_of(item_ids, package_ids), ledger.TRACKS_OPENED.is_(False))
        .order_by(OpenPackage.id)
    )
    ids = list(session.scalars(query))
    if not ids:
        return 0
    # In the ledger's order, after the places: the items, then the open
    # packages, each by id; then read again as locked.
    ledger.hold_across(session, open_ids=ids)
    rows = session.scalars(
        locked(
            select(OpenPackage)
            .where(
                OpenPackage.id.in_(ids),
                OpenPackage.closed_at.is_(None),
            )
            .order_by(OpenPackage.id),
            key_share=True,
        )
    ).all()
    enforce(CHANGED, when=any(r.location_id not in places for r in rows))
    left = balance.left_on(session, rows)
    for row in rows:
        _loosen(session, row, left[row.id], admin_id)
        _closed(session, row, admin_id, at, "unfollowed")
    return len(rows)


def _open_of(
    item_ids: Collection[int], package_ids: Collection[int]
) -> tuple[ColumnElement[bool], ...]:
    """Filters for the open packages of these items or packages."""
    return (
        OpenPackage.closed_at.is_(None),
        or_(
            OpenPackage.item_id.in_(list(item_ids)),
            OpenPackage.package_id.in_(list(package_ids)),
        ),
    )


def _loosen(session: Session, row: OpenPackage, left: Decimal, admin_id: int) -> None:
    """What is left on an open package made loose units at its place, by
    a loosening (ledger._loosening_breaks); left below zero, as much taken
    from them onto it. Nothing for one that shows nothing."""
    if left == 0:
        return
    on_it, loose = ledger.Form(open_package_id=row.id), ledger.LOOSE
    leaves, arrives = (on_it, loose) if left > 0 else (loose, on_it)
    header = {
        "kind": "loosen",
        "from_location_id": row.location_id,
        "to_location_id": row.location_id,
        "admin_id": admin_id,
    }
    line = ledger.Entry(row.item_id, abs(left), leaves, arrives)
    written = ledger.insert_transaction(
        session, uuid.uuid5(row.key, "loosened"), header, [line]
    )
    once_committed(
        session,
        log.info,
        "open package loosened",
        open_package_id=row.id,
        item_id=row.item_id,
        location_id=row.location_id,
        transaction_id=written and written.id,
    )


def clear(session: Session, open_id: int) -> bool:
    """A report taken back, as wrong: the package is offered again. False
    if there is no such package, or nothing to clear."""
    row = get_locked(session, OpenPackage, open_id, key_share=True)
    if row is None or row.reported_at is None or row.closed_at is not None:
        return False
    row.reported_at, row.reported_by_volunteer_id = None, None
    once_committed(session, cleared.add, 1)
    once_committed(
        session, log.info, "open package report cleared", open_package_id=row.id
    )
    return True


class Shown(NamedTuple):
    """An open package as the pages show it."""

    row: OpenPackage
    item: str
    package: str
    unit: str
    place: str
    left: Decimal
    reported_by: str | None


def shown(session: Session, *where: ColumnElement[bool]) -> list[Shown]:
    """Open packages not closed, `where` says which, oldest first."""
    return [each for each, _ in _read(session, where, ())]


def marked(
    session: Session, marks: Sequence[ColumnElement[bool]]
) -> list[tuple[Shown, tuple[bool | None, ...]]]:
    """Open packages not closed that meet any of `marks`, oldest first,
    each beside whether it meets each, in their order, read in the same
    statement: None where SQL says NULL, which a where would not have met
    either."""
    return _read(session, (or_(*marks),), marks)


def _read(
    session: Session,
    where: Iterable[ColumnElement[bool]],
    marks: Sequence[ColumnElement[bool]],
) -> list[tuple[Shown, tuple[bool | None, ...]]]:
    rows = session.execute(
        select(
            OpenPackage,
            Item.name_shown,
            ItemPackage.name_shown,
            Item.unit,
            Location.name,
            Volunteer.name,
            *marks,
        )
        .join(Item, Item.id == OpenPackage.item_id)
        .join(ItemPackage, ItemPackage.id == OpenPackage.package_id)
        .join(Location, Location.id == OpenPackage.location_id)
        .outerjoin(Volunteer, Volunteer.id == OpenPackage.reported_by_volunteer_id)
        .where(OpenPackage.closed_at.is_(None), *where)
        .order_by(OpenPackage.reported_at, OpenPackage.opened_at, OpenPackage.id)
    ).all()
    left = balance.left_on(session, [row for row, *_ in rows])
    return [
        (Shown(row, item, package, unit, place, left[row.id], by), tuple(met))
        for row, item, package, unit, place, by, *met in rows
    ]
