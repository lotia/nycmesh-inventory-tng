"""Stock to move: the rows a batch needs, and batches written."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from inventory_tng.ledger import Batch, record
from inventory_tng.models import (
    Category,
    Item,
    LedgerTransaction,
    Location,
    Volunteer,
)

from helpers.rows import add


class Place:
    """The rows a batch needs, made in one session."""

    def __init__(self, session: Session) -> None:
        # Unique names, so rows committed by one test never clash with
        # another's.
        tag = uuid.uuid4().hex[:8]
        category = add(session, Category(name=f"Radios {tag}"))
        self.item = add(session, Item(name=f"LiteBeam {tag}", category_id=category.id))
        self.cable = add(
            session, Item(name=f"Cable {tag}", category_id=category.id, unit="m")
        )
        self.store = add(session, Location(name=f"Store {tag}", kind="store"))
        self.van = add(session, Location(name=f"Van {tag}", kind="vehicle"))
        self.volunteer = add(session, Volunteer(name=f"Volunteer {tag}"))

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


def stocked(session: Session, quantity: str = "5") -> Place:
    """A Place with `quantity` radios received into its store."""
    place = Place(session)
    received(session, place, [{"item_id": place.item.id, "quantity": quantity}])
    return place


def received(
    session: Session, place: Place, lines: list[dict[str, object]] | None = None
) -> LedgerTransaction:
    """A receipt into the place's store: of `lines`, else three radios."""
    fields: dict[str, object] = {"kind": "receipt", "from_location_id": None}
    fields["to_location_id"] = place.store.id
    if lines is not None:
        fields["lines"] = lines
    return write(session, place, **fields)


def write(session: Session, place: Place, **fields: object) -> LedgerTransaction:
    transaction, _ = record(
        session, Batch.model_validate(place.batch(**fields)), device_id=None
    )
    return transaction
