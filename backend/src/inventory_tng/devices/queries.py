"""What the devices pages read (devices/routes.py): plain functions taking
a Session, each named for what it returns (docs/decisions/0023)."""

from collections.abc import Collection, Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng.devices.devices import DeviceFilter, find_devices
from inventory_tng.models.devices import Device, RegistrationCode


def codes_newest_first(session: Session) -> Sequence[RegistrationCode]:
    """Every registration code, the newest first."""
    return session.scalars(
        select(RegistrationCode).order_by(RegistrationCode.created_at.desc())
    ).all()


def devices_registered_by(
    session: Session, code_ids: Collection[int]
) -> dict[int, int]:
    """How many devices each of `code_ids` registered, by code id: none
    left out."""
    return dict(
        session.execute(
            select(Device.registration_code_id, func.count())
            .where(Device.registration_code_id.in_(code_ids))
            .group_by(Device.registration_code_id)
        ).all()
    )


def devices_found(session: Session, chosen: DeviceFilter) -> int:
    """How many devices the filter matches."""
    return (
        session.scalar(
            select(func.count()).select_from(find_devices(chosen).subquery())
        )
        or 0
    )


def devices_chosen(
    session: Session,
    chosen: DeviceFilter,
    *,
    order: Sequence[Any],
    limit: int | None,
    offset: int,
) -> list[Device]:
    """The devices the filter matches, in `order`, `limit` of them from
    `offset`."""
    return list(
        session.scalars(
            find_devices(chosen).order_by(*order).limit(limit).offset(offset)
        )
    )
