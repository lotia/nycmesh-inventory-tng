"""Locations: stores, areas and vehicles (admin/locations.py)."""

import html

from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_models import add, history

from inventory_tng.admin.locations import LOCATIONS
from inventory_tng.models import Location, Volunteer


def text(response: Response) -> str:
    return html.unescape(response.text)


def by_name(session: Session, name: str) -> Location:
    return session.scalars(select(Location).where(Location.name == name)).one()


def place(
    session: Session, name: str, kind: str, parent: Location | None = None
) -> Location:
    return add(
        session,
        Location(name=name, kind=kind, parent_id=parent.id if parent else None),
    )


def test_adding_a_store_and_an_area_inside_it(
    admin_client: TestClient, session: Session
) -> None:
    admin_client.post(LOCATIONS, data={"name": "Hub", "kind": "store"})
    hub = by_name(session, "Hub")
    assert (history(session, "location", hub.id)[0].actor or "").startswith("admin:")
    admin_client.post(
        LOCATIONS, data={"name": "Shelf A", "kind": "area", "parent_id": str(hub.id)}
    )
    assert by_name(session, "Shelf A").parent_id == hub.id
    page = text(admin_client.get(LOCATIONS))
    assert page.index("Hub") < page.index("Shelf A")


def test_an_area_needs_a_place_to_be(
    admin_client: TestClient, session: Session
) -> None:
    refused = admin_client.post(LOCATIONS, data={"name": "Shelf", "kind": "area"})
    assert refused.status_code == 400
    assert "Pick where it is." in text(refused)


def test_an_area_goes_inside_a_store_or_area_not_a_vehicle(
    admin_client: TestClient, session: Session
) -> None:
    van = place(session, "Van", "vehicle")
    refused = admin_client.post(
        LOCATIONS, data={"name": "Glovebox", "kind": "area", "parent_id": str(van.id)}
    )
    assert refused.status_code == 400
    assert "An area goes inside a store or another area." in text(refused)


def test_names_are_unique_beside_each_other_only(
    admin_client: TestClient, session: Session
) -> None:
    hub = place(session, "Hub", "store")
    depot = place(session, "Depot", "store")
    place(session, "Shelf A", "area", hub)
    taken = admin_client.post(
        LOCATIONS, data={"name": "shelf a", "kind": "area", "parent_id": str(hub.id)}
    )
    assert "already has that name" in text(taken)
    elsewhere = admin_client.post(
        LOCATIONS, data={"name": "Shelf A", "kind": "area", "parent_id": str(depot.id)}
    )
    assert elsewhere.status_code == 303


def test_moving_an_area_but_never_inside_itself(
    admin_client: TestClient, session: Session
) -> None:
    hub = place(session, "Hub", "store")
    depot = place(session, "Depot", "store")
    shelf = place(session, "Shelf", "area", hub)
    bin_ = place(session, "Bin", "area", shelf)
    path = f"{LOCATIONS}/{shelf.id}"
    assert (
        admin_client.post(
            path, data={"name": "Shelf", "parent_id": str(depot.id)}
        ).status_code
        == 303
    )
    assert shelf.parent_id == depot.id
    # A move under its own area is refused.
    loop = admin_client.post(path, data={"name": "Shelf", "parent_id": str(bin_.id)})
    assert loop.status_code == 400
    assert "inside itself" in text(loop)
    assert shelf.parent_id == depot.id


def test_retiring_needs_its_areas_gone_first(
    admin_client: TestClient, session: Session
) -> None:
    hub = place(session, "Hub", "store")
    shelf = place(session, "Shelf", "area", hub)
    refused = admin_client.post(f"{LOCATIONS}/{hub.id}/retire")
    assert refused.status_code == 400
    assert "Retire or move the areas inside it first." in text(refused)
    admin_client.post(f"{LOCATIONS}/{shelf.id}/retire")
    assert admin_client.post(f"{LOCATIONS}/{hub.id}/retire").status_code == 303
    # Bringing the area back first is refused: its store is retired.
    back = admin_client.post(f"{LOCATIONS}/{shelf.id}/restore")
    assert "bring that back first" in text(back)
    admin_client.post(f"{LOCATIONS}/{hub.id}/restore")
    assert admin_client.post(f"{LOCATIONS}/{shelf.id}/restore").status_code == 303
    assert shelf.retired_at is None


def test_custody_is_shown_but_changed_with_the_volunteer(
    admin_client: TestClient, session: Session
) -> None:
    ada = add(session, Volunteer(name="Ada"))
    custody = add(
        session, Location(name="Ada's bag", kind="custody", volunteer_id=ada.id)
    )
    page = text(admin_client.get(LOCATIONS))
    assert "Held by volunteers" in page
    assert "Ada's bag" in page
    path = f"{LOCATIONS}/{custody.id}"
    assert admin_client.post(path, data={"name": "Mine"}).status_code == 404
    assert admin_client.post(f"{path}/retire").status_code == 404
    assert "change it with the volunteer" in text(admin_client.get(path))
