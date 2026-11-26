"""Every page the sweeps visit (test_query_counts, test_phrases), and the
world they read: built once a module, in a database of its own, committed,
so a test only reads it, in a transaction of its own rolled back after."""

import copy
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng.admins import accounts, authenticator_totp, guards
from inventory_tng.core.db_sessions import DATABASE_INFO, Database, make_database
from inventory_tng.devices import devices
from inventory_tng.models.admins import Admin, Authenticator
from inventory_tng.models.catalogue import (
    Category,
    Item,
    ItemAlias,
    ItemPackage,
    ReadingRule,
)
from inventory_tng.models.devices import Device
from inventory_tng.models.labels import Label
from inventory_tng.models.places import Location
from inventory_tng.models.stock import OpenPackage
from inventory_tng.models.volunteers import Volunteer
from inventory_tng.stock import over_recorded
from inventory_tng.stock.length_checks_routes import KINDS

from helpers.apps import new_app, on_session
from helpers.databases import new_database
from helpers.devices import ADDRESS, new_device
from helpers.over_recorded import correct_check
from helpers.rows import add, rolled_back, setting_up, tag
from helpers.sign_in import new_admin, sealed
from helpers.stock import (
    Spools,
    cut,
    length,
    raise_check,
    received,
    report,
    retire,
    write,
)

CATALOGUE = "/api/catalogue"
# A name not in English, so what a page shows in English is its own words
# (test_phrases).
NOT_ENGLISH = "Ωμέγα"


@dataclass
class World:
    """What every page reads from: spools of cable in a store, one opened,
    with a label and an alias; an area in the store; a volunteer holding
    stock; a retired item, category and place; stock to put right (an item
    to reorder, less than none at a place, open packages cut below zero,
    said to show the wrong length and said used up, and a line recorded with
    more than was moved, and another one corrected); other volunteers,
    devices, a reading rule and administrators. Ada is signed in
    (`admin_cookie`), and the device registered (`device_cookie`). Then
    what each counted page lists, as many times as asked (build), with the
    labels made for the page that prints them, and a registration code once
    a device is registered with it."""

    session: Session
    ada_id: int
    spools: Spools
    opened: OpenPackage
    area: Location
    custody: Location
    retired_item: Item
    retired_category: Category
    retired_place: Location
    label: Label
    other: Volunteer
    merged: Volunteer
    device: Device
    rule: ReadingRule
    other_admin: Admin
    retired_admin: Admin
    admin_cookie: str
    device_cookie: str
    labels: list[int] = field(default_factory=list)
    code_id: int = 0
    phrase: str = ""
    authenticator_id: int = 0

    def on(self, session: Session) -> World:
        """This world writing in `session`, so what is added to it there
        goes with that session, and this one is left as built. The rows it
        holds are only read for their ids."""
        spools = copy.copy(self.spools)
        spools.session = session
        return replace(self, session=session, spools=spools, labels=list(self.labels))


def base(session: Session) -> World:
    ada, _ = new_admin(session)
    _, admin_cookie = accounts.start_session(session, ada.id, None, datetime.now(UTC))
    spools = Spools(session)
    opened = spools.opened()
    cable = spools.cable
    # An alias is never changed: named not in English as it is made.
    add(session, ItemAlias(item_id=cable.id, name=f"{NOT_ENGLISH} ἄλλο"))
    label = add(session, Label(item_id=cable.id, package_id=spools.spool.id))
    area = add(session, Location(name="Shelf", kind="area", parent_id=spools.store.id))
    custody = add(
        session,
        Location(
            name="Custody",
            kind="custody",
            volunteer_id=spools.place.volunteer.id,
        ),
    )
    retired_category = add(session, Category(name="Old"))
    retired_item = add(session, Item(name="Old", category_id=retired_category.id))
    retired_place = add(session, Location(name="Old", kind="store"))
    retire(session, retired_item, retired_category, retired_place)
    stock_to_put_right(spools)
    other = add(session, Volunteer(name="Other"))
    merged = add(
        session,
        Volunteer(name="Merged", merged_into_id=other.id, retired_at=datetime.now(UTC)),
    )
    device, device_cookie = new_device(session)
    revoked, _ = new_device(session)
    revoked.revoked_at = datetime.now(UTC)
    rule = add(session, ReadingRule(token="P", decided_by_admin_id=ada.id))
    # A unit after a number no rule settles, for the readings to propose.
    add(session, Item(name=f"{NOT_ENGLISH} 10g", category_id=cable.category_id))
    other_admin, _ = new_admin(session, "grace", email="grace@example.org")
    retired_admin, _ = new_admin(session, "hopper", totp=False)
    retired_admin.retired_at = datetime.now(UTC)
    world = World(
        session=session,
        ada_id=ada.id,
        spools=spools,
        opened=opened,
        area=area,
        custody=custody,
        retired_item=retired_item,
        retired_category=retired_category,
        retired_place=retired_place,
        label=label,
        other=other,
        merged=merged,
        device=device,
        rule=rule,
        other_admin=other_admin,
        retired_admin=retired_admin,
        admin_cookie=admin_cookie,
        device_cookie=device_cookie,
    )
    over_recorded_line(world)
    over_recorded_settled(world)
    # Ada's second, so removing it asks rather than refuses.
    world.authenticator_id = authenticator(world).id
    return world


def stock_to_put_right(spools: Spools) -> None:
    """An item to reorder, an open package cut below zero, one said to show
    the wrong length, less than none at a place, and one said used up."""
    session = spools.session
    spools.cable.minimum = Decimal(10_000)
    spools.spool.minimum_sealed = 99
    write(session, spools.place, lines=[cut(spools.opened(), "400")])
    write(session, spools.place, **report(spools, length(spools.opened(), "250")))
    # Radios the store never had moved out: less than none there.
    write(session, spools.place)
    write(session, spools.place, **report(spools, used_up=[spools.opened().key]))


def everything(world: World) -> None:
    """One more of all that the list pages show: a category, items, places,
    a volunteer, sealed spools, stock to put right (stock_to_put_right), one
    moved whole and said to show the wrong length there, a line recorded
    with more than was moved, and labels for a package and a place."""
    session = world.session
    spools = Spools(session)
    stock_to_put_right(spools)
    # Moved whole to the van, then said to show the wrong length there: a
    # place more for the length checks to read.
    van = spools.opened()
    write(session, spools.place, lines=[cut(van, "305") | {"whole": True}])
    at_van = report(spools, length(van, "250")) | {"from_location_id": spools.van.id}
    write(session, spools.place, **at_van)
    over_recorded_line(world)
    for label in (
        Label(item_id=spools.cable.id, package_id=spools.spool.id),
        Label(location_id=spools.store.id),
    ):
        world.labels.append(add(session, label).id)


def reading_rule(world: World) -> None:
    """A reading rule more, settled by an administrator."""
    session = world.session
    settled = session.scalar(select(func.count()).select_from(ReadingRule))
    rule = ReadingRule(token=f"{settled}P", decided_by_admin_id=world.ada_id)
    add(session, rule)


def unsettled_unit(world: World) -> None:
    """An item more whose name has a unit after a number no rule or word
    near it settles."""
    session = world.session
    count = session.scalar(select(func.count()).select_from(Item))
    add(
        session, Item(name=f"Box {count}0g", category_id=world.spools.cable.category_id)
    )


def over_recorded_line(world: World) -> None:
    """A line more recorded with more than was moved
    (stock/over_recorded.py)."""
    raise_check(world.session, write(world.session, world.spools.place))


def over_recorded_settled(world: World) -> None:
    """A line more recorded with more than was moved, corrected: its two
    places, each linked to its count, and who corrected it."""
    session = world.session
    check = raise_check(session, write(session, world.spools.place))
    correct_check(session, check, over_recorded.seen(check), world.ada_id)


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
        lines=[{"item_id": cable.id, "quantity": "1", "unit": cable.unit}],
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
    received(
        world.session,
        world.spools.place,
        [{"item_id": item.id, "quantity": "1", "unit": item.unit}],
    )


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
    new_admin(world.session, f"admin-{tag()}", totp=False)


def authenticator(world: World) -> Authenticator:
    """Another authenticator of the administrator signed in, Ada."""
    key = authenticator_totp.new_totp_key()
    authenticator = Authenticator(admin_id=world.ada_id, name=tag())
    return add(world.session, sealed(authenticator, key))


More = Callable[[World], object]
Path = str | Callable[[World], str]


@dataclass(frozen=True)
class Page:
    """How each sweep visits a GET route, or why it doesn't.

    test_query_counts: `more` adds one more of what the page lists, and
    `counted` is where to read it, each way if several, if not at the route
    itself; or `uncounted` says why it lists nothing that grows.
    test_phrases: `worded` is where to read it signed in, by name, and
    `signed_out` where signed out; or `unworded` says why neither."""

    more: More | None = None
    counted: tuple[Path, ...] = ()
    uncounted: str = ""
    worded: Mapping[str, Path] = field(default_factory=dict)
    signed_out: tuple[str, ...] = ()
    unworded: str = ""


A_FORM_OF_ONE_ROW = "a confirmation of one row"
DATA = "data the search box offers, which says no words"

# Every GET route under /admin, and the scan page's catalogue.
PAGES: dict[str, Page] = {
    CATALOGUE: Page(everything, unworded="the scan page's, which says its own words"),
    "/admin": Page(everything, worded={"home": "/admin"}),
    "/admin/account": Page(authenticator, worded={"your account": "/admin/account"}),
    "/admin/account/authenticators/{authenticator_id}/remove": Page(
        uncounted=A_FORM_OF_ONE_ROW,
        worded={
            "an authenticator to remove": lambda w: (
                f"/admin/account/authenticators/{w.authenticator_id}/remove"
            ),
        },
    ),
    "/admin/items": Page(
        everything,
        (
            "/admin/items",
            # Sorted by a heading, all on one page, and filtered.
            "/admin/items?sort=category&order=desc&per=all",
            lambda w: (
                f"/admin/items?q=Cable&sort=part&category={w.spools.cable.category_id}"
            ),
        ),
        worded={
            "items": "/admin/items",
            "items, retired": "/admin/items?show=retired",
            "items, searched": f"/admin/items?q={NOT_ENGLISH}",
        },
    ),
    "/admin/items/suggest": Page(
        everything, ("/admin/items/suggest?q=Cable",), unworded=DATA
    ),
    "/admin/items/{item_id}": Page(
        item_parts,
        (lambda w: f"/admin/items/{w.spools.cable.id}",),
        worded={
            "an item": lambda w: f"/admin/items/{w.spools.cable.id}",
            "a retired item": lambda w: f"/admin/items/{w.retired_item.id}",
        },
    ),
    "/admin/items/{item_id}/unit-recount": Page(
        item_parts,
        (lambda w: f"/admin/items/{w.spools.cable.id}/unit-recount?unit=ft",),
        worded={
            "an item's guided recount": lambda w: (
                f"/admin/items/{w.spools.cable.id}/unit-recount?unit=ft"
            ),
        },
    ),
    "/admin/items/{item_id}/unit-recounted": Page(
        uncounted="the places a recount brought to zero, named in its address",
        worded={
            "after a guided recount": lambda w: (
                f"/admin/items/{w.spools.cable.id}/unit-recounted"
                f"?unit=ft&places={w.spools.store.id}&labels=2&packages=retired"
            ),
        },
    ),
    "/admin/items/{item_id}/open-packages/{open_id}/close": Page(
        uncounted=A_FORM_OF_ONE_ROW,
        worded={
            "an open package to close": lambda w: (
                f"/admin/items/{w.spools.cable.id}/open-packages/{w.opened.id}/close"
            ),
        },
    ),
    "/admin/categories": Page(
        everything,
        ("/admin/categories", "/admin/categories?sort=items&order=desc&per=25"),
        worded={"categories": "/admin/categories"},
    ),
    "/admin/categories/{category_id}": Page(
        item_in_category,
        (lambda w: f"/admin/categories/{w.spools.cable.category_id}",),
        worded={
            "a category": lambda w: f"/admin/categories/{w.spools.cable.category_id}",
            "a retired category": lambda w: (
                f"/admin/categories/{w.retired_category.id}"
            ),
        },
    ),
    "/admin/locations": Page(everything, worded={"locations": "/admin/locations"}),
    "/admin/locations/{location_id}": Page(
        in_store,
        (lambda w: f"/admin/locations/{w.spools.store.id}",),
        worded={
            "a store": lambda w: f"/admin/locations/{w.spools.store.id}",
            "an area": lambda w: f"/admin/locations/{w.area.id}",
            "a volunteer's custody": lambda w: f"/admin/locations/{w.custody.id}",
            "a retired place": lambda w: f"/admin/locations/{w.retired_place.id}",
        },
    ),
    "/admin/labels": Page(
        everything,
        (
            "/admin/labels",
            "/admin/labels?sort=made&per=all&kind=place",
            lambda w: f"/admin/labels?q=Cable&sort=code&item={w.spools.cable.id}",
        ),
        worded={
            "labels": "/admin/labels",
            "labels, inactive": "/admin/labels?show=inactive",
        },
    ),
    "/admin/labels/suggest": Page(
        everything, ("/admin/labels/suggest?q=Cable",), unworded=DATA
    ),
    "/admin/labels/print": Page(
        everything,
        (lambda w: "/admin/labels/print?" + "&".join(f"label={i}" for i in w.labels),),
        worded={"labels to print": lambda w: f"/admin/labels/print?label={w.label.id}"},
    ),
    "/admin/labels/{label_id}/revoke": Page(
        uncounted=A_FORM_OF_ONE_ROW,
        worded={"a label to revoke": lambda w: f"/admin/labels/{w.label.id}/revoke"},
    ),
    "/admin/volunteers": Page(
        everything,
        ("/admin/volunteers", "/admin/volunteers?sort=holds&per=all&holds=no"),
        worded={
            "volunteers": "/admin/volunteers",
            "volunteers, searched": f"/admin/volunteers?q={NOT_ENGLISH}",
        },
    ),
    "/admin/volunteers/suggest": Page(
        everything, ("/admin/volunteers/suggest?q=Volunteer",), unworded=DATA
    ),
    # Their custody is one place, or none: the others, to merge into, grow.
    "/admin/volunteers/{volunteer_id}": Page(
        volunteer,
        (lambda w: f"/admin/volunteers/{w.spools.place.volunteer.id}",),
        worded={
            "a volunteer": lambda w: f"/admin/volunteers/{w.spools.place.volunteer.id}",
            "a volunteer merged": lambda w: f"/admin/volunteers/{w.merged.id}",
        },
    ),
    "/admin/volunteers/{volunteer_id}/merge": Page(
        uncounted=A_FORM_OF_ONE_ROW,
        worded={
            "a volunteer to merge": lambda w: (
                f"/admin/volunteers/{w.spools.place.volunteer.id}/merge"
                f"?into_id={w.other.id}"
            ),
        },
    ),
    "/admin/stock": Page(
        everything,
        (
            "/admin/stock",
            "/admin/stock?by=item",
            # Sorted within groups, a page of groups, and filtered.
            "/admin/stock?by=item&sort=quantity&order=desc&per=25",
            lambda w: f"/admin/stock?q=Cable&place={w.spools.store.id}&per=all",
        ),
        worded={
            "stock by place": "/admin/stock",
            "stock by item": "/admin/stock?by=item",
            "stock, filtered to nothing": "/admin/stock?q=qqqq",
        },
    ),
    "/admin/stock/reorder": Page(
        everything,
        ("/admin/stock/reorder", "/admin/stock/reorder?sort=usable&per=all"),
        worded={"reorder": "/admin/stock/reorder"},
    ),
    "/admin/stock/needs-counting": Page(
        everything,
        (
            "/admin/stock/needs-counting",
            "/admin/stock/needs-counting?sort=quantity&per=25",
        ),
        worded={"needs counting": "/admin/stock/needs-counting"},
    ),
    "/admin/stock/used-up": Page(
        everything,
        ("/admin/stock/used-up", "/admin/stock/used-up?sort=shows&per=all"),
        worded={"used up": "/admin/stock/used-up"},
    ),
    "/admin/stock/length-checks": Page(
        everything,
        (
            "/admin/stock/length-checks",
            "/admin/stock/length-checks?sort=package&order=desc&per=25",
        ),
        worded={"length checks": "/admin/stock/length-checks"},
    ),
    "/admin/stock/length-checks/{which}": Page(
        everything,
        tuple(kind.path for kind in KINDS),
        worded={f"length checks, {kind.path}": kind.path for kind in KINDS},
    ),
    "/admin/stock/recorded-more": Page(
        over_recorded_line,
        (
            "/admin/stock/recorded-more",
            "/admin/stock/recorded-more?sort=amount&per=all",
        ),
        worded={"recorded more than moved": "/admin/stock/recorded-more"},
    ),
    "/admin/stock/recorded-more/settled": Page(
        over_recorded_settled,
        (
            "/admin/stock/recorded-more/settled",
            "/admin/stock/recorded-more/settled?sort=how&per=all",
            "/admin/stock/recorded-more/settled?sort=why&order=desc&per=25",
        ),
        worded={
            "recorded more than moved, settled": "/admin/stock/recorded-more/settled"
        },
    ),
    "/admin/counts": Page(
        everything,
        (
            "/admin/counts",
            "/admin/counts?sort=where&per=all&kind=count",
            lambda w: f"/admin/counts?place={w.spools.store.id}&sort=by&order=desc",
        ),
        worded={"counts": "/admin/counts"},
    ),
    "/admin/counts/place": Page(
        uncounted="a redirect to the place picked", unworded="a redirect"
    ),
    "/admin/counts/{location_id:int}": Page(
        item_in_store,
        (
            lambda w: f"/admin/counts/{w.spools.store.id}",
            # Only the items a search finds.
            lambda w: f"/admin/counts/{w.spools.store.id}?q=Radio",
        ),
        worded={
            "a count": lambda w: f"/admin/counts/{w.spools.store.id}",
            "a count of one item": lambda w: (
                f"/admin/counts/{w.spools.store.id}"
                f"?item={w.spools.cable.id}&q={NOT_ENGLISH}"
            ),
        },
    ),
    "/admin/unit-readings": Page(
        reading_rule,
        (
            "/admin/unit-readings",
            "/admin/unit-readings?sort=read_as&per=all&by=admin&unit=p",
        ),
        worded={"unit readings": "/admin/unit-readings"},
    ),
    "/admin/unit-readings/seed": Page(
        unsettled_unit,
        worded={
            "unit readings to settle": "/admin/unit-readings/seed",
            "unit readings to settle, one by one": "/admin/unit-readings/seed?one=1",
        },
    ),
    "/admin/unit-readings/{rule_id:int}": Page(
        uncounted="one reading rule",
        worded={"a unit reading": lambda w: f"/admin/unit-readings/{w.rule.id}"},
    ),
    "/admin/devices": Page(
        new_device_in,
        ("/admin/devices", "/admin/devices?sort=used&per=all&state=used+up"),
        worded={"devices": "/admin/devices"},
    ),
    "/admin/devices/all": Page(
        new_device_in,
        ("/admin/devices/all", "/admin/devices/all?sort=used&order=asc&per=all"),
        worded={"devices, all": "/admin/devices/all?state=all"},
    ),
    "/admin/devices/{device_id:int}": Page(
        uncounted="one device",
        worded={"a device": lambda w: f"/admin/devices/{w.device.id}"},
    ),
    "/admin/devices/{device_id:int}/revoke": Page(
        uncounted=A_FORM_OF_ONE_ROW,
        worded={"a device to revoke": lambda w: f"/admin/devices/{w.device.id}/revoke"},
    ),
    "/admin/devices/codes/{code_id}": Page(
        device_of_code,
        (lambda w: f"/admin/devices/codes/{w.code_id}",),
        worded={
            "a code": lambda w: f"/admin/devices/codes/{w.device.registration_code_id}"
        },
    ),
    "/admin/devices/codes/{code_id}/revoke": Page(
        uncounted=A_FORM_OF_ONE_ROW,
        worded={
            "a code to revoke": lambda w: (
                f"/admin/devices/codes/{w.device.registration_code_id}/revoke"
            ),
        },
    ),
    "/admin/admins": Page(
        admin,
        ("/admin/admins", "/admin/admins?sort=authenticator&order=asc&per=25"),
        worded={"administrators": "/admin/admins"},
    ),
    "/admin/admins/{admin_id}": Page(
        uncounted="one administrator",
        worded={
            "an administrator": lambda w: f"/admin/admins/{w.other_admin.id}",
            "a retired administrator": lambda w: f"/admin/admins/{w.retired_admin.id}",
        },
    ),
    "/admin/admins/{admin_id}/reset": Page(
        uncounted=A_FORM_OF_ONE_ROW,
        worded={
            "an administrator to reset": lambda w: (
                f"/admin/admins/{w.other_admin.id}/reset"
            ),
        },
    ),
    "/admin/settings": Page(
        uncounted="one row of each setting", worded={"settings": "/admin/settings"}
    ),
    "/admin/authenticators/new": Page(
        uncounted="a form", worded={"a new authenticator": "/admin/authenticators/new"}
    ),
    "/admin/sign-in": Page(uncounted="a form", signed_out=("/admin/sign-in",)),
    "/admin/forgot": Page(uncounted="a form", signed_out=("/admin/forgot",)),
    "/admin/reset": Page(uncounted="a form", signed_out=("/admin/reset?token=none",)),
    "/admin/confirm": Page(
        uncounted="a form",
        unworded="a form another page sends one to: test_phrases.COVERED renders it",
    ),
}


def path(where: Path, world: World) -> str:
    return where if isinstance(where, str) else where(world)


def build(session: Session, times: int) -> World:
    """The base world, then `times` more of what each counted page lists."""
    world = base(session)
    mores = dict.fromkeys(more for page in PAGES.values() if (more := page.more))
    for _ in range(times):
        for more in mores:
            more(world)
    return world


# Longer than any run, and still a time once taken from now.
UNSEEN = timedelta(days=36500)


@dataclass
class Built:
    """A world built and committed in its own database, and one app on it
    with its client, for the module."""

    database: Database
    world: World
    app: FastAPI
    client: TestClient

    @contextmanager
    def visit(self, *, device: bool = False) -> Generator[tuple[TestClient, Session]]:
        """The client signed in as Ada, or as the registered device, its
        requests on a session of the world's database rolled back after:
        whatever a page writes is not left for the next test. What the app
        writes in a transaction of its own would reach the world's database:
        when Ada or the device was last seen (db_sessions.record_last_seen) is
        never written, its wait made longer than any run."""
        with (
            patch.object(accounts, "SEEN_EVERY", UNSEEN),
            patch.object(devices, "LAST_SEEN_EVERY", UNSEEN),
            rolled_back(self.database.engine, self.database) as session,
        ):
            self.app.dependency_overrides |= on_session(session)
            self.client.cookies.clear()
            if device:
                self.client.cookies.set(devices.COOKIE, self.world.device_cookie)
            else:
                self.client.cookies.set(guards.COOKIE, self.world.admin_cookie)
            yield self.client, session


@contextmanager
def built(
    template: str, times: int, then: Callable[[Session], object] = lambda _: None
) -> Generator[Built]:
    """A database of its own copied from `template` (conftest's
    migrated_template), dropped after, holding the world build makes
    `times` over, and what `then` does to it, committed."""
    with new_database(template=template) as url:
        database = make_database(url)
        try:
            with Session(
                database.engine,
                expire_on_commit=False,
                info={DATABASE_INFO: database},
                # Closed for good: the world writes only once put on a
                # visit's session (World.on).
                close_resets_only=False,
            ) as session:
                world = build(setting_up(session), times)
                then(session)
                session.commit()
            app = new_app(url)
            # Over https, as production is, for the Secure cookies, not
            # following redirects (conftest's make_client).
            with TestClient(
                app, base_url="https://testserver", follow_redirects=False
            ) as client:
                yield Built(database, world, app, client)
        finally:
            database.dispose()
