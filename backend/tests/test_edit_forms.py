"""Edit forms: a save sent from a form shown before another save of the
same row is refused, and shows the row as it is now, rather than putting
back what the form showed (inventory-tng-8i4.123, models.Versioned)."""

from collections.abc import Callable
from decimal import Decimal
from typing import NamedTuple

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.admin.categories import CATEGORIES
from inventory_tng.admin.common import CHANGED, RULES
from inventory_tng.admin.items import ITEMS
from inventory_tng.admin.locations import LOCATIONS
from inventory_tng.admin.settings import SETTINGS
from inventory_tng.admin.volunteers import VOLUNTEERS
from inventory_tng.db import Base
from inventory_tng.models import (
    AdjustmentReason,
    Category,
    Item,
    Location,
    ScanSettings,
    SignInSettings,
    SiteSettings,
    Volunteer,
)

from helpers.rows import add, shown
from helpers.sign_in import text


class Form(NamedTuple):
    """An edit form as shown: where it is sent, what it sends but its
    version, its row, and the field its version is sent in (a settings
    form's is named for it, as three share a page)."""

    path: str
    sends: dict[str, str]
    row: Base
    field: str = "version"


def _item(session: Session) -> Form:
    radios = add(session, Category(name="Radios"))
    item = add(session, Item(name="LiteBeam", category_id=radios.id))
    form = {"name": "LiteBeam 5AC", "category_id": str(radios.id), "unit": "each"}
    return Form(f"{ITEMS}/{item.id}", form, item)


def _category(session: Session) -> Form:
    radios = add(session, Category(name="Radios"))
    return Form(f"{CATEGORIES}/{radios.id}/name", {"name": "Antennas"}, radios)


def _location(session: Session) -> Form:
    hub = add(session, Location(name="Hub", kind="store"))
    return Form(f"{LOCATIONS}/{hub.id}", {"name": "Main hub"}, hub)


def _volunteer(session: Session) -> Form:
    ada = add(session, Volunteer(name="Ada"))
    return Form(f"{VOLUNTEERS}/{ada.id}", {"name": "Ada L", "email": ""}, ada)


def _reason(session: Session) -> Form:
    reason = add(session, AdjustmentReason(name="Dropped"))
    path = f"{SETTINGS}/reasons/{reason.id}"
    return Form(path, {"name": "Dropped in the river", "action": "rename"}, reason)


def _scan(session: Session) -> Form:
    row = session.get_one(ScanSettings, 1)
    return Form(f"{SETTINGS}/scan", {"name_check_minutes": "30"}, row, "scan_version")


def _sign_in(session: Session) -> Form:
    form = {
        "totp_policy": "optional",
        "session_days": "3",
        "confirm_hours": "12",
        "confirm_idle_minutes": "30",
        "account_change_minutes": "5",
    }
    row = session.get_one(SignInSettings, 1)
    return Form(f"{SETTINGS}/sign-in", form, row, "sign_in_version")


def _site(session: Session) -> Form:
    row = session.get_one(SiteSettings, 1)
    return Form(f"{SETTINGS}/site", {"time_zone": "Europe/London"}, row, "site_version")


# Each form, and a change another administrator saves meanwhile.
FORMS: dict[str, tuple[Callable[[Session], Form], str, object]] = {
    "item": (_item, "notes", "Outdoor only"),
    "category": (_category, "name", "Wireless"),
    "location": (_location, "name", "Depot"),
    "volunteer": (_volunteer, "email", "ada@example.org"),
    "reason": (_reason, "name", "Soaked"),
    "scan": (_scan, "name_check_minutes", 20),
    "sign_in": (_sign_in, "session_days", 5),
    "site": (_site, "time_zone", "Asia/Tokyo"),
}


@pytest.mark.parametrize("name", FORMS)
def test_a_save_after_another_since_the_form_was_shown_is_refused(
    admin_client: TestClient, session: Session, name: str
) -> None:
    make, column, meanwhile = FORMS[name]
    path, sends, row, field = make(session)
    form = sends | {field: shown(row)}
    setattr(row, column, meanwhile)
    session.flush()
    refused = admin_client.post(path, data=form)
    assert refused.status_code == 409
    assert RULES[CHANGED] in text(refused)
    session.refresh(row)
    assert getattr(row, column) == meanwhile
    # Shown again as it is now, the form saves.
    form = sends | {field: shown(row)}
    assert admin_client.post(path, data=form).status_code == 303


@pytest.mark.parametrize("version", ["", "0", "x"])
def test_a_form_without_its_version_is_refused(
    admin_client: TestClient, session: Session, version: str
) -> None:
    path, sends, row, _ = _category(session)
    refused = admin_client.post(path, data=sends | {"version": version})
    assert refused.status_code == 409
    assert getattr(row, "name") == "Radios"  # noqa: B009


def test_each_change_moves_the_version_on(session: Session) -> None:
    """In the update itself (db._next_version); a row added starts at 1."""
    radios = add(session, Category(name="Radios"))
    assert radios.edit_version == 1
    radios.name = "Antennas"
    session.flush()
    assert radios.edit_version == 2


def test_a_receipt_moves_no_version(session: Session) -> None:
    """It lowers what is on order, which no edit form sets, so an
    administrator's save of the item meanwhile is not refused."""
    radios = add(session, Category(name="Radios"))
    item = add(
        session, Item(name="LiteBeam", category_id=radios.id, on_order=Decimal(5))
    )
    item.on_order = Decimal(2)
    session.flush()
    assert item.edit_version == 1
