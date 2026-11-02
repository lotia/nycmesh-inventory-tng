"""What the scan page fills a cart from: GET /api/catalogue.

One reply, loaded once per page, holding what is current: retired rows,
merged volunteers and revoked labels are left out. Registered devices only,
since it names volunteers, and those names are never logged.
"""

from decimal import Decimal

import structlog
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.db import DbSession
from inventory_tng.devices import DeviceRefusedReply, RegisteredDevice
from inventory_tng.models import (
    Category,
    Item,
    ItemPackage,
    Label,
    Location,
    Volunteer,
)

log = structlog.get_logger(__name__)

router = APIRouter()

PATH = "/catalogue"


class VolunteerEntry(BaseModel):
    id: int
    name: str


class LocationEntry(BaseModel):
    id: int
    name: str
    kind: str
    parent_id: int | None


class PackageEntry(BaseModel):
    id: int
    name: str
    # In the item's unit.
    size: Decimal


class ItemEntry(BaseModel):
    id: int
    name: str
    unit: str
    category: str
    manufacturer: str | None
    part_number: str | None
    old_code: str | None
    packages: list[PackageEntry]


class LabelEntry(BaseModel):
    """A printed code, and the one thing it names."""

    code: str
    item_id: int | None
    package_id: int | None
    location_id: int | None


class Catalogue(BaseModel):
    volunteers: list[VolunteerEntry]
    locations: list[LocationEntry]
    items: list[ItemEntry]
    labels: list[LabelEntry]


def catalogue(session: Session) -> Catalogue:
    volunteers = session.scalars(
        select(Volunteer)
        # A merged volunteer is retired too (ck_volunteer_merged_is_retired).
        .where(Volunteer.retired_at.is_(None))
        .order_by(Volunteer.name)
    )
    locations = session.scalars(
        select(Location).where(Location.retired_at.is_(None)).order_by(Location.name)
    )
    packages: dict[int, list[PackageEntry]] = {}
    for package in session.scalars(
        select(ItemPackage)
        .where(ItemPackage.retired_at.is_(None))
        .order_by(ItemPackage.size)
    ):
        packages.setdefault(package.item_id, []).append(
            PackageEntry(id=package.id, name=package.name, size=package.size)
        )
    items = session.execute(
        select(Item, Category.name)
        .join(Category, Category.id == Item.category_id)
        .where(Item.retired_at.is_(None))
        .order_by(Item.name)
    )
    labels = session.scalars(select(Label).where(Label.revoked_at.is_(None)))
    return Catalogue(
        volunteers=[VolunteerEntry(id=v.id, name=v.name) for v in volunteers],
        locations=[
            LocationEntry(id=x.id, name=x.name, kind=x.kind, parent_id=x.parent_id)
            for x in locations
        ],
        items=[
            ItemEntry(
                id=item.id,
                name=item.name,
                unit=item.unit,
                category=category,
                manufacturer=item.manufacturer,
                part_number=item.part_number,
                old_code=item.old_code,
                packages=packages.get(item.id, []),
            )
            for item, category in items
        ],
        labels=[
            LabelEntry(
                code=label.code,
                item_id=label.item_id,
                package_id=label.package_id,
                location_id=label.location_id,
            )
            for label in labels
        ],
    )


@router.get(
    PATH,
    responses={
        403: {
            "model": DeviceRefusedReply,
            "description": "This device is not registered",
        }
    },
)
def get_catalogue(device: RegisteredDevice, session: DbSession) -> Catalogue:
    """Everything current that a cart can hold, and who can fill one."""
    reply = catalogue(session)
    # The request line already says who asked; counts help trace a short
    # list. Never names.
    log.debug(
        "catalogue sent",
        device_id=device.id,
        volunteers=len(reply.volunteers),
        locations=len(reply.locations),
        items=len(reply.items),
    )
    return reply
