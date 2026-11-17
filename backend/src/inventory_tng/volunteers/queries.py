"""What the volunteers pages read (admin/volunteers.py), and the places
page's names of custody places' volunteers (admin/locations.py): plain
functions taking a Session, each named for what it returns
(docs/decisions/0023)."""

from collections.abc import Collection, Sequence

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from inventory_tng.models import Volunteer


def volunteer_names(
    session: Session, volunteer_ids: Collection[int | None]
) -> dict[int, str]:
    """The names of `volunteer_ids`' volunteers, by id (a place's
    volunteer_id may be None)."""
    return dict(
        session.execute(
            select(Volunteer.id, Volunteer.name).where(Volunteer.id.in_(volunteer_ids))
        ).all()
    )


def ids_where(
    session: Session, volunteer_ids: Collection[int], *where: ColumnElement[bool]
) -> set[int]:
    """Those of `volunteer_ids` whose volunteers `where` picks."""
    return set(
        session.scalars(
            select(Volunteer.id).where(Volunteer.id.in_(volunteer_ids), *where)
        )
    )


def current_others(session: Session, volunteer_id: int) -> Sequence[Volunteer]:
    """The current volunteers but one, by name ignoring case."""
    return session.scalars(
        select(Volunteer)
        .where(Volunteer.retired_at.is_(None), Volunteer.id != volunteer_id)
        .order_by(func.lower(Volunteer.name))
    ).all()
