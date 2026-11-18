"""What the places pages read (places/routes.py): plain functions taking
a Session, each named for what it returns (docs/decisions/0023)."""

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng.models.places import Location


def retired_places(session: Session) -> Sequence[Location]:
    """Every retired place, by name ignoring case."""
    return session.scalars(
        select(Location)
        .where(Location.retired_at.is_not(None))
        .order_by(func.lower(Location.name))
    ).all()


def current_areas_of(session: Session, location_id: int) -> Sequence[Location]:
    """The current places within a place, by name ignoring case."""
    return session.scalars(
        select(Location)
        .where(Location.parent_id == location_id, Location.retired_at.is_(None))
        .order_by(func.lower(Location.name))
    ).all()


def changeable(session: Session, location_id: int) -> Location | None:
    """A location the places pages change, or Recorded more than moved
    brings back: not a volunteer's custody, whose kind never changes."""
    found = session.get(Location, location_id)
    return None if found is None or found.kind == "custody" else found
