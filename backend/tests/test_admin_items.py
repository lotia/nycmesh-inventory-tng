"""Items and their packages (admin/items.py)."""

import re
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng.admin.common import quantity
from inventory_tng.admin.items import ITEMS
from inventory_tng.admin.listing import PER_PAGE
from inventory_tng.local_time import DEFAULT_ZONE
from inventory_tng.models import (
    Category,
    Item,
    ItemPackage,
    LedgerTransaction,
    Location,
    Movement,
)

from helpers.rows import add, history, named, shown
from helpers.sign_in import text


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
        "version": shown(item),
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
            time_zone=DEFAULT_ZONE,
            idempotency_key=uuid.uuid4(),
            kind="receipt",
            to_location_id=store.id,
        ),
    )
    add(session, Movement(transaction_id=batch.id, item_id=item.id, quantity=1))
    page = text(admin_client.get(f"{ITEMS}/{item.id}"))
    assert "fixed, as stock has moved in it" in page
    assert '<input type="hidden" name="unit" value="each">' in page
    form = {"name": item.name, "category_id": str(radios.id), "unit": "m"}
    form |= {"version": shown(item)}
    refused = admin_client.post(f"{ITEMS}/{item.id}", data=form)
    assert refused.status_code == 400
    assert "Its unit is fixed, as stock has moved in it." in text(refused)
    assert item.unit == "each"
    # The form sends the fixed unit back as it is, so the rest still saves.
    kept = admin_client.post(f"{ITEMS}/{item.id}", data=form | {"unit": "each"})
    assert kept.status_code == 303
    # A form without a unit is refused, rather than keeping the one stored.
    unitless = admin_client.post(f"{ITEMS}/{item.id}", data=form | {"unit": ""})
    assert "Pick a unit." in text(unitless)
    negative = admin_client.post(f"{ITEMS}/{item.id}", data=form | {"minimum": "-1"})
    assert negative.status_code == 400


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
    assert admin_client.post(f"{ITEMS}/999999", data={}).status_code == 404
    assert admin_client.post(f"{ITEMS}/999999/packages").status_code == 404
    assert admin_client.post(f"{ITEMS}/999999/retire").status_code == 404
    assert admin_client.post(f"{ITEMS}/999999/restore").status_code == 404
    # A current item is not brought back.
    assert admin_client.post(f"{ITEMS}/{item.id}/restore").status_code == 404
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
            time_zone=DEFAULT_ZONE,
            idempotency_key=uuid.uuid4(),
            kind="receipt",
            to_location_id=store.id,
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


def test_bringing_an_item_back(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios)
    admin_client.post(f"{ITEMS}/{item.id}/retire")
    done = admin_client.post(f"{ITEMS}/{item.id}/restore")
    assert done.status_code == 303
    session.refresh(item)
    assert item.retired_at is None


def test_an_edit_with_a_problem_changes_nothing(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios)
    refused = admin_client.post(
        f"{ITEMS}/{item.id}",
        data={"name": "", "category_id": str(radios.id), "unit": "each"},
    )
    assert refused.status_code == 400
    session.refresh(item)
    assert item.name == "LiteBeam"


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


def items_named(page: str) -> list[str]:
    """The items the list shows, in its order."""
    return re.findall(r'<td><a href="/admin/items/\d+">([^<]+)</a>', page)


def test_a_search_forgives_typos_best_first(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    for name in ("LiteAP AC", "LiteBeam 5AC", "Patch cable", "NanoBeam"):
        new_item(session, radios, name)
    page = text(admin_client.get(ITEMS, params={"q": "litebeam"}))
    shown = items_named(page)
    assert shown[0] == "LiteBeam 5AC"
    assert "Patch cable" not in shown
    total = session.scalar(
        select(func.count()).select_from(Item).where(Item.retired_at.is_(None))
    )
    said = " ".join(page.split())
    assert f"{len(shown)} of {total} items, matching “litebeam”" in said
    assert 'href="/admin/items">Clear search</a>' in page


def test_suggestions_come_from_the_filter_the_list_shows(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    beam = new_item(session, radios, f"LiteBeam {uuid.uuid4().hex[:6]}")
    old = new_item(session, radios, f"LiteBeam {uuid.uuid4().hex[:6]}")
    admin_client.post(f"{ITEMS}/{old.id}/retire")
    current = admin_client.get(f"{ITEMS}/suggest", params={"q": "litebem"}).json()
    names = [each["name"] for each in current]
    assert beam.name in names
    assert old.name not in names
    assert current[names.index(beam.name)] == {
        "name": beam.name,
        "hint": radios.name,
        "href": f"{ITEMS}?pick={beam.id}",
    }
    retired = admin_client.get(
        f"{ITEMS}/suggest", params={"q": "litebem", "show": "retired"}
    ).json()
    assert [each["name"] for each in retired] == [old.name]
    assert retired[0]["href"] == f"{ITEMS}?show=retired&pick={old.id}"
    assert admin_client.get(f"{ITEMS}/suggest").json() == []


def test_picking_a_suggestion_narrows_the_list_to_it(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    beam = new_item(session, radios, "LiteBeam 5AC")
    new_item(session, radios, "LiteBeam M5")
    page = text(admin_client.get(ITEMS, params={"pick": str(beam.id)}))
    assert items_named(page) == ["LiteBeam 5AC"]
    assert "matching “LiteBeam 5AC”" in " ".join(page.split())


def test_current_retired_and_all(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    kept = new_item(session, radios, "Kept")
    gone = new_item(session, radios, "Gone")
    admin_client.post(f"{ITEMS}/{gone.id}/retire")
    for show, expected in (
        ("", {"Kept"}),
        ("retired", {"Gone"}),
        ("all", {"Kept", "Gone"}),
    ):
        page = text(admin_client.get(ITEMS, params={"show": show}))
        assert {kept.name, gone.name} & set(items_named(page)) == expected
        chosen = show or "current"
        link = f'href="{ITEMS}{"?show=" + show if show else ""}" aria-current="page"'
        assert link in page, chosen
    assert '<span class="tag">Retired</span>' in text(
        admin_client.get(ITEMS, params={"show": "all"})
    )


def test_pages_of_fifty(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    for number in range(PER_PAGE + 5):
        new_item(session, radios, f"Paged {number:03}")
    first = text(admin_client.get(ITEMS, params={"q": "Paged"}))
    assert len(items_named(first)) == PER_PAGE
    assert "Page 1 of 2" in first
    assert (
        f'href="{ITEMS}?q=Paged&amp;page=2"'
        in admin_client.get(ITEMS, params={"q": "Paged"}).text
    )
    second = text(admin_client.get(ITEMS, params={"q": "Paged", "page": "2"}))
    assert len(items_named(second)) == 5
    # A page past the end, or none, is the last or the first.
    assert "Page 2 of 2" in text(
        admin_client.get(ITEMS, params={"q": "Paged", "page": "9"})
    )
    assert "Page 1 of 2" in text(
        admin_client.get(ITEMS, params={"q": "Paged", "page": "x"})
    )


def test_an_empty_list_says_what_to_do_next(admin_client: TestClient) -> None:
    page = text(admin_client.get(ITEMS, params={"q": "zzzqqqxxx"}))
    assert "Nothing matches. Check the spelling" in page
