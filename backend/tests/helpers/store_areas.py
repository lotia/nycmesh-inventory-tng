"""A store with areas to count on one form (stock/counts_routes.py,
inventory-tng-8i4.51.4), and what the tests of it read back."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.models.catalogue import ItemPackage
from inventory_tng.models.places import Location
from inventory_tng.models.stock import LedgerTransaction
from inventory_tng.stock.moments import at_place
from inventory_tng.stock.paths import COUNTS

from helpers.rows import add, tag
from helpers.stock import retire, stocked, write


class Store:
    """A store holding five radios, with Shelf A (three radios) and in it
    Bin 1 (two metres of cable), and Shelf B, empty."""

    def __init__(self, session: Session) -> None:
        self.place = stocked(session)
        store = self.place.store
        self.store = store
        self.shelf = add(session, area("Shelf A", store))
        self.bin = add(session, area("Bin 1", self.shelf))
        self.empty = add(session, area("Shelf B", store))
        self.every_place = (store, self.shelf, self.bin, self.empty)
        self.receive(session, self.shelf, self.place.item.id, "3")
        self.receive(session, self.bin, self.place.cable.id, "2")
        self.path = f"{COUNTS}/{store.id}"

    def receive(self, session: Session, where: Location, item_id: int, n: str) -> None:
        write(
            session,
            self.place,
            kind="receipt",
            from_location_id=None,
            to_location_id=where.id,
            lines=[{"item_id": item_id, "quantity": n}],
        )

    def move(
        self, session: Session, here: Location, there: Location, n: str = "1"
    ) -> LedgerTransaction:
        """`n` radios moved from `here` to `there`."""
        return write(
            session,
            self.place,
            from_location_id=here.id,
            to_location_id=there.id,
            lines=[{"item_id": self.place.item.id, "quantity": n}],
        )

    def emptied_later(self, session: Session) -> Location:
        """Shelf C, retired, holding two radios a save brought after it was
        retired: what a count's moves from an area gone from it start
        from."""
        gone = add(session, area("Shelf C", self.store))
        retire(session, gone)
        self.move(session, self.store, gone, "2")
        return gone


def area(name: str, parent: Location) -> Location:
    return Location(name=f"{name} {tag()}", kind="area", parent_id=parent.id)


def field(place: Location, store: Location, name: str) -> str:
    """The count form's name for a place's field."""
    return name if place.id == store.id else f"at_{place.id}_{name}"


def counts_at(session: Session, place: Location) -> list[LedgerTransaction]:
    return list(
        session.scalars(
            select(LedgerTransaction)
            .where(LedgerTransaction.kind == "count", at_place(place.id))
            .order_by(LedgerTransaction.id)
        )
    )


def found_on(place: Location, package: ItemPackage) -> dict[str, str]:
    """A count form's open package nobody recorded, found at `place`."""
    return {
        "found_location_id": str(place.id),
        "found_package_id": str(package.id),
        "found_left": "40",
    }
