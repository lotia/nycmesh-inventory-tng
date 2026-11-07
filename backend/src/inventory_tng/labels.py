"""Labels (docs/decisions/0015): each item, package of one, or place has one
current code, printed in as many copies as wanted. Revoking a code voids
every copy; making labels for that thing again then makes a new code."""

from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import structlog
from opentelemetry import metrics
from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from inventory_tng.db import get_locked, locked_row
from inventory_tng.models import Item, ItemPackage, Label, Location
from inventory_tng.pages import amount

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

Names = Literal["item", "package", "location"]
MAX_COPIES = 120  # Ten pages of one label.
MAX_LABELS = 600  # Fifty pages, of any labels.

made = meter.create_counter(
    "inventory.labels.made",
    unit="{label}",
    description="Label codes made, by what they name",
)
printed = meter.create_counter(
    "inventory.labels.printed",
    unit="{label}",
    description="Labels laid out on sheets to print, each copy counted",
)
revoked = meter.create_counter(
    "inventory.labels.revoked",
    unit="{label}",
    description="Label codes revoked",
)


@dataclass(frozen=True)
class Named:
    """A label and what it names, in words. `current` if a scan of it is
    read: it is not revoked, and what it names is not retired."""

    label: Label
    words: str
    current: bool


def _package_words(item: Item, package: ItemPackage) -> str:
    unit = "" if item.unit == "each" else f" {item.unit}"
    return f"{item.name}: {package.name} of {amount(package.size)}{unit}"


def _by_id[M: (Item, ItemPackage, Location)](
    session: Session, model: type[M], ids: AbstractSet[int | None]
) -> dict[int, M]:
    """These rows, by id: no query for none."""
    wanted = ids - {None}
    if not wanted:
        return {}
    return {
        row.id: row
        for row in session.scalars(select(model).where(model.id.in_(wanted)))
    }


def named(session: Session, labels: Sequence[Label]) -> list[Named]:
    """Each label with what it names, in the order given."""
    item_ids = {label.item_id for label in labels if label.item_id is not None}
    package_ids = {label.package_id for label in labels if label.package_id}
    place_ids = {label.location_id for label in labels if label.location_id}
    items = _by_id(session, Item, item_ids)
    packages = _by_id(session, ItemPackage, package_ids)
    places = _by_id(session, Location, place_ids)
    # And the place each area is in.
    places |= _by_id(session, Location, {p.parent_id for p in places.values()})
    found = []
    for label in labels:
        if label.location_id is not None:
            place = places[label.location_id]
            parent = places.get(place.parent_id or 0)
            words = place.name + (f", in {parent.name}" if parent else "")
            retired = place.retired_at is not None
        else:
            item = items[label.item_id or 0]
            package = packages.get(label.package_id or 0)
            words = item.name if package is None else _package_words(item, package)
            retired = item.retired_at is not None or (
                package is not None and package.retired_at is not None
            )
        current = label.revoked_at is None and not retired
        found.append(Named(label, words, current))
    return found


def printable(session: Session, label_ids: Sequence[int]) -> list[Named]:
    """The current labels of these ids, in the order given, each once."""
    asked = list(dict.fromkeys(label_ids))
    rows = {
        row.id: row for row in session.scalars(select(Label).where(Label.id.in_(asked)))
    }
    found = named(session, [rows[i] for i in asked if i in rows])
    return [each for each in found if each.current]


def record_printing(picked: Sequence[Named], copies: int) -> int:
    """Counts and logs `copies` of each label laid out to print: how many."""
    total = len(picked) * copies
    printed.add(total)
    log.info(
        "labels laid out to print",
        label_ids=[each.label.id for each in picked],
        copies=copies,
    )
    return total


def lay_out(picked: Sequence[Named], copies: int, per_page: int) -> list[list[Named]]:
    """`copies` of each label, in pages of `per_page`."""
    every = [each for each in picked for _ in range(copies)]
    return [every[start : start + per_page] for start in range(0, len(every), per_page)]


def current_of(session: Session, names: ColumnElement[bool]) -> list[Named]:
    """The current labels `names` picks, such as an item's (itself and its
    packages) or a place's."""
    labels = session.scalars(
        select(Label)
        .where(names, Label.revoked_at.is_(None))
        .order_by(Label.package_id.is_not(None), Label.id)
    ).all()
    return named(session, labels)


def _current_or_new(session: Session, label: Label, names: Names) -> Label:
    """The current label naming what `label` names, or `label`, made."""
    if existing := session.scalar(
        select(Label).where(
            Label.revoked_at.is_(None),
            Label.item_id.is_not_distinct_from(label.item_id),
            Label.package_id.is_not_distinct_from(label.package_id),
            Label.location_id.is_not_distinct_from(label.location_id),
        )
    ):
        return existing
    session.add(label)
    session.flush()
    made.add(1, {"names": names})
    log.info("label made", label_id=label.id)
    return label


# Making one locks what it names first, so two made at once make one code,
# and nothing is retired meanwhile.


def make_for_item(
    session: Session, item_id: int, package_id: int | None = None
) -> Label | None:
    """The current label of an item, or of one of its packages, made if it
    has none: None if that is retired or not there."""
    item = locked_row(session, Item, item_id)
    if item is None or item.retired_at is not None:
        return None
    if package_id is not None:
        package = get_locked(session, ItemPackage, package_id)
        if package is None or package.item_id != item_id or package.retired_at:
            return None
    label = Label(item_id=item_id, package_id=package_id)
    return _current_or_new(session, label, "item" if package_id is None else "package")


def make_for_place(session: Session, location_id: int) -> Label | None:
    """The current label of a place, made if it has none: None if it is
    retired, not there, or a volunteer's custody."""
    place = locked_row(session, Location, location_id)
    if place is None or place.retired_at is not None or place.kind == "custody":
        return None
    return _current_or_new(session, Label(location_id=location_id), "location")


def revoke(session: Session, label_id: int, at: datetime) -> bool:
    """Revokes a current label: False if there is none by that id."""
    label = get_locked(session, Label, label_id)
    if label is None or label.revoked_at is not None:
        return False
    label.revoked_at = at
    session.flush()
    revoked.add(1)
    log.info("label revoked", label_id=label_id)
    return True
