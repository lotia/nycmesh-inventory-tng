"""What the scan page fills a cart from."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from structlog.testing import capture_logs
from test_devices import new_device
from test_models import add

from inventory_tng import devices
from inventory_tng.models import (
    Category,
    Item,
    ItemPackage,
    Label,
    Location,
    Volunteer,
)

URL = "/api/catalogue"
NOW = datetime.now(UTC)


class Rows:
    """Current rows beside retired, merged and revoked ones."""

    def __init__(self, session: Session) -> None:
        tag = uuid.uuid4().hex[:8]
        category = add(session, Category(name=f"Cables {tag}"))
        self.cable = add(
            session,
            Item(
                name=f"Cable {tag}",
                category_id=category.id,
                unit="m",
                manufacturer="Ubiquiti",
                part_number=f"TC-{tag}",
                old_code=f"OLD-{tag}",
            ),
        )
        self.retired_item = add(
            session,
            Item(name=f"Old cable {tag}", category_id=category.id, retired_at=NOW),
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
        self.store = add(session, Location(name=f"Store {tag}", kind="store"))
        self.shelf = add(
            session, Location(name=f"Shelf {tag}", kind="area", parent_id=self.store.id)
        )
        self.closed = add(
            session, Location(name=f"Closed {tag}", kind="store", retired_at=NOW)
        )
        self.volunteer = add(session, Volunteer(name=f"Volunteer {tag}"))
        self.left = add(session, Volunteer(name=f"Left {tag}", retired_at=NOW))
        self.merged = add(
            session,
            Volunteer(
                name=f"Twin {tag}", merged_into_id=self.volunteer.id, retired_at=NOW
            ),
        )
        self.label = add(
            session, Label(item_id=self.cable.id, package_id=self.spool.id)
        )
        self.shelf_label = add(session, Label(location_id=self.shelf.id))
        self.revoked = add(session, Label(item_id=self.cable.id, revoked_at=NOW))


@pytest.fixture
def rows(session: Session) -> Rows:
    return Rows(session)


@pytest.fixture
def registered(client: TestClient, session: Session) -> TestClient:
    _, token = new_device(session)
    client.cookies.set(devices.COOKIE, token)
    return client


def by_id(entries: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {entry["id"]: entry for entry in entries}


def test_an_unregistered_device_is_refused(client: TestClient) -> None:
    response = client.get(URL)
    assert (response.status_code, response.json()) == (403, {"reason": "unregistered"})


def test_holds_whats_current(registered: TestClient, rows: Rows) -> None:
    reply = registered.get(URL).json()
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


def test_lists_come_in_name_order(registered: TestClient, rows: Rows) -> None:
    reply = registered.get(URL).json()
    for name in ("volunteers", "locations", "items"):
        names = [entry["name"] for entry in reply[name]]
        assert names == sorted(names), name


def test_names_stay_out_of_the_logs(registered: TestClient, rows: Rows) -> None:
    with capture_logs() as logs:
        registered.get(URL)
    assert rows.volunteer.name not in repr(logs)
    (line,) = [line for line in logs if line["event"] == "catalogue sent"]
    assert line["volunteers"] >= 1
