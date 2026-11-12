"""No page reads row by row (inventory-tng-du6): CONTRIBUTING.md, Running
the checks, says what this measures and what a new page needs here."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng import accounts, devices
from inventory_tng.admin.length_checks import KINDS
from inventory_tng.models import (
    Authenticator,
    Item,
    ItemPackage,
    Label,
    Location,
    Volunteer,
)

from helpers.devices import ADDRESS, new_device
from helpers.queries import counting
from helpers.rows import add, tag
from helpers.sign_in import admin_named, new_admin, sealed
from helpers.stock import Spools, cut, length, received, report, write

CATALOGUE = "/api/catalogue"


@dataclass
class World:
    """What every page reads from: a place with spools of cable (Spools);
    the labels made since, for the page that prints them; and a
    registration code, once a device is registered with it."""

    session: Session
    spools: Spools = field(init=False)
    labels: list[int] = field(default_factory=list)
    code_id: int = 0
    phrase: str = ""

    def __post_init__(self) -> None:
        self.spools = Spools(self.session)


def everything(world: World) -> None:
    """One more of all that the list pages show: a category, items, places,
    a volunteer, sealed spools, an item to reorder, one short at a place,
    an open package cut below zero, one said to show the wrong length, one
    said used up, and labels for a package and a place."""
    session = world.session
    spools = Spools(session)
    spools.cable.minimum = Decimal(10_000)
    spools.spool.minimum_sealed = 99
    write(session, spools.place, lines=[cut(spools.opened(), "400")])
    write(session, spools.place, **report(spools, length(spools.opened(), "250")))
    # Radios the store never had moved out: less than none there.
    write(session, spools.place)
    used_up = report(spools) | {"lengths": None, "used_up": [str(spools.opened().key)]}
    write(session, spools.place, **used_up)
    for label in (
        Label(item_id=spools.cable.id, package_id=spools.spool.id),
        Label(location_id=spools.store.id),
    ):
        world.labels.append(add(session, label).id)


def item_parts(world: World) -> None:
    """One more package of the world's cable, its label, an open package of
    it, and some of it at a place more."""
    spools = world.spools
    cable = spools.cable
    box = add(
        world.session, ItemPackage(item_id=cable.id, name=f"Box {tag()}", size=10)
    )
    add(world.session, Label(item_id=cable.id, package_id=box.id))
    spools.opened()
    van = add(world.session, Location(name=f"Van {tag()}", kind="vehicle"))
    write(
        world.session,
        spools.place,
        to_location_id=van.id,
        lines=[{"item_id": cable.id, "quantity": "1"}],
    )


def item_in_category(world: World) -> None:
    category_id = world.spools.cable.category_id
    add(world.session, Item(name=f"Radio {tag()}", category_id=category_id))


def in_store(world: World) -> None:
    """An area more in the world's store. A place has one current label."""
    store = world.spools.store
    area = add(
        world.session,
        Location(name=f"Shelf {tag()}", kind="area", parent_id=store.id),
    )
    add(world.session, Label(location_id=area.id))


def item_in_store(world: World) -> None:
    """An item more held at the world's store, to count there."""
    item = add(
        world.session,
        Item(name=f"Radio {tag()}", category_id=world.spools.cable.category_id),
    )
    received(world.session, world.spools.place, [{"item_id": item.id, "quantity": "1"}])


def volunteer(world: World) -> None:
    """Another volunteer, whom the world's could be merged into."""
    add(world.session, Volunteer(name=f"Volunteer {tag()}"))


def new_device_in(world: World) -> None:
    new_device(world.session)


def device_of_code(world: World) -> None:
    """A device more registered with the world's code."""
    if not world.code_id:
        code, world.phrase = devices.create_code(
            world.session, max_uses=50, lifetime=timedelta(days=1)
        )
        world.code_id = code.id
    devices.register(world.session, world.phrase, ADDRESS)


def admin(world: World) -> None:
    new_admin(world.session, f"admin-{uuid.uuid4().hex[:8]}", totp=False)


def authenticator(world: World) -> None:
    """Another authenticator of the administrator signed in (admin_client)."""
    ada = admin_named(world.session, "ada")
    key = accounts.new_totp_key()
    add(world.session, sealed(Authenticator(admin_id=ada.id, name=tag()), key))


More = Callable[[World], object]
Path = str | Callable[[World], str]

# Every page that lists rows, by its route: how to add one more of what it
# lists to a world, then where to find it there, each way if several, if
# not at the route itself.
PAGES: dict[str, tuple[More, *tuple[Path, ...]]] = {
    CATALOGUE: (everything,),
    "/admin": (everything,),
    "/admin/account": (authenticator,),
    "/admin/items": (everything,),
    "/admin/items/suggest": (everything, "/admin/items/suggest?q=Cable"),
    "/admin/items/{item_id}": (
        item_parts,
        lambda w: f"/admin/items/{w.spools.cable.id}",
    ),
    "/admin/categories": (everything,),
    "/admin/categories/{category_id}": (
        item_in_category,
        lambda w: f"/admin/categories/{w.spools.cable.category_id}",
    ),
    "/admin/locations": (everything,),
    "/admin/locations/{location_id}": (
        in_store,
        lambda w: f"/admin/locations/{w.spools.store.id}",
    ),
    "/admin/labels": (everything,),
    "/admin/labels/suggest": (everything, "/admin/labels/suggest?q=Cable"),
    "/admin/labels/print": (
        everything,
        lambda w: "/admin/labels/print?" + "&".join(f"label={i}" for i in w.labels),
    ),
    "/admin/volunteers": (everything,),
    "/admin/volunteers/suggest": (everything, "/admin/volunteers/suggest?q=Volunteer"),
    # Their custody is one place, or none: the others, to merge into, grow.
    "/admin/volunteers/{volunteer_id}": (
        volunteer,
        lambda w: f"/admin/volunteers/{w.spools.place.volunteer.id}",
    ),
    "/admin/stock": (everything, "/admin/stock", "/admin/stock?by=item"),
    "/admin/stock/reorder": (everything,),
    "/admin/stock/needs-counting": (everything,),
    "/admin/stock/used-up": (everything,),
    "/admin/stock/length-checks": (everything,),
    "/admin/stock/length-checks/{which}": (everything, *(k.path for k in KINDS)),
    "/admin/counts": (everything,),
    "/admin/counts/{location_id:int}": (
        item_in_store,
        lambda w: f"/admin/counts/{w.spools.store.id}",
    ),
    "/admin/devices": (new_device_in,),
    "/admin/devices/all": (new_device_in,),
    "/admin/devices/codes/{code_id}": (
        device_of_code,
        lambda w: f"/admin/devices/codes/{w.code_id}",
    ),
    "/admin/admins": (admin,),
}

# GET routes under /admin that list nothing that grows, and why.
EXEMPT = {
    "/admin/sign-in": "a form",
    "/admin/confirm": "a form",
    "/admin/authenticators/new": "a form",
    "/admin/forgot": "a form",
    "/admin/reset": "a form",
    "/admin/settings": "one row of each setting",
    "/admin/counts/place": "a redirect to the place picked",
    "/admin/labels/{label_id}/revoke": "a confirmation of one row",
    "/admin/volunteers/{volunteer_id}/merge": "a confirmation of one row",
    "/admin/items/{item_id}/open-packages/{open_id}/close": (
        "a confirmation of one row"
    ),
    "/admin/devices/codes/{code_id}/revoke": "a confirmation of one row",
    "/admin/devices/{device_id:int}": "one device",
    "/admin/devices/{device_id:int}/revoke": "a confirmation of one row",
    "/admin/admins/{admin_id}": "one administrator",
    "/admin/admins/{admin_id}/reset": "a confirmation of one row",
}


def test_every_admin_page_is_measured(
    make_app_without_database: Callable[..., FastAPI],
) -> None:
    pages = {
        route.path
        for route in iter_route_contexts(make_app_without_database().routes)
        if "GET" in (route.methods or ()) and (route.path or "").startswith("/admin")
    }
    assert pages, "no admin pages found: the routes are read wrongly"
    unmeasured = pages - PAGES.keys() - EXEMPT.keys()
    assert not unmeasured, f"add these to PAGES, or to EXEMPT with why: {unmeasured}"
    assert not PAGES.keys() & EXEMPT.keys()
    gone = (PAGES.keys() | EXEMPT.keys()) - pages - {CATALOGUE}
    assert not gone, f"no such pages: {gone}"


CASES = [
    pytest.param(route, more, where, id=where if isinstance(where, str) else route)
    for route, (more, *paths) in sorted(PAGES.items())
    for where in paths or [route]
]


@pytest.mark.parametrize(("route", "more", "where"), CASES)
def test_a_page_reads_alike_for_one_row_or_eleven(
    route: str,
    more: More,
    where: Path,
    request: pytest.FixtureRequest,
    session: Session,
) -> None:
    client: TestClient = request.getfixturevalue(
        "registered_client" if route == CATALOGUE else "admin_client"
    )
    world = World(session)
    more(world)

    def read() -> tuple[int, int]:
        """The statements the page sent, and how long it is."""
        path = where if isinstance(where, str) else where(world)
        # As a request's own session would start: nothing loaded.
        session.flush()
        session.expire_all()
        with counting(session) as sent:
            response = client.get(path)
        assert response.status_code == 200, (path, response.status_code)
        return len(sent), len(response.content)

    # Warmed first: what a first request reads once, such as settings.
    read()
    one, shorter = read()
    for _ in range(10):
        more(world)
    eleven, longer = read()
    # Else what it adds is not what the page lists.
    assert longer > shorter, f"{route} shows nothing more"
    assert eleven == one, f"{route}: {one} statements for one row, {eleven} for eleven"
