"""Labels (docs/decisions/0015): each item, package of one, or place has one
current code, printed in as many copies as wanted. Revoking a code voids
every copy; making labels for that thing again then makes a new code.

Every write to the label table is here. A label is only made or revoked:
the app may not change its code or what it names (migration 0002)."""

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
from inventory_tng.ledger import takeable
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


# Why the scan page doesn't read a label.
Inactive = Literal["revoked", "item retired", "package retired", "place retired"]


@dataclass(frozen=True)
class Sent:
    """The ids the scan page's catalogue holds (catalogue.catalogue)."""

    items: AbstractSet[int]
    packages: AbstractSet[int]
    places: AbstractSet[int]


def inactive(label: Label, sent: Sent) -> Inactive | None:
    """Why the scan page doesn't read this label, or None if it does: it
    reads one only if it isn't revoked and the catalogue holds everything it
    names, as a retired item does while it has stock."""
    if label.revoked_at is not None:
        return "revoked"
    if label.location_id is not None and label.location_id not in sent.places:
        return "place retired"
    if label.item_id is not None and label.item_id not in sent.items:
        return "item retired"
    if label.package_id is not None and label.package_id not in sent.packages:
        return "package retired"
    return None


@dataclass(frozen=True)
class Named:
    """A label, what it names in words, and why it is inactive, if it is."""

    label: Label
    words: str
    why: Inactive | None

    @property
    def current(self) -> bool:
        return self.why is None


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
    sent_items = set(
        session.scalars(select(Item.id).where(Item.id.in_(items), takeable()))
    )
    sent = Sent(
        sent_items,
        {
            i
            for i, package in packages.items()
            if package.retired_at is None and package.item_id in sent_items
        },
        {i for i, place in places.items() if place.retired_at is None},
    )
    found = []
    for label in labels:
        if label.location_id is not None:
            place = places[label.location_id]
            parent = places.get(place.parent_id or 0)
            words = place.name + (f", in {parent.name}" if parent else "")
        else:
            item = items[label.item_id or 0]
            package = packages.get(label.package_id or 0)
            words = item.name if package is None else _package_words(item, package)
        found.append(Named(label, words, inactive(label, sent)))
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
    has none: None if that is retired or not there. A package never moves
    to another item (migration 0002), so the label always names its own."""
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
    label = locked_row(session, Label, label_id)
    if label is None or label.revoked_at is not None:
        return False
    label.revoked_at = at
    session.flush()
    revoked.add(1)
    log.info("label revoked", label_id=label_id)
    return True
