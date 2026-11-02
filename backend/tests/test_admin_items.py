"""Items and their packages (admin/items.py)."""

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_models import add, history, named
from test_sign_in import text

from inventory_tng.admin.common import quantity
from inventory_tng.admin.items import ITEMS
from inventory_tng.models import (
    Category,
    Item,
    ItemPackage,
    LedgerTransaction,
    Location,
    Movement,
)


@pytest.fixture
def radios(session: Session) -> Category:
    return add(session, Category(name="Radios"))


def new_item(session: Session, category: Category, name: str = "LiteBeam") -> Item:
    return add(session, Item(name=name, category_id=category.id))


def test_adding_an_item(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    done = admin_client.post(
        ITEMS,
        data={
            "name": "LiteBeam 5AC",
            "category_id": str(radios.id),
            "unit": "each",
            "manufacturer": "Ubiquiti",
            "part_number": "LBE-5AC",
        },
    )
    item = named(session, Item, "LiteBeam 5AC")
    assert done.headers["location"] == f"{ITEMS}/{item.id}?notice=added"
    assert (item.manufacturer, item.part_number) == ("Ubiquiti", "LBE-5AC")
    assert (history(session, "item", item.id)[0].actor or "").startswith("admin:")
    assert "LiteBeam 5AC" in text(admin_client.get(ITEMS))


def test_an_item_needs_a_name_and_a_current_category(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    radios.retired_at = radios.created_at
    session.flush()
    refused = admin_client.post(
        ITEMS, data={"name": "", "category_id": str(radios.id), "unit": "each"}
    )
    assert refused.status_code == 400
    page = text(refused)
    assert "Give it a name" in page
    assert "Pick a category" in page
    assert "Add a" in page  # No current categories: add one first.


def test_names_and_part_numbers_are_unique(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    add(
        session,
        Item(
            name="LiteBeam",
            category_id=radios.id,
            manufacturer="Ubiquiti",
            part_number="LBE",
        ),
    )
    for form in (
        {"name": "litebeam"},
        {"name": "Other", "manufacturer": "ubiquiti", "part_number": "lbe"},
    ):
        refused = admin_client.post(
            ITEMS, data={"category_id": str(radios.id), "unit": "each"} | form
        )
        assert refused.status_code == 400
        assert "Another item already has" in text(refused)


def test_searching(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    new_item(session, radios, "LiteBeam 5AC")
    new_item(session, radios, "Patch cable")
    page = text(admin_client.get(ITEMS, params={"q": "litebeem"}))
    assert "LiteBeam 5AC" in page
    assert "Patch cable" not in page


def test_editing_every_field(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios)
    cables = add(session, Category(name="Cables"))
    form = {
        "name": "Cat6 cable",
        "category_id": str(cables.id),
        "unit": "m",
        "manufacturer": "",
        "part_number": "",
        "old_code": "NYCM-0042",
        "minimum": "305",
        "notes": "Outdoor",
        "ordering_notes": "Buy by the reel",
    }
    done = admin_client.post(f"{ITEMS}/{item.id}", data=form)
    assert done.headers["location"] == f"{ITEMS}/{item.id}?notice=saved"
    assert (item.name, item.category_id, item.unit) == ("Cat6 cable", cables.id, "m")
    assert (item.old_code, item.minimum, item.manufacturer) == (
        "NYCM-0042",
        Decimal(305),
        None,
    )
    assert 'value="305"' in admin_client.get(f"{ITEMS}/{item.id}").text


def test_the_unit_is_fixed_once_stock_has_moved(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios)
    store = add(session, Location(name="Hub", kind="store"))
    batch = add(
        session,
        LedgerTransaction(
            idempotency_key=uuid.uuid4(), kind="receipt", to_location_id=store.id
        ),
    )
    add(session, Movement(transaction_id=batch.id, item_id=item.id, quantity=1))
    page = text(admin_client.get(f"{ITEMS}/{item.id}"))
    assert "fixed, as stock has moved in it" in page
    form = {"name": item.name, "category_id": str(radios.id), "unit": "m"}
    admin_client.post(f"{ITEMS}/{item.id}", data=form)
    assert item.unit == "each"


def test_packages(admin_client: TestClient, session: Session, radios: Category) -> None:
    item = new_item(session, radios)
    path = f"{ITEMS}/{item.id}/packages"
    done = admin_client.post(path, data={"package_name": "Box", "size": "4"})
    assert done.status_code == 303
    (box,) = session.scalars(select(ItemPackage)).all()
    assert (box.name, box.size) == ("Box", Decimal(4))
    assert "4 each" in text(admin_client.get(f"{ITEMS}/{item.id}"))
    taken = admin_client.post(path, data={"package_name": "box", "size": "2"})
    assert "already has a package by that name" in text(taken)
    for size in ("0", "-1", "lots", "1.2345", ""):
        refused = admin_client.post(path, data={"package_name": "Bag", "size": size})
        assert refused.status_code == 400, size
    admin_client.post(f"{path}/{box.id}/retire")
    assert box.retired_at is not None


def test_retiring_and_bringing_back(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios)
    admin_client.post(f"{ITEMS}/{item.id}/retire")
    assert item.retired_at is not None
    assert "Retired" in text(admin_client.get(ITEMS))
    new_item(session, radios)  # Its name, taken while it was retired.
    clash = admin_client.post(f"{ITEMS}/{item.id}/restore")
    assert clash.status_code == 400
    assert "change one first" in text(clash)


def test_unknown_items_and_others_packages_are_not_found(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios)
    other = new_item(session, radios, "Other")
    theirs = add(session, ItemPackage(item_id=other.id, name="Box", size=4))
    assert admin_client.get(f"{ITEMS}/999999").status_code == 404
    path = f"{ITEMS}/{item.id}/packages/{theirs.id}/retire"
    assert admin_client.post(path).status_code == 404


@pytest.mark.parametrize(
    ("typed", "positive", "parsed"),
    [
        ("", True, None),
        (" 2.5 ", True, Decimal("2.5")),
        ("0", False, Decimal(0)),
        ("0", True, "Use a number above 0."),
        ("-1", False, "Use 0 or more."),
        ("1.2345", False, "Use at most 3 decimal places."),
        ("NaN", False, "That number is too big."),
        ("1e12", False, "That number is too big."),
        ("lots", False, "Type a number, such as 12 or 0.5."),
    ],
)
def test_quantities_typed(typed: str, positive: bool, parsed: object) -> None:
    assert quantity(typed, positive=positive) == parsed


def test_a_retired_item_says_what_is_left(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios)
    store = add(session, Location(name="Hub", kind="store"))
    batch = add(
        session,
        LedgerTransaction(
            idempotency_key=uuid.uuid4(), kind="receipt", to_location_id=store.id
        ),
    )
    add(session, Movement(transaction_id=batch.id, item_id=item.id, quantity=3))
    admin_client.post(f"{ITEMS}/{item.id}/retire")
    page = text(admin_client.get(f"{ITEMS}/{item.id}"))
    assert "can't be received" in page
    assert "take the 3 each left, until it runs out" in page


def test_an_item_comes_back_only_into_a_current_category(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios)
    admin_client.post(f"{ITEMS}/{item.id}/retire")
    radios.retired_at = radios.created_at
    session.flush()
    refused = admin_client.post(f"{ITEMS}/{item.id}/restore")
    assert refused.status_code == 400
    assert "Its category is retired" in text(refused)
    assert item.retired_at is not None


def test_changes_are_logged_by_id(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        admin_client.post(
            ITEMS,
            data={"name": "Radio X", "category_id": str(radios.id), "unit": "each"},
        )
    (line,) = [line for line in logs if line["event"] == "item added"]
    assert line["item_id"] == named(session, Item, "Radio X").id
    assert "Radio X" not in str(line)
