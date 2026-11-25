"""The units and package sizes a save's amounts were shown in, compared
with the catalogue's as it is saved. Each line of a scan page's batch, and
each count or adjustment form, says the unit of each item it shows and
the size of each package; one changed since is refused, and the page asks
again with the new ones, never converting from one to the other (Ali,
2026-11-23, inventory-tng-57u8). Compared under the save's own locks on
its items and packages (stock/ledger.py), which a change to either waits
for, so none slips between the check and the write. Each refusal is
logged and measured here, whichever save it was; an item page's package
forms, compared by catalogue/unit_changes.py, are measured here too.
"""

from collections.abc import Collection, Mapping
from decimal import Decimal
from typing import Literal, NamedTuple

import structlog
from opentelemetry import metrics
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.core.db_errors import RuleBroken
from inventory_tng.models.catalogue import Item, ItemPackage
from inventory_tng.stock.queries import units_of

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

# What changed since a line was shown, and the save it was in: the
# counter's attributes, from these lists only.
Changed = Literal["unit", "package_size"]
# An item page's package forms count here too (catalogue/unit_changes.py).
Save = Literal[
    "batch", "count", "adjustment", "confirm_count", "add_package", "package_settings"
]

refused = meter.create_counter(
    "inventory.saves.shown_otherwise",
    unit="{change}",
    description="Item and package units and package sizes a save showed "
    "that changed since, one for each item or package, so the save was "
    "refused and asked again, by what changed and the save",
)

SHOWN_OTHERWISE = "shown_otherwise"


class Shown(NamedTuple):
    """What a form showed: each item's unit, by item id, and each package's
    size in it, by package id."""

    units: Mapping[int, str]
    sizes: Mapping[int, Decimal]


class Now(NamedTuple):
    """What changed since it was shown, as it is now: each item's unit, by
    item id; each package's size in its item's unit, and that unit, by
    package id (ItemPackage.size_in_items_unit)."""

    units: dict[int, str]
    sizes: dict[int, tuple[Decimal, str]]

    def __bool__(self) -> bool:
        return bool(self.units or self.sizes)


class ShownOtherwise(RuleBroken):
    """shown_otherwise, with what changed, as it is now, so a page asks
    again in the units and sizes that will be saved."""

    def __init__(self, now: Now) -> None:
        super().__init__(SHOWN_OTHERWISE)
        self.now = now


def changed_since(
    session: Session,
    shown: Shown,
    item_ids: Collection[int],
    package_ids: Collection[int] = (),
) -> Now:
    """The items of `item_ids` and packages of `package_ids` that `shown`
    shows otherwise than they are, or does not show at all, as they are
    now. Those that do not exist are left out, for the save to refuse as
    it does."""
    units = units_of(session, set(item_ids))
    sizes: dict[int, tuple[Decimal, str]] = {}
    if package_ids:
        rows = session.execute(
            select(ItemPackage.id, ItemPackage.size_in_items_unit, Item.unit)
            .join(Item, Item.id == ItemPackage.item_id)
            .where(ItemPackage.id.in_(set(package_ids)))
        )
        sizes = {package_id: (size, unit) for package_id, size, unit in rows}
    return Now(
        {
            item_id: unit
            for item_id, unit in units.items()
            if shown.units.get(item_id) != unit
        },
        {
            package_id: now
            for package_id, now in sizes.items()
            if shown.sizes.get(package_id) != now[0]
        },
    )


def refusing(save: Save, now: Now) -> None:
    """Log and measure a save refused as shown otherwise (`now`)."""
    changes: tuple[tuple[Changed, Mapping[int, object]], ...] = (
        ("unit", now.units),
        ("package_size", now.sizes),
    )
    for what, changed in changes:
        if changed:
            refused.add(len(changed), {"changed": what, "save": save})
    log.info(
        "save refused: shown otherwise",
        save=save,
        item_ids=sorted(now.units),
        item_package_ids=sorted(now.sizes),
    )


def require_as_shown(
    session: Session,
    save: Save,
    shown: Shown,
    item_ids: Collection[int],
    package_ids: Collection[int] = (),
) -> None:
    """ShownOtherwise unless `shown` shows the items and packages as they
    are (changed_since). Call it holding them, as the save does."""
    now = changed_since(session, shown, item_ids, package_ids)
    if now:
        refusing(save, now)
        raise ShownOtherwise(now)
