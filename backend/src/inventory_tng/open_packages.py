"""Open packages (docs/decisions/0020): a package opened at a place and
followed until used up, for an item that tracks its opened packages. What
is left on one is its balance in the ledger (ledger.by_form); this module
makes them, and the rows say where each is, who reported it used up, and
which administrator closed it.
"""

import uuid

import structlog
from sqlalchemy import exc, select
from sqlalchemy.orm import Session

from inventory_tng.db import enforce, locked
from inventory_tng.models import Item, ItemPackage, OpenPackage

log = structlog.get_logger(__name__)


def tracks_opened(item: bool, package: bool | None) -> bool:
    """Whether opening a package makes an open package, followed until used
    up (docs/decisions/0020, Amendment): the package's own setting, if it
    has one, else its item's."""
    return item if package is None else package


def make(
    session: Session, key: uuid.UUID, package_id: int, location_id: int
) -> OpenPackage:
    """The open package made under `key` for a package opened at a place,
    or the one made under it already. RuleBroken (open_package_untracked)
    unless the package's item tracks opened ones, and (open_package_key)
    for a key used for another package or place. Call it holding the
    opening's places and items (ledger.hold), as the ledger's lock order
    says; it holds the package."""
    made = session.scalar(select(OpenPackage).where(OpenPackage.key == key))
    if made is None:
        row = session.execute(
            locked(
                select(ItemPackage, Item.track_opened)
                .join(Item, Item.id == ItemPackage.item_id)
                .where(ItemPackage.id == package_id),
                key_share=True,
                shared=True,
                of=ItemPackage,
            )
        ).one_or_none()
        tracked = row is not None and tracks_opened(row[1], row[0].track_opened)
        enforce("open_package_untracked", when=not tracked)
        assert row is not None
        package = row[0]
        made = OpenPackage(
            key=key,
            item_id=package.item_id,
            package_id=package.id,
            location_id=location_id,
        )
        try:
            with session.begin_nested():
                session.add(made)
        except exc.IntegrityError:
            # The same key, made by a request at the same time, now committed;
            # anything else is not this function's to answer.
            found = session.scalar(select(OpenPackage).where(OpenPackage.key == key))
            if found is None:
                raise
            made = found
        else:
            log.info(
                "open package made",
                open_package_id=made.id,
                package_id=package_id,
                location_id=location_id,
            )
            return made
    enforce(
        "open_package_key",
        when=(made.package_id, made.location_id) != (package_id, location_id),
    )
    return made
