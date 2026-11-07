"""What the scan page fills a cart from."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng.models import (
    Category,
    Item,
    ItemPackage,
    Label,
    Location,
    Volunteer,
)

from helpers.rows import add, tag
from helpers.stock import Place, write

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


def test_an_unregistered_device_is_refused(client: TestClient) -> None:
    response = client.get(URL)
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
        {"id": rows.box.id, "name": "Box", "size": 50},
        {"id": rows.spool.id, "name": "Spool", "size": 300},
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
    for item in (place.item, place.cable):
        item.retired_at = datetime.now(UTC)
    session.flush()
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
    place.item.retired_at = datetime.now(UTC)
    session.flush()
    assert place.item.id in by_id(registered_client.get(URL).json()["items"])
