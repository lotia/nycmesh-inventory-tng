"""What the scan page fills a cart from: GET /api/catalogue.

One reply, loaded once per page, holding what is current: retired rows,
merged volunteers and revoked labels are left out, but for retired items
and packages with stock left, which may be taken. Registered devices only,
since it names volunteers, and those names are never logged.
"""

import math
import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Annotated

import structlog
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import labels, ledger
from inventory_tng.db import SnapshotSession
from inventory_tng.devices import DeviceRefusedReply, RegisteredDevice
from inventory_tng.ledger import takeable, takeable_package
from inventory_tng.models import (
    Category,
    Item,
    ItemPackage,
    Label,
    LedgerTransaction,
    Location,
    OpenPackage,
    ScanSettings,
    Volunteer,
)
from inventory_tng.open_packages import tracks_opened

log = structlog.get_logger(__name__)

router = APIRouter()

PATH = "/catalogue"
# Batch keys a page may ask about at once (Catalogue.saved): its outbox's
# saved batches no catalogue has shown, and those still waiting.
MOST_KEYS = 100


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


class SealedAt(BaseModel):
    location_id: int
    # Whole packages.
    count: int


class PackageEntry(Entry):
    id: int
    name: str
    # In the item's unit.
    size: Decimal
    # Whether opening one makes an open package, followed until used up, or
    # loose units: its own setting, else its item's (docs/decisions/0020).
    track_opened: bool
    # Retired, but with sealed packages left: they may be taken, never
    # received.
    retired: bool
    # Too short to use, in the item's unit, if set: for an open package
    # the page makes before the server has seen it (OpenPackageEntry).
    short_below: Decimal | None
    # The current places with some sealed, and how many: opening its item
    # at one of them opens this package, if it is the item's only one
    # sealed there (the page takes off what its outbox has moved since).
    sealed: list[SealedAt]


class OpenPackageEntry(Entry):
    """A package opened and followed, not reported used up: one a cart can
    take from where it is."""

    id: int
    key: uuid.UUID
    item_id: int
    package_id: int
    # Its package's name, sent here as its package may not be: a retired
    # one with none sealed left is not (takeable_package).
    package_name: str
    location_id: int
    opened_at: datetime
    # What is left on it, in the item's unit, as the records say.
    left: Decimal
    # Its package's too-short-to-use length, in the item's unit, if set,
    # retired package or not: a cut leaving less than it ticks "nothing
    # usable left" (docs/decisions/0020).
    short_below: Decimal | None


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
    # Of the batch keys the page asked about, those saved: what the rest of
    # the reply shows already (with_saved), so the page takes off only what
    # the others moved (inventory-tng-jlc).
    saved: list[uuid.UUID] = []


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
    sealed = ledger.sealed_places(session)
    packages: dict[int, list[PackageEntry]] = {}
    for package, item_tracks in session.execute(
        select(ItemPackage, Item.track_opened)
        .join(Item, Item.id == ItemPackage.item_id)
        .where(takeable_package())
        .order_by(ItemPackage.size)
    ):
        packages.setdefault(package.item_id, []).append(
            PackageEntry(
                id=package.id,
                name=package.name,
                size=package.size,
                track_opened=tracks_opened(item_tracks, package.track_opened),
                retired=package.retired_at is not None,
                short_below=package.short_below,
                sealed=[
                    # A part of one, as a count may leave, is one to open.
                    SealedAt(
                        location_id=place, count=math.ceil(quantity / package.size)
                    )
                    for place, quantity in sealed.get(package.id, {}).items()
                ],
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


def with_saved(session: Session, keys: Sequence[uuid.UUID]) -> Catalogue:
    """The catalogue, and which of `keys` are saved batches, so that a batch
    is in `saved` exactly when the stock shows it. On a session reading one
    snapshot (db.SnapshotSession): a batch committed as it is read is in
    neither (inventory-tng-19c)."""
    reply = catalogue(session)
    reply.saved = sorted(saved_of(session, keys))
    return reply


def saved_of(session: Session, keys: Sequence[uuid.UUID]) -> set[uuid.UUID]:
    if not keys:
        return set()
    return set(
        session.scalars(
            select(LedgerTransaction.idempotency_key).where(
                LedgerTransaction.idempotency_key.in_(list(keys))
            )
        )
    )


def open_to_take(session: Session, item_ids: set[int]) -> list[OpenPackageEntry]:
    """The open packages of these items a cart can take from: not closed,
    not reported used up, with what is left on each where it is, oldest
    first. One whose records say nothing is left is sent too, at that: it
    is where a volunteer says it is used up."""
    found = session.execute(
        select(OpenPackage, ItemPackage.name, ItemPackage.short_below)
        .join(ItemPackage, ItemPackage.id == OpenPackage.package_id)
        .where(
            OpenPackage.closed_at.is_(None),
            OpenPackage.reported_at.is_(None),
            OpenPackage.item_id.in_(item_ids),
        )
        .order_by(OpenPackage.opened_at, OpenPackage.id)
    ).all()
    left = ledger.left_on(session, [row for row, *_ in found])
    return [
        OpenPackageEntry(
            id=row.id,
            key=row.key,
            item_id=row.item_id,
            package_id=row.package_id,
            package_name=name,
            location_id=row.location_id,
            opened_at=row.opened_at,
            left=left[row.id],
            short_below=short_below,
        )
        for row, name, short_below in found
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
def get_catalogue(
    # The device first: one without a cookie is refused with no read.
    device: RegisteredDevice,
    session: SnapshotSession,
    key: Annotated[list[uuid.UUID], Query(max_length=MOST_KEYS)] = [],  # noqa: B006  FastAPI copies it.
) -> Catalogue:
    """Everything current that a cart can hold, and who can fill one; and
    which of the batches `key` names are saved."""
    reply = with_saved(session, key)
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
