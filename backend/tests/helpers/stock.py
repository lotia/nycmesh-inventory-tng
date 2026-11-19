"""Stock to move: the rows a batch needs, batches written, and the forms
and signs of the pages that put stock right."""

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.models.catalogue import Category, Item, ItemPackage
from inventory_tng.models.places import Location
from inventory_tng.models.stock import (
    AdjustmentReason,
    LedgerTransaction,
    Movement,
    OpenPackage,
    OverRecorded,
)
from inventory_tng.models.volunteers import Volunteer
from inventory_tng.scan.batch import record
from inventory_tng.scan.batch_schemas import Batch
from inventory_tng.stock import open_packages, over_recorded
from inventory_tng.stock.ledger import LOOSE, Entry, Form, insert_transaction
from inventory_tng.stock.paths import COUNTS
from inventory_tng.web.navigation import retired_holding

from helpers.concurrency import Committed
from helpers.markup import Element, hidden_fields, read
from helpers.rows import add, tag
from helpers.sign_in import admin_named

# Where the scan page sends its batches.
URL = "/api/transactions"


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


def raise_check(
    session: Session,
    transaction: LedgerTransaction | None,
    more: Decimal = Decimal(1),
) -> OverRecorded:
    """`more` recorded than moved of the transaction's line, raised
    directly; over_recorded.one_bag_waits goes through the API."""
    assert transaction is not None
    movement_id = session.scalars(
        select(Movement.id).where(Movement.transaction_id == transaction.id)
    ).one()
    over_recorded.raise_checks(session, {movement_id: more})
    return session.scalars(
        select(OverRecorded).where(OverRecorded.movement_id == movement_id)
    ).one()


def ada_id(session: Session) -> int:
    """The id of admin_client's administrator."""
    return admin_named(session, "ada").id


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


def badge(page: str | Element) -> tuple[str, str] | None:
    """The badge beside Counts, if any: the number shown, and the words a
    screen reader reads in its place."""
    for link in read(page).all("a"):
        if link.text.startswith("Counts "):
            for shown in link.all("span", class_="badge"):
                return (
                    shown.one("span", aria_hidden="true").text,
                    shown.one("span", class_="sr-only").text,
                )
    return None


def badge_of(places: int) -> tuple[str, str]:
    """The badge saying how many retired places hold stock."""
    return str(places), f": {retired_holding(places)}"


def at_retired_van(session: Session) -> Place:
    """Five radios in the store, and three of them saved into the van after
    it was retired."""
    place = stocked(session)
    retire(session, place.van)
    write(session, place)
    return place


class Spools:
    """A Place whose cable comes in spools of 305 m, tracked when opened
    (docs/decisions/0020), and two sealed ones received into its store."""

    def __init__(self, session: Session, *, tracked: bool = True) -> None:
        self.session = session
        self.place = Place(session)
        self.cable = self.place.cable
        self.cable.track_opened = tracked
        self.spool = add(
            session, ItemPackage(item_id=self.cable.id, name="Spool", size=305)
        )
        self.store, self.van = self.place.store, self.place.van
        self.receive(uuid.uuid4(), spools=2)

    @property
    def sealed(self) -> Form:
        return Form(package_id=self.spool.id)

    def write(
        self, header: Mapping[str, object], *lines: Entry, key: uuid.UUID | None = None
    ) -> LedgerTransaction | None:
        return insert_transaction(self.session, key or uuid.uuid4(), header, lines)

    def receive(self, key: uuid.UUID, spools: int = 1) -> None:
        """Sealed spools received into the store, under `key`."""
        self.write(
            {"kind": "receipt", "to_location_id": self.store.id},
            Entry(self.cable.id, Decimal(305 * spools), LOOSE, self.sealed),
            key=key,
        )

    def opened(self, at: datetime | None = None) -> OpenPackage:
        """One spool opened in the store: an open package of all 305 m,
        opened `at` if given, else now. The app never changes when one was
        opened, so one opened `at` is made first, and make() finds it."""
        key = uuid.uuid4()
        if at is not None:
            row = OpenPackage(
                key=key,
                opened_at=at,
                item_id=self.cable.id,
                package_id=self.spool.id,
                location_id=self.store.id,
            )
            self.session.add(row)
            self.session.flush()
        made = open_packages.make(self.session, key, self.spool.id, self.store.id)
        self.write(
            {
                "kind": "open",
                "from_location_id": self.store.id,
                "to_location_id": self.store.id,
            },
            Entry(
                self.cable.id, Decimal(305), self.sealed, Form(open_package_id=made.id)
            ),
        )
        return made

    def move(self, *lines: Entry, kind: str = "move") -> LedgerTransaction | None:
        """From the store to the van."""
        header = {
            "kind": kind,
            "from_location_id": self.store.id,
            "to_location_id": self.van.id,
        }
        return self.write(header, *lines)


def length(made: OpenPackage, left: str) -> dict[str, str]:
    """A batch's report of what is really on an open package."""
    return {"open_package_key": str(made.key), "left": left}


def report(spools: Spools, *lengths: dict[str, str]) -> dict[str, object]:
    """A batch of reports alone, of `lengths`, at the spools' store."""
    return spools.place.batch(
        kind="report", to_location_id=None, lines=[], lengths=list(lengths)
    )


def cut(made: OpenPackage, quantity: str, **fields: object) -> dict[str, object]:
    """A batch's line taking `quantity` from an open package, cut loose
    unless `fields` say otherwise."""
    return {
        "item_id": str(made.item_id),
        "quantity": quantity,
        "open_package_key": str(made.key),
    } | fields


def sealed_line(spools: Spools, quantity: str) -> dict[str, object]:
    """A batch's line of `quantity` of the cable in sealed spools."""
    return {
        "item_id": spools.cable.id,
        "quantity": quantity,
        "package_id": spools.spool.id,
        "package_size": "305",
    }


def count_form(page: Response) -> dict[str, str]:
    """A count page's hidden fields, as it was shown: its key, the place's
    latest ledger entry then and how many it saw, and the items it showed."""
    return {
        name: value
        for name, value in hidden_fields(page.text)
        if name in {"key", "as_of", "seen", "items"}
    }


def count_form_at(client: TestClient, place_id: int) -> dict[str, str]:
    """A place's count form's hidden fields, as shown now."""
    return count_form(client.get(f"{COUNTS}/{place_id}"))


def count_at(
    client: TestClient,
    place_id: int,
    lines: dict[str, str],
    shown: dict[str, str] | None = None,
    *,
    status: int = 303,
) -> Response:
    """A count of a place sent with `lines`, from its form as shown now, or
    as `shown`, answered with `status`: saved, unless said otherwise."""
    form = shown if shown is not None else count_form_at(client, place_id)
    response = client.post(f"{COUNTS}/{place_id}", data=form | lines)
    assert response.status_code == status
    return response
