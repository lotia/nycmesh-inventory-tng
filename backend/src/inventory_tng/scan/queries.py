"""What the scan page's catalogue reads (scan/catalogue_api.py): plain
functions taking a Session, each named for what it returns
(docs/decisions/0023)."""

import uuid
from collections.abc import Collection, Sequence
from decimal import Decimal

from sqlalchemy import Row, select
from sqlalchemy.orm import Session

from inventory_tng.models.catalogue import (
    CURRENT_ALIAS,
    Category,
    Item,
    ItemAlias,
    ItemPackage,
)
from inventory_tng.models.labels import Label
from inventory_tng.models.places import Location
from inventory_tng.models.stock import LedgerTransaction, OpenPackage
from inventory_tng.models.volunteers import Volunteer
from inventory_tng.stock.balance import takeable


def current_volunteers(session: Session) -> Sequence[Volunteer]:
    """Current volunteers, by name."""
    return session.scalars(
        select(Volunteer)
        # A merged volunteer is retired too (ck_volunteer_merged_is_retired).
        .where(Volunteer.retired_at.is_(None))
        .order_by(Volunteer.name)
    ).all()


def current_places(session: Session) -> Sequence[Location]:
    """Current places, by name."""
    return session.scalars(
        select(Location).where(Location.retired_at.is_(None)).order_by(Location.name)
    ).all()


def items_offered(session: Session) -> Sequence[Row[Item, str, bool]]:
    """Every item, by name then id, with its category's name and whether
    volunteers may still pick it (balance.takeable)."""
    return session.execute(
        select(Item, Category.name, takeable())
        .join(Category, Category.id == Item.category_id)
        .order_by(Item.name, Item.id)
    ).all()


def packages_with_tracking(session: Session) -> Sequence[Row[ItemPackage, bool]]:
    """Every package, smallest first, ties by id, with whether its item
    follows packages once opened."""
    return session.execute(
        select(ItemPackage, Item.track_opened)
        .join(Item, Item.id == ItemPackage.item_id)
        .order_by(ItemPackage.size, ItemPackage.id)
    ).all()


def current_aliases(session: Session) -> Sequence[Row[int, str]]:
    """Each current alias's item id and name, in the order they were made."""
    return session.execute(
        select(ItemAlias.item_id, ItemAlias.name)
        .where(CURRENT_ALIAS)
        .order_by(ItemAlias.id)
    ).all()


def labels_in_use(session: Session) -> Sequence[Label]:
    """Every label not revoked."""
    return session.scalars(select(Label).where(Label.revoked_at.is_(None))).all()


def saved_keys(session: Session, keys: Collection[uuid.UUID]) -> set[uuid.UUID]:
    """Those of `keys` that are saved batches' keys."""
    return set(
        session.scalars(
            select(LedgerTransaction.idempotency_key).where(
                LedgerTransaction.idempotency_key.in_(keys)
            )
        )
    )


def open_not_reported(
    session: Session, item_ids: Collection[int]
) -> Sequence[Row[OpenPackage, str, Decimal | None]]:
    """The open packages of `item_ids`' items neither closed nor reported
    used up, oldest first, each with its package's name and short_below."""
    return session.execute(
        select(OpenPackage, ItemPackage.name_shown, ItemPackage.short_below)
        .join(ItemPackage, ItemPackage.id == OpenPackage.package_id)
        .where(
            OpenPackage.closed_at.is_(None),
            OpenPackage.reported_at.is_(None),
            OpenPackage.item_id.in_(item_ids),
        )
        .order_by(OpenPackage.opened_at, OpenPackage.id)
    ).all()
