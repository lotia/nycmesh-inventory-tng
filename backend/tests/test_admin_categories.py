"""Categories, and the pattern every catalogue page follows (admin/)."""

import html
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_models import add, history

from inventory_tng.admin.categories import CATEGORIES
from inventory_tng.models import Admin, Category, Item

pytestmark = pytest.mark.usefixtures("admin_client")


def text(response: Response) -> str:
    return html.unescape(response.text)


def by_name(session: Session, name: str) -> Category:
    return session.scalars(select(Category).where(Category.name == name)).one()


def test_the_pages_need_signing_in(make_client: Callable[..., TestClient]) -> None:
    assert make_client().get(CATEGORIES).status_code == 303


def test_the_home_page_leads_here(admin_client: TestClient) -> None:
    page = text(admin_client.get("/admin"))
    assert f'href="{CATEGORIES}"' in page


def test_adding_a_category_is_recorded_as_the_admin(
    admin_client: TestClient, session: Session
) -> None:
    done = admin_client.post(CATEGORIES, data={"name": " Radios "})
    assert done.headers["location"] == f"{CATEGORIES}?notice=category_added"
    radios = by_name(session, "Radios")
    (added,) = history(session, "category", radios.id)
    ada = session.scalars(select(Admin)).one()
    assert added.actor == f"admin:{ada.id}"
    page = text(admin_client.get(done.headers["location"]))
    assert "Category added." in page
    assert "Radios" in page


@pytest.mark.parametrize("name", ["", "x" * 201])
def test_a_name_must_be_given_and_not_too_long(
    admin_client: TestClient, name: str
) -> None:
    refused = admin_client.post(CATEGORIES, data={"name": name})
    assert refused.status_code == 400
    assert "Give it a name" in text(refused)


def test_names_are_unique_ignoring_case(
    admin_client: TestClient, session: Session
) -> None:
    add(session, Category(name="Radios"))
    refused = admin_client.post(CATEGORIES, data={"name": "radios"})
    assert refused.status_code == 400
    assert "already a category by that name" in text(refused)


def test_renaming(admin_client: TestClient, session: Session) -> None:
    radios = add(session, Category(name="Radios"))
    add(session, Category(name="Cables"))
    path = f"{CATEGORIES}/{radios.id}"
    assert (
        admin_client.post(f"{path}/name", data={"name": "Antennas"}).status_code == 303
    )
    assert radios.name == "Antennas"
    taken = admin_client.post(f"{path}/name", data={"name": "CABLES"})
    assert taken.status_code == 400
    assert radios.name == "Antennas"


def test_a_category_with_current_items_is_not_retired(
    admin_client: TestClient, session: Session
) -> None:
    radios = add(session, Category(name="Radios"))
    add(session, Item(name="LiteBeam", category_id=radios.id))
    refused = admin_client.post(f"{CATEGORIES}/{radios.id}/retire")
    assert refused.status_code == 400
    assert "Move or retire its items first" in text(refused)
    assert radios.retired_at is None


def test_retiring_and_bringing_back(admin_client: TestClient, session: Session) -> None:
    radios = add(session, Category(name="Radios"))
    path = f"{CATEGORIES}/{radios.id}"
    assert admin_client.post(f"{path}/retire").status_code == 303
    assert radios.retired_at is not None
    page = text(admin_client.get(CATEGORIES))
    assert "Retired" in page
    # Its name is free again while it is retired...
    add(session, Category(name="Radios"))
    # ...so bringing it back clashes, and says so.
    clash = admin_client.post(f"{path}/restore")
    assert clash.status_code == 400
    assert "rename one first" in text(clash)
    admin_client.post(f"{path}/name", data={"name": "Old radios"})
    assert admin_client.post(f"{path}/restore").status_code == 303
    assert radios.retired_at is None


def test_an_unknown_category_is_not_found(admin_client: TestClient) -> None:
    assert admin_client.get(f"{CATEGORIES}/999999").status_code == 404
    assert admin_client.post(f"{CATEGORIES}/999999/retire").status_code == 404
