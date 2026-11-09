"""Locations (docs/decisions/0003): every write to the location table, and
the tree's rules. Stores and vehicles stand alone; areas nest under a store
or another area; a volunteer holding stock has a custody location.

A rule of more than one condition is a function of plain values,
returning the name of the rule a change would break, or None. The app may
not change a location's kind or volunteer (migration 0002), so no rule need
keep a place with areas one that can have them."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.db import (
    enforce,
    exists_where,
    lock_catalogue,
    relock,
    require_current,
)
from inventory_tng.ledger import holds_stock
from inventory_tng.models import STORE_KINDS, Location, Volunteer


@dataclass(frozen=True)
class Parent:
    """What the rules need of a location's parent."""

    kind: str
    current: bool


def placing(parent: Parent | None, *, current: bool) -> str | None:
    """A location put under `parent`, as it is added, moved or brought
    back: an area's parent is a store or another area, and a current
    area's parent is current."""
    if parent is None:
        return None
    if parent.kind not in STORE_KINDS:
        return "location_area_parent"
    if current and not parent.current:
        return "location_parent_current"
    return None


def no_cycle(
    parents: Mapping[int, int | None], location_id: int, parent_id: int
) -> str | None:
    """Moving `location_id` under `parent_id`, given each location's parent:
    walking up from the new parent never reaches the location itself."""
    seen: set[int] = set()
    at: int | None = parent_id
    while at is not None and at not in seen:
        if at == location_id:
            return "location_no_cycle"
        seen.add(at)
        at = parents.get(at)
    return None


def _parent(session: Session, parent_id: int | None) -> Parent | None:
    """Read afresh, as plain values: an object the session holds may be from
    before the catalogue lock."""
    if parent_id is None:
        return None
    found = session.execute(
        select(Location.kind, Location.retired_at).where(Location.id == parent_id)
    ).one_or_none()
    # None if there is no such row, for its foreign key to refuse.
    return None if found is None else Parent(found.kind, found.retired_at is None)


def _ancestors(session: Session, location_id: int) -> dict[int, int | None]:
    """Each place from `location_id` up to the top of its tree, and its
    parent: all no_cycle walks, rather than the whole table."""
    up = (
        select(Location.id, Location.parent_id)
        .where(Location.id == location_id)
        .cte("up", recursive=True)
    )
    # UNION, not UNION ALL: a cycle already there ends the walk.
    up = up.union(
        select(Location.id, Location.parent_id).join(up, Location.id == up.c.parent_id)
    )
    return {
        row.id: row.parent_id
        for row in session.execute(select(up.c.id, up.c.parent_id))
    }


def add(
    session: Session, name: str, kind: str, parent_id: int | None = None
) -> Location:
    """A store, vehicle or area. A custody location comes with its
    volunteer (add_custody)."""
    lock_catalogue(session)
    enforce(placing(_parent(session, parent_id), current=True))
    location = Location(name=name, kind=kind, parent_id=parent_id)
    session.add(location)
    session.flush()
    return location


def add_custody(session: Session, volunteer_id: int, name: str) -> Location:
    """Where a volunteer's stock is, named for them. The caller holds the
    catalogue lock and the volunteer's row (volunteers.hold_stock)."""
    require_current(session, Volunteer, volunteer_id, "location_custody_volunteer")
    location = Location(name=name, kind="custody", volunteer_id=volunteer_id)
    session.add(location)
    session.flush()
    return location


def save(
    session: Session, location: Location, name: str, parent_id: int | None = None
) -> None:
    """Renamed, and moved under `parent_id` if one is given."""
    relock(session, location)
    if parent_id is not None and parent_id != location.parent_id:
        current = location.retired_at is None
        enforce(placing(_parent(session, parent_id), current=current))
        enforce(no_cycle(_ancestors(session, parent_id), location.id, parent_id))
        location.parent_id = parent_id
    location.name = name


def rename_custody(session: Session, custody: Location, name: str) -> None:
    """A custody location renamed with its volunteer. The caller holds the
    catalogue lock and the row (volunteers.save)."""
    custody.name = name


def retire(session: Session, location: Location, at: datetime) -> None:
    relock(session, location, current=True)
    retire_held(session, location, at)


def retire_held(session: Session, location: Location, at: datetime) -> None:
    """retire(), for a caller that holds the catalogue lock and the row
    (volunteers.stop_holding_stock). A place is retired only once it holds
    nothing: a retired place is offered for a count or an adjustment only
    while a late save leaves stock there."""
    enforce("location_holds_stock", when=holds_stock(session, location.id))
    if location.kind in STORE_KINDS:  # Only these have areas.
        has_areas = exists_where(
            session, Location.parent_id == location.id, Location.retired_at.is_(None)
        )
        enforce("location_parent_current", when=has_areas)
    location.retired_at = at


def restore(session: Session, location: Location) -> None:
    relock(session, location, current=False)
    enforce(placing(_parent(session, location.parent_id), current=True))
    if location.volunteer_id is not None:
        require_current(
            session, Volunteer, location.volunteer_id, "location_custody_volunteer"
        )
    location.retired_at = None
