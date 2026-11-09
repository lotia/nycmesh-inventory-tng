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
from inventory_tng.admin.devices import CODES, DEVICES
from inventory_tng.models import AdminSession, RegistrationCode

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
    assert (
        done.headers["location"]
        == f"{CODES}/{code.id}?notice=device_revoked&section=devices#devices"
    )
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
