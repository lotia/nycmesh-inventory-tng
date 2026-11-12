"""What the scan page fills a cart from."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng import catalogue
from inventory_tng.ledger import LOOSE, Entry
from inventory_tng.models import (
    Category,
    Item,
    ItemPackage,
    Label,
    Location,
    Volunteer,
)

from helpers.rows import add, tag
from helpers.stock import Place, Spools, retire, write

URL = "/api/catalogue"
NOW = datetime.now(UTC)


class Rows:
    """Current rows beside retired, merged and revoked ones."""

    def __init__(self, session: Session) -> None:
        mark = tag()
        category = add(session, Category(name=f"Cables {mark}"))
        self.cable = add(
            session,
            Item(
                name=f"Cable {mark}",
                category_id=category.id,
                unit="m",
                manufacturer="Ubiquiti",
                part_number=f"TC-{mark}",
                old_code=f"OLD-{mark}",
            ),
        )
        self.retired_item = add(
            session,
            Item(name=f"Old cable {mark}", category_id=category.id, retired_at=NOW),
        )
        self.spool = add(
            session, ItemPackage(item_id=self.cable.id, name="Spool", size=Decimal(300))
        )
        self.box = add(
            session, ItemPackage(item_id=self.cable.id, name="Box", size=Decimal(50))
        )
        add(
            session,
            ItemPackage(item_id=self.cable.id, name="Reel", size=1, retired_at=NOW),
        )
        self.store = add(session, Location(name=f"Store {mark}", kind="store"))
        self.shelf = add(
            session,
            Location(name=f"Shelf {mark}", kind="area", parent_id=self.store.id),
        )
        self.closed = add(
            session, Location(name=f"Closed {mark}", kind="store", retired_at=NOW)
        )
        self.volunteer = add(session, Volunteer(name=f"Volunteer {mark}"))
        self.left = add(session, Volunteer(name=f"Left {mark}", retired_at=NOW))
        self.merged = add(
            session,
            Volunteer(
                name=f"Twin {mark}", merged_into_id=self.volunteer.id, retired_at=NOW
            ),
        )
        self.label = add(
            session, Label(item_id=self.cable.id, package_id=self.spool.id)
        )
        self.shelf_label = add(session, Label(location_id=self.shelf.id))
        self.revoked = add(session, Label(item_id=self.cable.id, revoked_at=NOW))
        reel = session.scalars(
            select(ItemPackage).where(
                ItemPackage.item_id == self.cable.id, ItemPackage.name == "Reel"
            )
        ).one()
        # Labels left on what has since been retired.
        self.on_retired = [
            add(session, Label(item_id=self.cable.id, package_id=reel.id)),
            add(session, Label(item_id=self.retired_item.id)),
            add(session, Label(location_id=self.closed.id)),
        ]


@pytest.fixture
def rows(session: Session) -> Rows:
    return Rows(session)


def by_id(entries: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {entry["id"]: entry for entry in entries}


def test_an_unregistered_device_is_refused(
    make_client_without_database: Callable[..., TestClient],
) -> None:
    response = make_client_without_database().get(URL)
    assert (response.status_code, response.json()) == (403, {"reason": "unregistered"})


def test_holds_whats_current(registered_client: TestClient, rows: Rows) -> None:
    reply = registered_client.get(URL).json()
    volunteers = by_id(reply["volunteers"])
    assert volunteers[rows.volunteer.id] == {
        "id": rows.volunteer.id,
        "name": rows.volunteer.name,
    }
    assert rows.left.id not in volunteers
    assert rows.merged.id not in volunteers

    locations = by_id(reply["locations"])
    assert locations[rows.shelf.id] == {
        "id": rows.shelf.id,
        "name": rows.shelf.name,
        "kind": "area",
        "parent_id": rows.store.id,
    }
    assert rows.store.id in locations
    assert rows.closed.id not in locations

    items = by_id(reply["items"])
    assert rows.retired_item.id not in items
    cable = items[rows.cable.id]
    # Sizes are decimal strings; compared as numbers, however they are spelt.
    packages = [p | {"size": Decimal(p["size"])} for p in cable.pop("packages")]
    # Smallest first; the retired reel is left out.
    assert packages == [
        {
            "id": rows.box.id,
            "name": "Box",
            "size": 50,
            "track_opened": False,
            "retired": False,
            "sealed": [],
            "short_below": None,
        },
        {
            "id": rows.spool.id,
            "name": "Spool",
            "size": 300,
            "track_opened": False,
            "retired": False,
            "sealed": [],
            "short_below": None,
        },
    ]
    assert cable == {
        "id": rows.cable.id,
        "name": rows.cable.name,
        "unit": "m",
        "category": f"Cables {rows.cable.name.removeprefix('Cable ')}",
        "manufacturer": "Ubiquiti",
        "part_number": rows.cable.part_number,
        "old_code": rows.cable.old_code,
        "retired": False,
    }

    labels = {label["code"]: label for label in reply["labels"]}
    assert labels[rows.label.code] == {
        "code": rows.label.code,
        "item_id": rows.cable.id,
        "package_id": rows.spool.id,
        "location_id": None,
    }
    assert labels[rows.shelf_label.code]["location_id"] == rows.shelf.id
    assert rows.revoked.code not in labels
    for label in rows.on_retired:
        assert label.code not in labels


def test_holds_the_scan_settings(registered_client: TestClient) -> None:
    assert registered_client.get(URL).json()["settings"] == {"name_check_minutes": 15}


def test_lists_come_in_name_order(registered_client: TestClient, rows: Rows) -> None:
    reply = registered_client.get(URL).json()
    for name in ("volunteers", "locations", "items"):
        names = [entry["name"] for entry in reply[name]]
        assert names == sorted(names), name


def test_names_stay_out_of_the_logs(registered_client: TestClient, rows: Rows) -> None:
    with capture_logs() as logs:
        registered_client.get(URL)
    assert rows.volunteer.name not in repr(logs)
    (line,) = [line for line in logs if line["event"] == "catalogue sent"]
    assert line["volunteers"] >= 1


def test_a_retired_item_stays_while_it_has_stock(
    registered_client: TestClient, session: Session
) -> None:
    place = Place(session)
    write(
        session,
        place,
        kind="receipt",
        from_location_id=None,
        to_location_id=place.store.id,
    )
    retire(session, place.item, place.cable)
    items = by_id(registered_client.get(URL).json()["items"])
    # The radio has stock left; the cable never had any.
    assert items[place.item.id]["retired"] is True
    assert place.cable.id not in items
    # Once it is all taken, it is gone from the page.
    write(
        session,
        place,
        kind="install",
        from_location_id=place.store.id,
        to_location_id=None,
    )
    items = by_id(registered_client.get(URL).json()["items"])
    assert place.item.id not in items


def test_a_retired_item_counts_stock_where_there_is_some(
    registered_client: TestClient, session: Session
) -> None:
    """Four at the store and four short at the van make nothing in all, but
    four are on a shelf to take."""
    place = Place(session)
    write(
        session,
        place,
        kind="receipt",
        from_location_id=None,
        to_location_id=place.store.id,
        lines=[{"item_id": place.item.id, "quantity": "4"}],
    )
    write(
        session,
        place,
        kind="install",
        from_location_id=place.van.id,
        to_location_id=None,
        lines=[{"item_id": place.item.id, "quantity": "4"}],
    )
    retire(session, place.item)
    assert place.item.id in by_id(registered_client.get(URL).json()["items"])


def test_a_retired_package_stays_while_some_are_sealed(
    registered_client: TestClient, session: Session
) -> None:
    """Taking only, as a retired item (inventory-tng-96a): two sealed
    spools in the store, retired."""
    spools = Spools(session)
    retire(session, spools.spool)

    def sent() -> dict[int, dict[str, Any]]:
        items = by_id(registered_client.get(URL).json()["items"])
        return by_id(items[spools.cable.id]["packages"])

    assert sent()[spools.spool.id]["retired"] is True
    # One opened: the other is still sealed, and its open one is sent too.
    opened = spools.opened()
    assert spools.spool.id in sent()
    # The last one installed: none sealed is left, so it is gone.
    spools.write(
        {"kind": "install", "from_location_id": spools.store.id},
        Entry(spools.cable.id, Decimal(305), spools.sealed, LOOSE),
    )
    assert spools.spool.id not in sent()
    # Its open one is still sent, with its package's name, which the page
    # no longer has (inventory-tng-4w4).
    reply = registered_client.get(URL).json()
    [entry] = [each for each in reply["open_packages"] if each["id"] == opened.id]
    assert entry["package_name"] == spools.spool.name


def test_a_package_says_where_some_are_sealed(
    registered_client: TestClient, session: Session
) -> None:
    """Where opening its item opens it, and how many (inventory-tng-f9h,
    jlc): current places with some sealed, not those whose sealed ones
    were all opened."""
    spools = Spools(session)
    store, van = spools.store.id, spools.van.id

    def sealed() -> dict[int, int]:
        items = by_id(registered_client.get(URL).json()["items"])
        found = by_id(items[spools.cable.id]["packages"])[spools.spool.id]["sealed"]
        return {each["location_id"]: each["count"] for each in found}

    assert sealed() == {store: 2}
    spools.move(Entry(spools.cable.id, Decimal(305), spools.sealed, spools.sealed))
    assert sealed() == {store: 1, van: 1}
    spools.opened()
    assert sealed() == {van: 1}
    retire(session, spools.van)
    assert sealed() == {}


def test_says_which_batches_asked_about_are_saved(
    registered_client: TestClient, session: Session
) -> None:
    """So the page takes off only what the others moved (inventory-tng-jlc)."""
    spools = Spools(session)
    saved, waiting = uuid.uuid4(), uuid.uuid4()
    spools.receive(saved)
    reply = registered_client.get(URL, params={"key": [str(saved), str(waiting)]})
    assert reply.json()["saved"] == [str(saved)]
    assert registered_client.get(URL).json()["saved"] == []
    too_many = [str(uuid.uuid4()) for _ in range(catalogue.MOST_KEYS + 1)]
    assert registered_client.get(URL, params={"key": too_many}).status_code == 422


def test_a_batch_saved_as_the_catalogue_is_read_reads_it_again(
    registered_client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saved between the reads of its key, its stock may be in some reads
    and not others: the catalogue is read again, so `saved` and the stock
    agree."""
    spools = Spools(session)
    key = uuid.uuid4()
    read = catalogue.catalogue
    reads: list[int] = []

    def saving_meanwhile(session: Session) -> catalogue.Catalogue:
        reads.append(1)
        if len(reads) == 1:
            spools.receive(key)
        return read(session)

    monkeypatch.setattr(catalogue, "catalogue", saving_meanwhile)
    reply = registered_client.get(URL, params={"key": [str(key)]}).json()
    assert len(reads) == 2
    assert reply["saved"] == [str(key)]
    [cable] = [each for each in reply["items"] if each["id"] == spools.cable.id]
    assert cable["packages"][0]["sealed"] == [
        {"location_id": spools.store.id, "count": 3}
    ]


def test_batches_saved_on_every_read_answer_those_saved_first(
    registered_client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    spools = Spools(session)
    read = catalogue.catalogue
    keys = [uuid.uuid4() for _ in range(catalogue.TRIES)]

    def saving_each_time(session: Session) -> catalogue.Catalogue:
        spools.receive(keys.pop())
        return read(session)

    asked = [str(each) for each in keys]
    monkeypatch.setattr(catalogue, "catalogue", saving_each_time)
    reply = registered_client.get(URL, params={"key": asked}).json()
    assert len(reply["saved"]) == catalogue.TRIES - 1
