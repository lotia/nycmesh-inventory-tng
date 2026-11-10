"""Registration codes and devices from the browser (admin/devices.py)."""

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import devices
from inventory_tng.admin.common import NAME_MAX
from inventory_tng.admin.devices import BULK_REVOKE, CODES, DEVICE_LIST, DEVICES
from inventory_tng.models import AdminSession, Device, RegistrationCode

from helpers.devices import new_device
from helpers.rows import history
from helpers.sign_in import once, text


def make_code(client: TestClient, **form: str) -> tuple[Response, str]:
    made = client.post(CODES, data=once(uses="2", days="1", label="Open day") | form)
    match = re.search(r'class="key">([a-z-]+)<', made.text)
    return made, match.group(1) if match else ""


def test_making_a_code_shows_it_once(
    admin_client: TestClient, session: Session
) -> None:
    made, phrase = make_code(admin_client)
    assert made.status_code == 200
    assert made.headers["cache-control"] == "no-store"
    page = text(made)
    assert "Shown only now." in page
    assert "<svg" in page
    assert f"/register?code={phrase}" in page
    # Ready to print: one sheet, with where to type the phrase.
    assert page.count('class="print-sheet"') == 1
    assert page.count("://testserver/register</strong>") == 1
    code = session.scalars(select(RegistrationCode)).one()
    assert (code.label, code.max_uses) == ("Open day", 2)
    assert (history(session, "registration_code", code.id)[0].actor or "").startswith(
        "admin:"
    )
    # Listed afterwards, without the phrase.
    listed = text(admin_client.get(DEVICES))
    assert "Open day" in listed
    assert phrase not in listed


def test_a_code_made_here_registers_a_phone(
    admin_client: TestClient,
    session: Session,
    make_client: Callable[..., TestClient],
) -> None:
    _, phrase = make_code(admin_client)
    phone = make_client(admin_client.app, address="192.0.2.50")
    registered = phone.post("/api/devices", json={"code": phrase})
    assert registered.status_code == 201
    code = session.scalars(select(RegistrationCode)).one()
    page = text(admin_client.get(f"{CODES}/{code.id}"))
    assert "1 of 2 used" in page
    assert "192.0.2.50" in page


def test_limits_on_a_code(admin_client: TestClient) -> None:
    for form, problem in (
        ({"uses": "0"}, "From 1 to 500 devices."),
        ({"uses": "many"}, "From 1 to 500 devices."),
        ({"days": "91"}, "From 1 to 90 days."),
        ({"label": "x" * (NAME_MAX + 1)}, f"Up to {NAME_MAX} characters."),
    ):
        refused, _ = make_code(admin_client, **form)
        assert refused.status_code == 400
        assert problem in text(refused)


def test_revoking_a_code_and_a_device(
    admin_client: TestClient, session: Session
) -> None:
    _, phrase = make_code(admin_client)
    device, _ = devices.register(session, phrase, "192.0.2.51")
    code = session.scalars(select(RegistrationCode)).one()
    done = admin_client.post(f"{CODES}/{code.id}/revoke")
    assert done.headers["location"] == f"{CODES}/{code.id}?notice=code_revoked"
    assert code.revoked_at is not None
    assert device.revoked_at is None  # Its devices stay registered.
    done = admin_client.post(f"{DEVICES}/{device.id}/revoke")
    assert done.headers["location"] == f"{DEVICES}/{device.id}?notice=device_revoked"
    session.refresh(device)
    assert device.revoked_at is not None


def test_changes_here_ask_for_a_fresh_code(
    admin_client: TestClient, session: Session
) -> None:
    for row in session.scalars(select(AdminSession)):
        row.confirmed_at = datetime.now(UTC) - timedelta(hours=2)
        row.last_sensitive_at = None
    session.flush()
    asked, _ = make_code(admin_client)
    assert asked.status_code == 403
    assert "needs a fresh" in text(asked)
    assert session.scalars(select(RegistrationCode)).first() is None


def test_unknown_codes_and_devices_are_not_found(admin_client: TestClient) -> None:
    assert admin_client.get(f"{CODES}/999999").status_code == 404
    assert admin_client.post(f"{CODES}/999999/revoke").status_code == 404
    assert admin_client.post(f"{DEVICES}/999999/revoke").status_code == 404


# Every device, filtered, and revoking many at once (ph9)


def device_at(session: Session, at: datetime) -> Device:
    """A registered device, as if registered `at`."""
    device, _ = new_device(session)
    device.created_at = at
    session.flush()
    return device


def test_devices_are_listed_by_when_they_registered(
    admin_client: TestClient, session: Session
) -> None:
    """Since is typed in the site's time zone (America/New_York here)."""
    old = device_at(session, datetime(2026, 11, 1, 12, tzinfo=UTC))
    new = device_at(session, datetime(2026, 11, 5, 12, tzinfo=UTC))
    listed = text(admin_client.get(DEVICE_LIST, params={"since": "2026-11-03T00:00"}))
    assert f"Device {new.id}" in listed
    assert f"Device {old.id}" not in listed
    by_code = admin_client.get(
        DEVICE_LIST, params={"code": str(old.registration_code_id)}
    )
    assert f"Device {old.id}" in text(by_code)
    assert f"Device {new.id}" not in text(by_code)
    bad = admin_client.get(DEVICE_LIST, params={"since": "soon", "code": "x"})
    assert bad.status_code == 400
    assert "A date and time" in text(bad)
    assert "A code&#39;s number" in bad.text


def test_revoking_the_ticked_ones_asks_first(
    admin_client: TestClient, session: Session
) -> None:
    """With how many, then only those ticked that the filter still matches."""
    a = device_at(session, datetime(2026, 11, 5, 12, tzinfo=UTC))
    b = device_at(session, datetime(2026, 11, 5, 13, tzinfo=UTC))
    kept = device_at(session, datetime(2026, 11, 5, 14, tzinfo=UTC))
    old = device_at(session, datetime(2026, 11, 1, 12, tzinfo=UTC))
    form = once(since="2026-11-03T00:00", code="", state="current")
    ticked = form | {"device": [str(a.id), str(b.id), str(old.id)]}
    asked = admin_client.post(BULK_REVOKE, data=ticked)
    assert "Revoke 2 devices?" in text(asked)
    assert a.revoked_at is None
    done = admin_client.post(BULK_REVOKE, data=ticked | {"sure": "yes"})
    assert done.status_code == 303
    assert "notice=devices_revoked" in done.headers["location"]
    assert a.revoked_at is not None
    assert b.revoked_at is not None
    assert kept.revoked_at is None
    # Out of the filter: not revoked, though ticked.
    assert old.revoked_at is None
    # Sent again: those ticked are revoked already, so nothing is done again.
    again = admin_client.post(BULK_REVOKE, data=ticked | {"sure": "yes"})
    assert again.headers["location"].startswith(DEVICE_LIST)
    nothing = admin_client.post(BULK_REVOKE, data=form)
    assert nothing.headers["location"].startswith(DEVICE_LIST)


def test_one_device_is_asked_about_in_the_singular(
    admin_client: TestClient, session: Session
) -> None:
    a = device_at(session, datetime(2026, 11, 5, 12, tzinfo=UTC))
    asked = admin_client.post(BULK_REVOKE, data=once(device=str(a.id)))
    assert "Revoke 1 device?" in text(asked)


def test_a_date_past_what_a_time_holds_is_refused(admin_client: TestClient) -> None:
    refused = admin_client.get(DEVICE_LIST, params={"since": "9999-12-31T23:59"})
    assert refused.status_code == 400


def test_the_devices_page_links_to_every_device(admin_client: TestClient) -> None:
    assert f'href="{DEVICE_LIST}"' in admin_client.get(DEVICES).text


def test_revoked_and_all_devices_are_listed_too(
    admin_client: TestClient, session: Session
) -> None:
    current = device_at(session, datetime(2026, 11, 5, 12, tzinfo=UTC))
    gone = device_at(session, datetime(2026, 11, 5, 13, tzinfo=UTC))
    devices.revoke_device(session, gone.id)
    # Revoking it again finds no current device.
    assert not devices.revoke_device(session, gone.id)
    revoked = text(admin_client.get(DEVICE_LIST, params={"state": "revoked"}))
    assert f"Device {gone.id}" in revoked
    assert f"Device {current.id}" not in revoked
    every = text(admin_client.get(DEVICE_LIST, params={"state": "all"}))
    assert f"Device {gone.id}" in every
    assert f"Device {current.id}" in every
    # Nothing current among those ticked: nothing revoked, nothing counted.
    none = devices.revoke_devices(
        session, devices.DeviceFilter(), [gone.id], datetime.now(UTC)
    )
    assert none == 0


def test_a_bulk_revoke_is_sent_once(admin_client: TestClient, session: Session) -> None:
    """Its key spent, the same form names a device still current: refused."""
    a = device_at(session, datetime(2026, 11, 5, 12, tzinfo=UTC))
    b = device_at(session, datetime(2026, 11, 5, 13, tzinfo=UTC))
    form = once(sure="yes")
    assert (
        admin_client.post(BULK_REVOKE, data=form | {"device": str(a.id)}).status_code
        == 303
    )
    again = admin_client.post(BULK_REVOKE, data=form | {"device": str(b.id)})
    assert again.status_code == 409
    assert b.revoked_at is None


def test_each_device_has_a_page_reached_from_both_lists(
    admin_client: TestClient, session: Session
) -> None:
    device, _ = new_device(session)
    link = f'<a href="{DEVICES}/{device.id}">{device.name}</a>'
    assert link in admin_client.get(DEVICE_LIST).text
    assert link in admin_client.get(f"{CODES}/{device.registration_code_id}").text
    page = text(admin_client.get(f"{DEVICES}/{device.id}"))
    assert f'href="{CODES}/{device.registration_code_id}"' in page
    assert "Revoke this device" in page
    assert admin_client.get(f"{DEVICES}/999999").status_code == 404


def test_naming_a_device(admin_client: TestClient, session: Session) -> None:
    device, _ = new_device(session)
    path = f"{DEVICES}/{device.id}"
    version = str(device.edit_version)
    done = admin_client.post(path, data={"label": " Store tablet ", "version": version})
    assert done.headers["location"] == f"{path}?notice=saved"
    session.refresh(device)
    assert device.label == "Store tablet"
    # A form shown before that save is refused, not undoing it.
    stale = admin_client.post(path, data={"label": "Old", "version": version})
    assert stale.status_code == 409
    long = admin_client.post(
        path, data={"label": "x" * (NAME_MAX + 1), "version": str(device.edit_version)}
    )
    assert long.status_code == 400
    # Blank, it goes by its number again.
    admin_client.post(path, data={"label": "", "version": str(device.edit_version)})
    session.refresh(device)
    assert device.label is None
    assert (
        admin_client.post(f"{DEVICES}/999999", data={"label": "x"}).status_code == 404
    )


def test_a_device_is_revoked_after_a_confirmation(
    admin_client: TestClient, session: Session
) -> None:
    device, _ = new_device(session)
    path = f"{DEVICES}/{device.id}"
    asked = text(admin_client.get(f"{path}/revoke"))
    assert '<button class="danger">Revoke</button>' in asked
    assert f'<a href="{path}">Keep it</a>' in asked
    assert device.revoked_at is None
    admin_client.post(f"{path}/revoke")
    session.refresh(device)
    assert device.revoked_at is not None
    # Revoked: read only, and not asked about again.
    page = text(admin_client.get(path))
    assert '<span class="tag">Revoked</span>' in page
    assert "Revoke this device" not in page
    assert '<button class="primary">Save</button>' not in page
    assert admin_client.get(f"{path}/revoke").status_code == 404
    refused = admin_client.post(
        path, data={"label": "Lost", "version": str(device.edit_version)}
    )
    assert refused.status_code == 409
