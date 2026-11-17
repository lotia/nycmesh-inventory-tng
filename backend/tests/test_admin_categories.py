"""Categories, and the pattern every catalogue page follows (admin/)."""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.admin.categories import CATEGORIES, TAKEN
from inventory_tng.admins import guards
from inventory_tng.models import Admin, Category, Item
from inventory_tng.web.forms import RULES

from helpers.markup import links, read, row_links, text
from helpers.rows import add, history, named, shown

pytestmark = pytest.mark.usefixtures("admin_client")


def test_the_pages_need_signing_in(make_client: Callable[..., TestClient]) -> None:
    answer = make_client().get(CATEGORIES)
    assert answer.status_code == 303
    assert answer.headers["location"].startswith(guards.SIGN_IN)


def test_the_home_page_leads_here(admin_client: TestClient) -> None:
    page = text(admin_client.get("/admin"))
    assert f'href="{CATEGORIES}"' in page


def test_adding_a_category_is_recorded_as_the_admin(
    admin_client: TestClient, session: Session
) -> None:
    done = admin_client.post(CATEGORIES, data={"name": " Radios "})
    assert done.headers["location"] == f"{CATEGORIES}?notice=added"
    radios = named(session, Category, "Radios")
    (added,) = history(session, "category", radios.id)
    ada = session.scalars(select(Admin)).one()
    assert added.actor == f"admin:{ada.id}"
    page = text(admin_client.get(done.headers["location"]))
    assert "Added." in page
    assert "Radios" in page


# The last as stored: ℃ is stored °C (models.canonical).
@pytest.mark.parametrize("name", ["", "x" * 201, "x" * 199 + "\u2103"])
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
    assert str(TAKEN) in text(refused)


def test_renaming(admin_client: TestClient, session: Session) -> None:
    radios = add(session, Category(name="Radios"))
    add(session, Category(name="Cables"))
    path = f"{CATEGORIES}/{radios.id}"
    assert (
        admin_client.post(
            f"{path}/name", data={"name": "Antennas", "version": shown(radios)}
        ).status_code
        == 303
    )
    assert radios.name == "Antennas"
    taken = admin_client.post(
        f"{path}/name", data={"name": "CABLES", "version": shown(radios)}
    )
    assert taken.status_code == 400
    assert radios.name == "Antennas"
    blank = admin_client.post(
        f"{path}/name", data={"name": " ", "version": shown(radios)}
    )
    assert blank.status_code == 400
    assert radios.name == "Antennas"


def test_a_category_with_current_items_is_not_retired(
    admin_client: TestClient, session: Session
) -> None:
    radios = add(session, Category(name="Radios"))
    add(session, Item(name="LiteBeam", category_id=radios.id))
    refused = admin_client.post(f"{CATEGORIES}/{radios.id}/retire")
    assert refused.status_code == 400
    assert str(RULES["category_has_items"]) in text(refused)
    assert radios.retired_at is None


def test_retiring_and_bringing_back(admin_client: TestClient, session: Session) -> None:
    radios = add(session, Category(name="Radios"))
    path = f"{CATEGORIES}/{radios.id}"
    assert admin_client.post(f"{path}/retire").status_code == 303
    assert radios.retired_at is not None
    # Its name stays its own while it is retired, so it can come back.
    taken = admin_client.post(CATEGORIES, data={"name": "radios"})
    assert taken.status_code == 400
    assert str(TAKEN) in text(taken)
    # Retired, it is read only.
    refused = admin_client.post(
        f"{path}/name", data={"name": "Old radios", "version": shown(radios)}
    )
    assert refused.status_code == 409
    assert "Bring it back first" in text(refused)
    assert admin_client.post(f"{path}/restore").status_code == 303
    assert radios.retired_at is None


def test_an_unknown_category_is_not_found(admin_client: TestClient) -> None:
    assert admin_client.get(f"{CATEGORIES}/999999").status_code == 404
    assert admin_client.post(f"{CATEGORIES}/999999/retire").status_code == 404
    assert admin_client.post(f"{CATEGORIES}/999999/restore").status_code == 404


def test_a_page_shown_again_after_a_refusal_still_links_right(
    admin_client: TestClient, session: Session
) -> None:
    """Shown at the refused form's own address, it must not build its links
    from that address."""
    radios = add(session, Category(name="Radios"))
    add(session, Item(name="LiteBeam", category_id=radios.id))
    refused = admin_client.post(f"{CATEGORIES}/{radios.id}/retire")
    assert refused.status_code == 400
    page = refused.text
    assert f'action="{CATEGORIES}/{radios.id}/name"' in page
    back = read(page).one("p", class_="back")
    assert [href for _, href in links(back)] == [CATEGORIES]


def test_categories_sort_by_name_or_items_and_show_25_50_100_or_all(
    admin_client: TestClient, session: Session
) -> None:
    """By name unless a heading sorts them (Ali, 2026-11-15,
    inventory-tng-qwb)."""
    many = add(session, Category(name="Bravo"))
    add(session, Category(name="alpha"))
    add(session, Category(name="Charlie"))
    for number in range(2):
        add(session, Item(name=f"Thing {number}", category_id=many.id))

    def listed(**query: str) -> list[str]:
        page = admin_client.get(CATEGORIES, params=query).text
        return row_links(page, CATEGORIES)

    assert listed() == ["alpha", "Bravo", "Charlie"]
    assert listed(sort="name", order="desc") == ["Charlie", "Bravo", "alpha"]
    assert listed(sort="items") == ["alpha", "Charlie", "Bravo"]
    assert listed(sort="items", order="desc") == ["Bravo", "alpha", "Charlie"]
    for number in range(30):
        add(session, Category(name=f"Sized {number:03}"))
    assert len(listed(per="25")) == 25
    assert "Page 2 of 2" in text(
        admin_client.get(CATEGORIES, params={"per": "25", "page": "2"})
    )
    assert len(listed(per="all")) == 33
    page = admin_client.get(CATEGORIES).text
    assert ("Items", f"{CATEGORIES}?sort=items") in links(page)
