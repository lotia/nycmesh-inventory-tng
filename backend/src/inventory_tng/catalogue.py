"""What the scan page fills a cart from: GET /api/catalogue.

One reply, loaded once per page, holding what is current: retired rows,
merged volunteers and revoked labels are left out. Registered devices only,
since it names volunteers, and those names are never logged.
"""

import uuid
from datetime import datetime
from decimal import Decimal

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import labels
from inventory_tng.db import DbSession
from inventory_tng.devices import DeviceRefusedReply, RegisteredDevice
from inventory_tng.ledger import by_form, takeable
from inventory_tng.models import (
    Category,
    Item,
    ItemPackage,
    Label,
    Location,
    OpenPackage,
    ScanSettings,
    Volunteer,
)
from inventory_tng.open_packages import tracks_opened

log = structlog.get_logger(__name__)

router = APIRouter()

PATH = "/catalogue"


class Entry(BaseModel):
    """Read straight from a row's attributes."""

    model_config = ConfigDict(from_attributes=True)


class VolunteerEntry(Entry):
    id: int
    name: str


class LocationEntry(Entry):
    id: int
    name: str
    kind: str
    parent_id: int | None


class PackageEntry(Entry):
    id: int
    name: str
    # In the item's unit.
    size: Decimal
    # Whether opening one makes an open package, followed until used up, or
    # loose units: its own setting, else its item's (docs/decisions/0020).
    track_opened: bool


class OpenPackageEntry(Entry):
    """A package opened and followed, not reported used up: one a cart can
    take from where it is."""

    id: int
    key: uuid.UUID
    item_id: int
    package_id: int
    location_id: int
    opened_at: datetime
    # What is left on it, in the item's unit, as the records say.
    left: Decimal


class ItemEntry(BaseModel):
    id: int
    name: str
    unit: str
    category: str
    manufacturer: str | None
    part_number: str | None
    old_code: str | None
    packages: list[PackageEntry]
    # Retired, but with stock left: it may be taken, never received.
    retired: bool


class LabelEntry(Entry):
    """A printed code, and the one thing it names."""

    code: str
    item_id: int | None
    package_id: int | None
    location_id: int | None


class Settings(Entry):
    """How the page behaves, as administrators set it (models.ScanSettings)."""

    name_check_minutes: int


class Catalogue(BaseModel):
    settings: Settings
    volunteers: list[VolunteerEntry]
    locations: list[LocationEntry]
    items: list[ItemEntry]
    labels: list[LabelEntry]
    open_packages: list[OpenPackageEntry]


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
    for package, item_tracks in session.execute(
        select(ItemPackage, Item.track_opened)
        .join(Item, Item.id == ItemPackage.item_id)
        .where(ItemPackage.retired_at.is_(None))
        .order_by(ItemPackage.size)
    ):
        packages.setdefault(package.item_id, []).append(
            PackageEntry(
                id=package.id,
                name=package.name,
                size=package.size,
                track_opened=tracks_opened(item_tracks, package.track_opened),
            )
        )
    # A retired item stays while it has stock, so what is left can be taken.
    items = session.execute(
        select(Item, Category.name)
        .join(Category, Category.id == Item.category_id)
        .where(takeable())
        .order_by(Item.name)
    )
    found = session.scalars(select(Label).where(Label.revoked_at.is_(None)))
    places = [LocationEntry.model_validate(x) for x in locations]
    entries = [
        ItemEntry(
            id=item.id,
            name=item.name,
            unit=item.unit,
            category=category,
            manufacturer=item.manufacturer,
            part_number=item.part_number,
            old_code=item.old_code,
            packages=packages.get(item.id, []),
            retired=item.retired_at is not None,
        )
        for item, category in items
    ]
    # A label is sent only if everything it names is (labels.inactive): one
    # on a retired item, package or place would name what the page cannot show.
    sent = labels.Sent(
        {entry.id for entry in entries},
        {pack.id for entry in entries for pack in entry.packages},
        {place.id for place in places},
    )

    return Catalogue(
        settings=Settings.model_validate(session.get_one(ScanSettings, 1)),
        volunteers=[VolunteerEntry.model_validate(v) for v in volunteers],
        locations=places,
        items=entries,
        labels=[
            LabelEntry.model_validate(label)
            for label in found
            if labels.inactive(label, sent) is None
        ],
        open_packages=open_to_take(session, {entry.id for entry in entries}),
    )


def open_to_take(session: Session, item_ids: set[int]) -> list[OpenPackageEntry]:
    """The open packages of these items a cart can take from: not closed,
    not reported used up, with what is left on each where it is, oldest
    first. One whose records say nothing is left is sent too, at that: it
    is where a volunteer says it is used up."""
    rows = session.scalars(
        select(OpenPackage)
        .where(
            OpenPackage.closed_at.is_(None),
            OpenPackage.reported_at.is_(None),
            OpenPackage.item_id.in_(item_ids),
        )
        .order_by(OpenPackage.opened_at, OpenPackage.id)
    ).all()
    # Their balances alone, by id, so only their movements are summed.
    ids = [row.id for row in rows]
    left = {
        (open_id, place): quantity
        for open_id, place, quantity in session.execute(
            select(
                by_form.c.open_package_id, by_form.c.location_id, by_form.c.quantity
            ).where(by_form.c.open_package_id.in_(ids))
        )
    }
    return [
        OpenPackageEntry(
            id=row.id,
            key=row.key,
            item_id=row.item_id,
            package_id=row.package_id,
            location_id=row.location_id,
            opened_at=row.opened_at,
            left=left.get((row.id, row.location_id), Decimal(0)),
        )
        for row in rows
    ]


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
