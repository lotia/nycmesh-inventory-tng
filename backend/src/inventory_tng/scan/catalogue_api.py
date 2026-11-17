"""What the scan page fills a cart from: GET /api/catalogue.

One reply, loaded once per page, holding what is current: retired rows,
merged volunteers and revoked labels are left out, but for retired items
and packages with stock left, which may be taken. The other retired items
are named apart (retired_items), with their packages, as are the other
retired packages of an item sent (ItemEntry.retired_packages), only so
the page can say what a save or cart an older build left held. Registered
devices only, since it names volunteers, and those names are never logged.
"""

import math
import uuid
from collections.abc import Sequence
from typing import Annotated

import structlog
from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import labels
from inventory_tng.core.db import SnapshotSession
from inventory_tng.devices.guards import RegisteredDevice
from inventory_tng.devices.schemas import DeviceRefusedReply
from inventory_tng.models import (
    CURRENT_ALIAS,
    Category,
    Item,
    ItemAlias,
    ItemPackage,
    Label,
    LedgerTransaction,
    Location,
    OpenPackage,
    ScanSettings,
    Volunteer,
    held,
    reading_rules,
)
from inventory_tng.open_packages import tracks_opened
from inventory_tng.scan.catalogue_schemas import (
    Catalogue,
    ItemEntry,
    LabelEntry,
    LocationEntry,
    OpenPackageEntry,
    PackageEntry,
    ReadingRuleEntry,
    RetiredItem,
    RetiredPackage,
    SealedAt,
    Settings,
    VolunteerEntry,
)
from inventory_tng.stock import balance
from inventory_tng.stock.balance import takeable

log = structlog.get_logger(__name__)

router = APIRouter()

PATH = "/catalogue"
# Batch keys a page may ask about at once (Catalogue.saved): its outbox's
# saved batches no catalogue has shown, and those still waiting.
MOST_KEYS = 100


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
    sealed = balance.sealed_places(session)
    # A retired item stays while it has stock, so what is left can be taken;
    # the rest are gone, named only.
    items: list[tuple[Item, str]] = []
    gone: list[Item] = []
    for item, category, offered in session.execute(
        select(Item, Category.name, takeable())
        .join(Category, Category.id == Item.category_id)
        .order_by(Item.name, Item.id)
    ):
        if offered:
            items.append((item, category))
        else:
            gone.append(item)
    gone.sort(key=lambda item: item.id)
    gone_ids = {item.id for item in gone}
    packages: dict[int, list[PackageEntry]] = {}
    # Packages by their item, to name: an item no longer offered has them
    # all named; one offered, those it no longer offers.
    named: dict[int, list[RetiredPackage]] = {}
    for package, item_tracks in session.execute(
        select(ItemPackage, Item.track_opened)
        .join(Item, Item.id == ItemPackage.item_id)
        .order_by(ItemPackage.size)
    ):
        # Retired, one with some sealed left may still be taken
        # (takeable_package).
        offered = package.retired_at is None or package.id in sealed
        if package.item_id in gone_ids or not offered:
            named.setdefault(package.item_id, []).append(
                RetiredPackage(
                    id=package.id, name=package.name_shown, size=package.size
                )
            )
            continue
        packages.setdefault(package.item_id, []).append(
            PackageEntry(
                id=package.id,
                name=package.name_shown,
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
    aliases: dict[int, list[str]] = {}
    for item_id, alias in session.execute(
        select(ItemAlias.item_id, ItemAlias.name)
        .where(CURRENT_ALIAS)
        .order_by(ItemAlias.id)
    ):
        aliases.setdefault(item_id, []).append(alias)
    found = session.scalars(select(Label).where(Label.revoked_at.is_(None)))
    places = [LocationEntry.model_validate(x) for x in locations]
    # Each category folded once, not once an item in it.
    categories = {category: held(category) for _, category in items}
    entries = [
        ItemEntry(
            id=item.id,
            name=item.name_shown,
            unit=item.unit,
            category=category,
            manufacturer=item.manufacturer,
            part_number=item.part_number,
            old_code=item.old_code,
            search_text=f"{item.search_text} {categories[category]}",
            aliases=aliases.get(item.id, []),
            packages=packages.get(item.id, []),
            retired=item.retired_at is not None,
            retired_packages=named.get(item.id, []),
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
        retired_items=[
            RetiredItem(
                id=item.id,
                name=item.name_shown,
                unit=item.unit,
                packages=named.get(item.id, []),
            )
            for item in gone
        ],
        reading_rules=[
            ReadingRuleEntry(token=token, reading=rule.reading)
            for token, rule in sorted(reading_rules(session).items())
        ],
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
        select(OpenPackage, ItemPackage.name_shown, ItemPackage.short_below)
        .join(ItemPackage, ItemPackage.id == OpenPackage.package_id)
        .where(
            OpenPackage.closed_at.is_(None),
            OpenPackage.reported_at.is_(None),
            OpenPackage.item_id.in_(item_ids),
        )
        .order_by(OpenPackage.opened_at, OpenPackage.id)
    ).all()
    left = balance.left_on(session, [row for row, *_ in found])
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
