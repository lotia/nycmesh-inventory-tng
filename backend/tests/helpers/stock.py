"""Stock to move: the rows a batch needs, batches written, and the forms
and signs of the pages that put stock right."""

import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.ledger import Batch, record
from inventory_tng.models import (
    AdjustmentReason,
    Category,
    Item,
    LedgerTransaction,
    Location,
    Volunteer,
)
from inventory_tng.pages import retired_holding

from helpers.concurrency import Committed
from helpers.rows import add, tag


class Place:
    """The rows a batch needs, made in one session."""

    def __init__(self, session: Session) -> None:
        mark = tag()
        category = add(session, Category(name=f"Radios {mark}"))
        self.item = add(session, Item(name=f"LiteBeam {mark}", category_id=category.id))
        self.cable = add(
            session, Item(name=f"Cable {mark}", category_id=category.id, unit="m")
        )
        self.store = add(session, Location(name=f"Store {mark}", kind="store"))
        self.van = add(session, Location(name=f"Van {mark}", kind="vehicle"))
        self.volunteer = add(session, Volunteer(name=f"Volunteer {mark}"))

    def batch(self, **fields: object) -> dict[str, Any]:
        """A move from the store to the van, as the scan page sends it."""
        return {
            "key": str(uuid.uuid4()),
            "kind": "move",
            "volunteer_id": self.volunteer.id,
            "from_location_id": self.store.id,
            "to_location_id": self.van.id,
            "lines": [{"item_id": self.item.id, "quantity": "3"}],
        } | fields


def committed_place(committed: Committed) -> Place:
    """A Place, committed, for tests on two connections."""
    setup = committed.session()
    place = Place(setup)
    setup.commit()
    return place


def stocked(session: Session, quantity: str = "5") -> Place:
    """A Place with `quantity` radios received into its store."""
    place = Place(session)
    received(session, place, [{"item_id": place.item.id, "quantity": quantity}])
    return place


def received(
    session: Session, place: Place, lines: list[dict[str, object]] | None = None
) -> LedgerTransaction:
    """A receipt into the place's store: of `lines`, else three radios."""
    fields: dict[str, object] = {
        "kind": "receipt",
        "from_location_id": None,
        "to_location_id": place.store.id,
    }
    if lines is not None:
        fields["lines"] = lines
    return write(session, place, **fields)


def write(session: Session, place: Place, **fields: object) -> LedgerTransaction:
    return record(
        session, Batch.model_validate(place.batch(**fields)), device_id=None
    ).transaction


class Retirable(Protocol):
    retired_at: Any


def retire(session: Session, *rows: Retirable) -> None:
    """Rows retired by hand, in a test's own session, whatever their rules."""
    for row in rows:
        row.retired_at = datetime.now(UTC)
    session.flush()


def adjustment_form(
    session: Session,
    location: Location,
    item: Item,
    *,
    reason: str = "Damaged",
    **fields: str,
) -> dict[str, str]:
    """An adjustment as the Counts page sends it: one of the item written
    off at the place, for `reason`, with a key of its own; `fields` change
    any of that."""
    reason_id = session.scalars(
        select(AdjustmentReason.id).where(AdjustmentReason.name == reason)
    ).one()
    return {
        "key": str(uuid.uuid4()),
        "location_id": str(location.id),
        "item_id": str(item.id),
        "amount": "1",
        "direction": "off",
        "reason_id": str(reason_id),
    } | fields


def badge_html(places: int) -> str:
    """The badge beside Counts: how many retired places hold stock, its
    words read in place of the number."""
    return (
        f'Counts <span class="badge"><span aria-hidden="true">{places}</span>'
        f'<span class="sr-only">: {retired_holding(places)}</span></span>'
    )


def at_retired_van(session: Session) -> Place:
    """Five radios in the store, and three of them saved into the van after
    it was retired."""
    place = stocked(session)
    retire(session, place.van)
    write(session, place)
    return place
