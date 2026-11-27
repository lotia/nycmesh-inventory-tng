"""A box refused for what was typed is marked invalid on every
administrator page, and only in the form sent back: the same box in
another form of the page is not (decision 0019, _described_by.html;
inventory-tng-gjuq).

Each refusal below is one form of a page sent wrong. Its page is read
the same way for each: every box a problem is said for, the boxes inside
the problem's label and those its id describes, is marked aria-invalid
when it is in the form sent, and no other box is. A refusal on a page
whose problems are page-wide, such as a retire refused with its problem
beside the name, marks nothing."""

from collections.abc import Callable
from typing import NamedTuple
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.admins import paths
from inventory_tng.catalogue.paths import CATEGORIES, ITEMS
from inventory_tng.devices.paths import CODES, DEVICE_LIST, DEVICES
from inventory_tng.models.admins import Authenticator
from inventory_tng.models.catalogue import Category, Item, ItemPackage
from inventory_tng.models.places import Location
from inventory_tng.models.site_settings import ScanSettings, SignInSettings
from inventory_tng.models.stock import AdjustmentReason
from inventory_tng.models.volunteers import Volunteer
from inventory_tng.places.paths import LOCATIONS
from inventory_tng.site_settings.paths import SETTINGS
from inventory_tng.volunteers.paths import VOLUNTEERS
from inventory_tng.web.forms import NAME_MAX

from helpers.devices import new_device
from helpers.items import shown_item_form
from helpers.markup import Element, read
from helpers.rows import add, shown
from helpers.sign_in import (
    PASSWORD,
    new_admin,
    reset_link,
    sign_in_with,
    with_code,
)

TOKEN = "t" * 40
TOO_LONG = "x" * (NAME_MAX + 1)
BOXES = {"input", "select", "textarea"}

MakeClient = Callable[..., TestClient]


class Sent(NamedTuple):
    """A form sent wrong: by whom, where, what, and the names of the boxes
    its page then marks invalid, in the page's order."""

    client: TestClient
    path: str
    data: dict[str, str]
    marked: tuple[str, ...]
    method: str = "post"


def _ada(session: Session, make_client: MakeClient) -> TestClient:
    """A client signed in as Ada, with the confirmation window open."""
    client = make_client()
    _, key = new_admin(session)
    assert sign_in_with(client, key=key).status_code == 303
    return client


def _volunteer_added(session: Session, make_client: MakeClient) -> Sent:
    data = {"name": "", "email": "not an address"}
    return Sent(_ada(session, make_client), VOLUNTEERS, data, ("name", "email"))


def _volunteer_saved(session: Session, make_client: MakeClient) -> Sent:
    ada = add(session, Volunteer(name="Ada"))
    data = {"name": "", "email": "", "version": shown(ada)}
    path = f"{VOLUNTEERS}/{ada.id}"
    return Sent(_ada(session, make_client), path, data, ("name",))


def _location_added(session: Session, make_client: MakeClient) -> Sent:
    data = {"name": "", "kind": "nothing", "parent_id": ""}
    return Sent(_ada(session, make_client), LOCATIONS, data, ("name", "kind"))


def _area_added(session: Session, make_client: MakeClient) -> Sent:
    data = {"name": "Shelf", "kind": "area", "parent_id": ""}
    return Sent(_ada(session, make_client), LOCATIONS, data, ("parent_id",))


def _location_saved(session: Session, make_client: MakeClient) -> Sent:
    hub = add(session, Location(name="Hub", kind="store"))
    data = {"name": "", "time_zone": "", "version": shown(hub)}
    path = f"{LOCATIONS}/{hub.id}"
    return Sent(_ada(session, make_client), path, data, ("name",))


def _location_retired(session: Session, make_client: MakeClient) -> Sent:
    """Refused for its current area, said beside its name."""
    hub = add(session, Location(name="Hub", kind="store"))
    add(session, Location(name="Shelf", kind="area", parent_id=hub.id))
    path = f"{LOCATIONS}/{hub.id}/retire"
    return Sent(_ada(session, make_client), path, {}, ())


def _codes_made(session: Session, make_client: MakeClient) -> Sent:
    data = {"label": TOO_LONG, "uses": "0", "days": "0", "count": "0"}
    marked = ("label", "uses", "days", "count")
    return Sent(_ada(session, make_client), CODES, data, marked)


def _devices_listed(session: Session, make_client: MakeClient) -> Sent:
    data = {"since": "soon", "until": "later", "code": "first"}
    marked = ("since", "until", "code")
    return Sent(_ada(session, make_client), DEVICE_LIST, data, marked, "get")


def _device_saved(session: Session, make_client: MakeClient) -> Sent:
    client = _ada(session, make_client)
    device, _ = new_device(session)
    data = {"label": TOO_LONG, "version": shown(device)}
    return Sent(client, f"{DEVICES}/{device.id}", data, ("label",))


def _item_added(session: Session, make_client: MakeClient) -> Sent:
    data = {"name": "", "category_id": "", "unit": "each"}
    return Sent(_ada(session, make_client), ITEMS, data, ("name", "category_id"))


def _item(session: Session) -> Item:
    radios = add(session, Category(name="Radios"))
    item = add(session, Item(name="LiteBeam", category_id=radios.id))
    add(session, ItemPackage(item_id=item.id, name="Box", size=1))
    return item


def _item_saved(session: Session, make_client: MakeClient) -> Sent:
    item = _item(session)
    data = shown_item_form(item, name="")
    return Sent(_ada(session, make_client), f"{ITEMS}/{item.id}", data, ("name",))


def _alias_added(session: Session, make_client: MakeClient) -> Sent:
    item = _item(session)
    path = f"{ITEMS}/{item.id}/aliases"
    return Sent(_ada(session, make_client), path, {"alias_name": ""}, ("alias_name",))


def _package_added(session: Session, make_client: MakeClient) -> Sent:
    """Its boxes marked in Add a package, not in the package's own form."""
    item = _item(session)
    path = f"{ITEMS}/{item.id}/packages"
    data = {"package_name": "", "size": ""}
    marked = ("package_name", "size")
    return Sent(_ada(session, make_client), path, data, marked)


def _category_added(session: Session, make_client: MakeClient) -> Sent:
    return Sent(_ada(session, make_client), CATEGORIES, {"name": ""}, ("name",))


def _category_renamed(session: Session, make_client: MakeClient) -> Sent:
    radios = add(session, Category(name="Radios"))
    path = f"{CATEGORIES}/{radios.id}/name"
    data = {"name": "", "version": shown(radios)}
    return Sent(_ada(session, make_client), path, data, ("name",))


def _category_retired(session: Session, make_client: MakeClient) -> Sent:
    """Refused for its item, said beside its name."""
    item = _item(session)
    path = f"{CATEGORIES}/{item.category_id}/retire"
    return Sent(_ada(session, make_client), path, {}, ())


def _set_up(_: Session, make_client: MakeClient) -> Sent:
    data = {
        "token": "x" * 40,
        "username": "ada",
        "password": PASSWORD,
        "password_again": PASSWORD,
        "authenticator_name": "Phone",
    }
    client = make_client(setup_token=TOKEN)
    return Sent(client, paths.SETUP, data, ("token",))


def _recovered(session: Session, make_client: MakeClient) -> Sent:
    new_admin(session)
    client = make_client(recovery_token=TOKEN)
    data = {"token": "x" * 40, "username": "ada"}
    return Sent(client, paths.RECOVER, data, ("token",))


def _reset(session: Session, make_client: MakeClient) -> Sent:
    ada, _ = new_admin(session)
    data = {"token": reset_link(session, ada), "password": "short"}
    data["password_again"] = "short"
    return Sent(make_client(), paths.RESET, data, ("password",))


def _invited(session: Session, make_client: MakeClient) -> Sent:
    client = _ada(session, make_client)
    data = with_code(session, username="", email="not an address")
    return Sent(client, paths.ADMINS, data, ("username", "email"))


def _authenticator_added(session: Session, make_client: MakeClient) -> Sent:
    data = {"authenticator_name": "", "code": "000000"}
    marked = ("authenticator_name", "code")
    return Sent(_ada(session, make_client), paths.NEW_AUTHENTICATOR, data, marked)


def _email_changed(session: Session, make_client: MakeClient) -> Sent:
    path = f"{paths.ACCOUNT}/email"
    return Sent(_ada(session, make_client), path, {"email": "nope"}, ("email",))


def _password_changed(session: Session, make_client: MakeClient) -> Sent:
    path = f"{paths.ACCOUNT}/password"
    data = {"password": "short", "password_again": "short"}
    return Sent(_ada(session, make_client), path, data, ("password",))


def _authenticator_renamed(session: Session, make_client: MakeClient) -> Sent:
    client = _ada(session, make_client)
    (authenticator,) = session.scalars(select(Authenticator)).all()
    path = f"{paths.ACCOUNT}/authenticators/{authenticator.id}/name"
    data = {"authenticator_name": ""}
    return Sent(client, path, data, ("authenticator_name",))


def _scan_settings(session: Session, make_client: MakeClient) -> Sent:
    row = session.get_one(ScanSettings, 1)
    data = {"name_check_minutes": "-1", "scan_version": shown(row)}
    path = f"{SETTINGS}/scan"
    return Sent(_ada(session, make_client), path, data, ("name_check_minutes",))


def _sign_in_settings(session: Session, make_client: MakeClient) -> Sent:
    row = session.get_one(SignInSettings, 1)
    data = {
        "totp_policy": "optional",
        "session_days": "0",
        "confirm_hours": "12",
        "confirm_idle_minutes": "30",
        "account_change_minutes": "5",
        "sign_in_version": shown(row),
    }
    path = f"{SETTINGS}/sign-in"
    return Sent(_ada(session, make_client), path, data, ("session_days",))


def _reason_added(session: Session, make_client: MakeClient) -> Sent:
    """Its box marked, not the box of the reason beside it."""
    add(session, AdjustmentReason(name="Dropped"))
    path = f"{SETTINGS}/reasons"
    return Sent(_ada(session, make_client), path, {"name": ""}, ("name",))


def _reason_renamed(session: Session, make_client: MakeClient) -> Sent:
    """Its box marked, not New reason's."""
    reason = add(session, AdjustmentReason(name="Dropped"))
    path = f"{SETTINGS}/reasons/{reason.id}"
    data = {"name": TOO_LONG, "action": "rename", "version": shown(reason)}
    return Sent(_ada(session, make_client), path, data, ("name",))


REFUSALS: dict[str, Callable[[Session, MakeClient], Sent]] = {
    "volunteer added": _volunteer_added,
    "volunteer saved": _volunteer_saved,
    "location added": _location_added,
    "area added": _area_added,
    "location saved": _location_saved,
    "location retired": _location_retired,
    "codes made": _codes_made,
    "devices listed": _devices_listed,
    "device saved": _device_saved,
    "item added": _item_added,
    "item saved": _item_saved,
    "alias added": _alias_added,
    "package added": _package_added,
    "category added": _category_added,
    "category renamed": _category_renamed,
    "category retired": _category_retired,
    "set up": _set_up,
    "recovered": _recovered,
    "reset": _reset,
    "invited": _invited,
    "authenticator added": _authenticator_added,
    "email changed": _email_changed,
    "password changed": _password_changed,
    "authenticator renamed": _authenticator_renamed,
    "scan settings": _scan_settings,
    "sign-in settings": _sign_in_settings,
    "reason added": _reason_added,
    "reason renamed": _reason_renamed,
}


def _boxes(scope: Element) -> list[Element]:
    """The boxes in `scope` a person fills in, in the page's order."""
    return [
        box
        for box in scope.all("*")
        if box.name in BOXES and box.attrs.get("type") != "hidden"
    ]


def _within(box: Element, forms: list[Element]) -> bool:
    return any(parent is form for parent in box.parents for form in forms)


def refused_boxes(page: Element) -> list[Element]:
    """Each box a problem on the page is said for: those inside the label
    the problem sits in, and those its id describes."""
    found: dict[int, Element] = {}
    every = _boxes(page)
    for said in page.all("span", class_="problem"):
        label = said.find_parent("label")
        inside = _boxes(label) if isinstance(label, Element) else []
        named = said.attrs.get("id")
        describing = [
            box
            for box in every
            if named in box.attrs.get("aria-describedby", "").split()
        ]
        found |= {id(box): box for box in inside + describing}
    return [box for box in every if id(box) in found]


@pytest.mark.parametrize("refusal", REFUSALS)
def test_a_refused_box_is_marked_invalid_only_in_the_form_sent(
    session: Session, make_client: MakeClient, refusal: str
) -> None:
    sent = REFUSALS[refusal](session, make_client)
    if sent.method == "get":
        response = sent.client.get(sent.path, params=sent.data)
    else:
        response = sent.client.post(sent.path, data=sent.data)
    assert response.status_code == 400, response.text
    page = read(response.text)
    forms = [
        form
        for form in page.all("form", method=sent.method)
        if urlsplit(form.attrs.get("action", "")).path == sent.path
    ]
    assert forms, f"no {sent.method} form to {sent.path}"
    marked = [box for box in _boxes(page) if box.attrs.get("aria-invalid") == "true"]
    # Marked, the boxes of the form sent that a problem is said for...
    assert [box.attrs.get("name") for box in marked] == list(sent.marked)
    refused = [box for box in refused_boxes(page) if _within(box, forms)]
    assert [id(box) for box in marked] == [id(box) for box in refused]
    # ...and no box of another form.
    assert all(_within(box, forms) for box in marked)
