"""A retired record's page (docs/decisions/0019): a Retired tag, when and
by whom, and read only, with bringing it back its one action; a save to it
is refused."""

import re
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.catalogue import categories
from inventory_tng.core.db import set_actor
from inventory_tng.models.admins import Admin
from inventory_tng.models.catalogue import Category, Item, ItemPackage
from inventory_tng.models.places import Location
from inventory_tng.models.volunteers import Volunteer
from inventory_tng.volunteers.volunteers_routes import BRING_THEM_BACK
from inventory_tng.web.admin_pages import retired_by
from inventory_tng.web.forms import RETIRED, RULES

from helpers.markup import read, text
from helpers.rows import add, shown, tag

KINDS = [
    ("/admin/items", "item"),
    ("/admin/categories", "category"),
    ("/admin/locations", "location"),
    ("/admin/volunteers", "volunteer"),
]


def made(session: Session, kind: str) -> Item | Category | Location | Volunteer:
    if kind == "item":
        category = add(session, Category(name=f"Spare {tag()}"))
        return add(session, Item(name=f"Spare {tag()}", category_id=category.id))
    return add(
        session,
        {
            "category": lambda: Category(name=f"Spare {tag()}"),
            "location": lambda: Location(name=f"Spare {tag()}", kind="store"),
            "volunteer": lambda: Volunteer(name=f"Spare {tag()}"),
        }[kind](),
    )


@pytest.mark.parametrize(("path", "kind"), KINDS)
def test_a_retired_records_page_says_when_and_by_whom_and_is_read_only(
    admin_client: TestClient, session: Session, path: str, kind: str
) -> None:
    row = made(session, kind)
    page = read(admin_client.get(f"{path}/{row.id}").text)
    assert not page.all("span", "Retired", class_="tag")
    assert any(
        b.text.startswith("Save") for b in page.all("button", class_="primary")
    ) or any(b.text.endswith("Rename") for b in page.all("button"))
    assert admin_client.post(f"{path}/{row.id}/retire").status_code == 303

    page = read(admin_client.get(f"{path}/{row.id}").text)
    (line,) = [p.text for p in page.all("p") if p.all("span", "Retired", class_="tag")]
    said = re.fullmatch(r"Retired on (.+) by (\w+)\.", line)
    assert said is not None
    # In the site's zone, New York's by default.
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d E[SD]T", said.group(1))
    assert said.group(2) == "ada"
    # Read only: its fields are disabled, with no Save and no Retire, and
    # bringing it back is its one button.
    fieldsets = page.all("fieldset", class_="contents")
    assert any("disabled" in f.attrs for f in fieldsets) or any(
        {"required", "disabled"} <= field.attrs.keys() for field in page.all("*")
    )
    buttons = [b.text for b in page.all("button")]
    assert [b for b in buttons if b not in ("Menu", "Sign out")] == [
        BRING_THEM_BACK if kind == "volunteer" else "Bring it back"
    ]


@pytest.mark.parametrize(
    ("path", "kind", "save"),
    [
        ("/admin/items", "item", ""),
        ("/admin/categories", "category", "/name"),
        ("/admin/locations", "location", ""),
        ("/admin/volunteers", "volunteer", ""),
    ],
)
def test_a_save_to_a_retired_record_is_refused(
    admin_client: TestClient, session: Session, path: str, kind: str, save: str
) -> None:
    row = made(session, kind)
    admin_client.post(f"{path}/{row.id}/retire")
    session.refresh(row)
    was = row.name
    form = {"name": "Changed", "version": shown(row)}
    if isinstance(row, Item):
        form |= {"category_id": str(row.category_id), "unit": row.unit}
    refused = admin_client.post(f"{path}/{row.id}{save}", data=form)
    assert refused.status_code == 409
    assert str(RULES[RETIRED]) in text(refused)
    session.refresh(row)
    assert row.name == was


def test_a_package_is_not_added_to_a_retired_item(
    admin_client: TestClient, session: Session
) -> None:
    item = made(session, "item")
    admin_client.post(f"/admin/items/{item.id}/retire")
    refused = admin_client.post(
        f"/admin/items/{item.id}/packages", data={"package_name": "Box", "size": "10"}
    )
    assert refused.status_code == 409
    assert str(RULES[RETIRED]) in text(refused)


def test_a_retired_items_package_is_not_retired(
    admin_client: TestClient, session: Session
) -> None:
    item = made(session, "item")
    package = add(session, ItemPackage(item_id=item.id, name="Box", size=10))
    admin_client.post(f"/admin/items/{item.id}/retire")
    refused = admin_client.post(f"/admin/items/{item.id}/packages/{package.id}/retire")
    assert refused.status_code == 409
    assert str(RULES[RETIRED]) in text(refused)
    session.refresh(package)
    assert package.retired_at is None


def test_a_retired_administrators_page_says_who_retired_them(
    admin_client: TestClient, session: Session
) -> None:
    ben = add(session, Admin(username=f"ben{tag()}"))
    ada = named_admin(session, "ada")
    set_actor(session, f"admin:{ada.id}")
    ben.retired_at = datetime.now(UTC)
    session.flush()
    response = admin_client.get(f"/admin/admins/{ben.id}")
    page = text(response)
    (line,) = [
        p.text
        for p in read(response.text).all("p")
        if p.all("span", "Retired", class_="tag")
    ]
    assert re.fullmatch(r"Retired on .+ by ada\.", line)
    assert "They can't sign in." in page


def named_admin(session: Session, username: str) -> Admin:
    return session.scalars(select(Admin).where(Admin.username == username)).one()


def test_who_retired_it_is_read_from_the_history(session: Session) -> None:
    """An administrator by username, a command as the operator, and nobody
    where the history names nobody; brought back and retired again, the
    last retire's."""
    row = add(session, Category(name=f"Spare {tag()}"))
    now = datetime.now(UTC)
    row.retired_at = now  # A session that named nobody.
    session.flush()
    assert retired_by(session, row) is None

    categories.restore(session, row)
    set_actor(session, "system:cli")
    categories.retire(session, row, now)
    session.flush()
    assert retired_by(session, row) == "the operator"

    ada = add(session, Admin(username=f"ada{tag()}"))
    categories.restore(session, row)
    set_actor(session, f"admin:{ada.id}")
    categories.retire(session, row, now)
    session.flush()
    assert retired_by(session, row) == ada.username
