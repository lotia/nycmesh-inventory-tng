"""Volunteers (docs/decisions/0004): every write to the volunteer table, and
its rules. A duplicate is merged into the volunteer it duplicates: retired,
pointing at them. One who can hold stock has a custody location
(locations.add_custody), named for them.

A rule of more than one condition is a function of plain values, returning
the name of the rule a change would break, or None."""

from datetime import datetime

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from inventory_tng import locations
from inventory_tng.db import (
    Gone,
    enforce,
    exists_where,
    lock_catalogue,
    locked,
    relock,
    require_current,
)
from inventory_tng.models import Location, Volunteer


def leaving(*, others_merged_in: bool, holds_custody: bool) -> str | None:
    """A volunteer retired, or merged: those merged into them are pointed
    elsewhere first, and their custody location is retired first."""
    if others_merged_in:
        return "volunteer_merge_target"
    if holds_custody:
        return "location_custody_volunteer"
    return None


def _custody(volunteer_id: int) -> Select[Location]:
    return select(Location).where(
        Location.kind == "custody",
        Location.volunteer_id == volunteer_id,
        Location.retired_at.is_(None),
    )


def custody(session: Session, volunteer_id: int) -> Location | None:
    """Their current custody location, if they hold stock."""
    return session.scalar(_custody(volunteer_id))


def _lock(
    session: Session, volunteer: Volunteer, *, current: bool | None = None
) -> Location | None:
    """The catalogue lock, then their custody location, then them, each read
    again as locked (db.locked_row says why in that order): their custody
    location, if they have one. With `current`, as db.relock."""
    lock_catalogue(session)
    held = session.scalar(locked(_custody(volunteer.id)))
    relock(session, volunteer, current=current)
    return held


def _leaving(session: Session, volunteer: Volunteer, held: Location | None) -> None:
    others = exists_where(session, Volunteer.merged_into_id == volunteer.id)
    enforce(leaving(others_merged_in=others, holds_custody=held is not None))


def add(session: Session, name: str, email: str | None) -> Volunteer:
    lock_catalogue(session)
    volunteer = Volunteer(name=name, email=email)
    session.add(volunteer)
    session.flush()
    return volunteer


def save(session: Session, volunteer: Volunteer, name: str, email: str | None) -> None:
    """Renamed, with their custody location."""
    held = _lock(session, volunteer)
    volunteer.name, volunteer.email = name, email
    if held is not None:
        locations.rename_custody(session, held, name)


def hold_stock(session: Session, volunteer: Volunteer) -> Location:
    """Their custody location, made if they have none."""
    held = _lock(session, volunteer)
    return held or locations.add_custody(session, volunteer.id, volunteer.name)


def stop_holding_stock(session: Session, volunteer: Volunteer, at: datetime) -> None:
    """Their custody location retired, once nothing is in it. Gone if it
    was retired meanwhile."""
    held = _lock(session, volunteer)
    if held is None:
        raise Gone
    locations.retire_held(session, held, at)


def retire(session: Session, volunteer: Volunteer, at: datetime) -> None:
    held = _lock(session, volunteer, current=True)
    _leaving(session, volunteer, held)
    volunteer.retired_at = at


def merge(session: Session, volunteer: Volunteer, into_id: int, at: datetime) -> None:
    """A duplicate of `into_id`: retired, pointing at them."""
    held = _lock(session, volunteer, current=True)
    require_current(session, Volunteer, into_id, "volunteer_merge_target")
    _leaving(session, volunteer, held)
    volunteer.merged_into_id, volunteer.retired_at = into_id, at


def restore(session: Session, volunteer: Volunteer) -> None:
    """Back, and a merged duplicate as themselves."""
    relock(session, volunteer, current=False)
    volunteer.retired_at, volunteer.merged_into_id = None, None
