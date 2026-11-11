"""Open packages (docs/decisions/0020): a package opened at a place and
followed until used up, for an item that tracks its opened packages. What
is left on one is its balance in the ledger (ledger.by_form); this module
makes them, and the rows say where each is, who reported it used up, and
which administrator closed it.
"""

import uuid
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import NamedTuple

import structlog
from sqlalchemy import ColumnElement, exc, select
from sqlalchemy.orm import Session

from inventory_tng import adjustment_reasons, ledger
from inventory_tng.db import enforce, get_locked, locked
from inventory_tng.models import Item, ItemPackage, Location, OpenPackage, Volunteer

log = structlog.get_logger(__name__)


def tracks_opened(item: bool, package: bool | None) -> bool:
    """Whether opening a package makes an open package, followed until used
    up (docs/decisions/0020, Amendment): the package's own setting, if it
    has one, else its item's."""
    return item if package is None else package


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
        tracked = row is not None and tracks_opened(row[1], row[0].track_opened)
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
            log.info(
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
    are (ledger.py's lock order), after its places and items."""
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
            log.info(
                "open package reported used up",
                open_package_id=row.id,
                volunteer_id=volunteer_id,
            )
    return reported


def close(
    session: Session,
    open_id: int,
    admin_id: int,
    at: datetime,
    *,
    reported: bool = False,
) -> bool:
    """Close an open package as used up (docs/decisions/0020): an
    adjustment, under the administrator's name, for whatever it still
    showed, with the reason Used up, so it shows nothing; then closed.
    Whether it closed it: not if there is no such package, it is closed
    already, it moved since it was read, or, with `reported`, it is no
    longer reported (a report cleared meanwhile). Its place is locked
    first, then it, as a save's are (ledger.py). RuleBroken as
    ledger.record_adjustment refuses."""
    found = session.get(OpenPackage, open_id)
    if found is None:
        return False
    place = found.location_id
    ledger.lock_place(session, place)
    row = get_locked(session, OpenPackage, open_id, key_share=True)
    assert row is not None  # Read just now; open packages are never deleted.
    if row.closed_at is not None or row.location_id != place:
        return False
    if reported and row.reported_at is None:
        return False
    left = ledger.left_on(session, [row])[row.id]
    if left != 0:
        ledger.record_adjustment(
            session,
            uuid.uuid5(row.key, "closed"),
            admin_id,
            row.location_id,
            row.item_id,
            abs(left),
            found=left < 0,
            reason_id=adjustment_reasons.used_up(session).id,
            form=ledger.Form(open_package_id=row.id),
        )
    row.closed_at, row.closed_by_admin_id = at, admin_id
    log.info("open package closed", open_package_id=row.id, admin_id=admin_id)
    return True


def clear(session: Session, open_id: int) -> bool:
    """A report taken back, as wrong: the package is offered again. False
    if there is no such package, or nothing to clear."""
    row = get_locked(session, OpenPackage, open_id, key_share=True)
    if row is None or row.reported_at is None or row.closed_at is not None:
        return False
    row.reported_at, row.reported_by_volunteer_id = None, None
    log.info("open package report cleared", open_package_id=row.id)
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
    rows = session.execute(
        select(
            OpenPackage,
            Item.name,
            ItemPackage.name,
            Item.unit,
            Location.name,
            Volunteer.name,
        )
        .join(Item, Item.id == OpenPackage.item_id)
        .join(ItemPackage, ItemPackage.id == OpenPackage.package_id)
        .join(Location, Location.id == OpenPackage.location_id)
        .outerjoin(Volunteer, Volunteer.id == OpenPackage.reported_by_volunteer_id)
        .where(OpenPackage.closed_at.is_(None), *where)
        .order_by(OpenPackage.reported_at, OpenPackage.opened_at, OpenPackage.id)
    ).all()
    left = ledger.left_on(session, [row for row, *_ in rows])
    return [
        Shown(row, item, package, unit, place, left[row.id], by)
        for row, item, package, unit, place, by in rows
    ]


@dataclass
class Forms:
    """An item's stock at a place, form by form (docs/decisions/0020):
    sealed packages of each, by name, how many and how much; each open
    package's left; loose units; short ends, open packages left below
    their package's too-short-to-use length; and what is usable, all but
    short ends."""

    sealed: list[tuple[str, Decimal, Decimal]] = field(default_factory=list)
    # How many sealed of each package, by id.
    sealed_by_package: dict[int, Decimal] = field(default_factory=dict)
    open: list[Decimal] = field(default_factory=list)
    loose: Decimal = Decimal(0)
    short_ends: list[Decimal] = field(default_factory=list)

    @property
    def total(self) -> Decimal:
        return (
            sum((amount for _, _, amount in self.sealed), Decimal(0))
            + sum(self.open, Decimal(0))
            + self.loose
            + sum(self.short_ends, Decimal(0))
        )

    @property
    def usable(self) -> Decimal:
        return self.total - sum(self.short_ends, Decimal(0))


class PackageSize(NamedTuple):
    name: str
    size: Decimal
    short_below: Decimal | None


def split(
    rows: Iterable[tuple[int | None, int | None, Decimal]],
    packages: Mapping[int, PackageSize],
    opened_from: Mapping[int, int],
) -> Forms:
    """An item's balances at a place, each of a package (sealed), an open
    package, or neither (loose), as Forms: `packages` by id, and each open
    package's package by its id, to tell a short end."""
    forms = Forms()
    for package_id, open_id, amount in rows:
        if package_id is not None:
            package = packages[package_id]
            count = amount / package.size
            forms.sealed.append((package.name, count, amount))
            forms.sealed_by_package[package_id] = count
        elif open_id is not None:
            short = packages[opened_from[open_id]].short_below
            # Less than nothing left is the records', not a short end.
            short_end = short is not None and 0 < amount < short
            (forms.short_ends if short_end else forms.open).append(amount)
        else:
            forms.loose += amount
    return forms


def forms_of(
    session: Session, where: ColumnElement[bool]
) -> dict[tuple[int, int], Forms]:
    """Stock by item and place, form by form, of the balances `where`
    picks (on ledger.by_form): those of nothing left are left out."""
    rows = session.execute(
        select(
            ledger.by_form.c.item_id,
            ledger.by_form.c.location_id,
            ledger.by_form.c.package_id,
            ledger.by_form.c.open_package_id,
            ledger.by_form.c.quantity,
        ).where(where, ledger.by_form.c.quantity != 0)
    ).all()
    item_ids = {row[0] for row in rows}
    packages = {
        package.id: PackageSize(package.name, package.size, package.short_below)
        for package in session.scalars(
            select(ItemPackage).where(ItemPackage.item_id.in_(item_ids))
        )
    }
    open_ids = {row[3] for row in rows if row[3] is not None}
    opened_from = dict(
        session.execute(
            select(OpenPackage.id, OpenPackage.package_id).where(
                OpenPackage.id.in_(open_ids)
            )
        ).all()
    )
    grouped: dict[tuple[int, int], list[tuple[int | None, int | None, Decimal]]] = {}
    for item_id, place, package_id, open_id, amount in rows:
        grouped.setdefault((item_id, place), []).append((package_id, open_id, amount))
    return {key: split(found, packages, opened_from) for key, found in grouped.items()}
