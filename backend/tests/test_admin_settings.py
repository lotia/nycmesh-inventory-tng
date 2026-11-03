"""The settings page: how the scan page behaves (admin/settings.py)."""

import html
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from conftest import SentMail
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_models import history
from test_sign_in import code, new_admin, sign_in_with

from inventory_tng.admin.settings import SETTINGS
from inventory_tng.models import (
    AdminSession,
    ScanSettings,
    SignInSettings,
    SiteSettings,
)


def test_shows_and_changes_the_name_check(
    admin_client: TestClient, session: Session
) -> None:
    assert 'value="15"' in admin_client.get(SETTINGS).text
    done = admin_client.post(f"{SETTINGS}/scan", data={"name_check_minutes": "30"})
    assert done.headers["location"] == f"{SETTINGS}?notice=settings_saved"
    assert session.get_one(ScanSettings, 1).name_check_minutes == 30
    change = history(session, "scan_settings")[-1]
    assert (change.actor or "").startswith("admin:")
    # The scan page reads it with the catalogue.
    assert "within ten minutes" in html.unescape(
        admin_client.get(done.headers["location"]).text
    )


@pytest.mark.parametrize("typed", ["", "-1", "soon", "1441"])
def test_refuses_what_is_not_minutes(admin_client: TestClient, typed: str) -> None:
    refused = admin_client.post(f"{SETTINGS}/scan", data={"name_check_minutes": typed})
    assert refused.status_code == 400
    assert "A whole number of minutes" in refused.text


def test_zero_never_asks(admin_client: TestClient, session: Session) -> None:
    admin_client.post(f"{SETTINGS}/scan", data={"name_check_minutes": "0"})
    assert session.get_one(ScanSettings, 1).name_check_minutes == 0


# Signing in and the time zone


SIGN_IN_FORM = {
    "totp_policy": "optional",
    "session_days": "3",
    "confirm_hours": "12",
    "confirm_idle_minutes": "30",
}


def test_sign_in_settings_change_and_everyone_is_told(
    make_client: Callable[..., TestClient], session: Session, sent_mail: SentMail
) -> None:
    client = make_client(mailer=sent_mail)
    _, key = new_admin(session, email="ada@example.org")
    new_admin(session, "ben", email="ben@example.org")
    new_admin(session, "carol")  # No address: not told.
    sign_in_with(client, code=code(key))
    done = client.post(f"{SETTINGS}/sign-in", data=SIGN_IN_FORM)
    assert done.headers["location"] == f"{SETTINGS}?notice=saved"
    settings = session.get_one(SignInSettings, 1)
    assert (
        settings.totp_policy,
        settings.session_days,
        settings.confirm_hours,
        settings.confirm_idle_minutes,
    ) == ("optional", 3, 12, 30)
    assert sorted(m.to for m in sent_mail) == ["ada@example.org", "ben@example.org"]
    assert "TOTP is now optional" in sent_mail[0].body
    assert (history(session, "sign_in_settings")[-1].actor or "").startswith("admin:")


@pytest.mark.parametrize(
    ("field", "typed"),
    [
        ("totp_policy", "sometimes"),
        ("session_days", "0"),
        ("session_days", "401"),
        ("confirm_hours", "lots"),
        ("confirm_idle_minutes", ""),
        ("session_days", "²"),
        ("confirm_hours", "9" * 5000),
    ],
)
def test_sign_in_settings_refuse_what_cannot_be(
    admin_client: TestClient, session: Session, field: str, typed: str
) -> None:
    refused = admin_client.post(
        f"{SETTINGS}/sign-in", data=SIGN_IN_FORM | {field: typed}
    )
    assert refused.status_code == 400
    assert session.get_one(SignInSettings, 1).totp_policy == "required"


def test_sign_in_settings_ask_for_a_fresh_code(
    admin_client: TestClient, session: Session
) -> None:
    for row in session.scalars(select(AdminSession)):
        row.confirmed_at = datetime.now(UTC) - timedelta(hours=2)
        row.last_sensitive_at = None
    session.flush()
    asked = admin_client.post(f"{SETTINGS}/sign-in", data=SIGN_IN_FORM)
    assert asked.status_code == 403
    assert session.get_one(SignInSettings, 1).totp_policy == "required"


def test_the_time_zone(admin_client: TestClient, session: Session) -> None:
    page = admin_client.get(SETTINGS).text
    assert "<option selected>America/New_York</option>" in page
    done = admin_client.post(f"{SETTINGS}/site", data={"time_zone": "Europe/London"})
    assert done.headers["location"] == f"{SETTINGS}?notice=site_saved"
    assert session.get_one(SiteSettings, 1).time_zone == "Europe/London"
    refused = admin_client.post(f"{SETTINGS}/site", data={"time_zone": "Mars/Olympus"})
    assert refused.status_code == 400
    assert "Pick a zone from the list." in refused.text


def test_saving_the_same_sign_in_settings_tells_nobody(
    make_client: Callable[..., TestClient], session: Session, sent_mail: SentMail
) -> None:
    client = make_client(mailer=sent_mail)
    _, key = new_admin(session, email="ada@example.org")
    sign_in_with(client, code=code(key))
    same = SIGN_IN_FORM | {"totp_policy": "required", "session_days": "7"}
    same |= {"confirm_hours": "24", "confirm_idle_minutes": "60"}
    assert client.post(f"{SETTINGS}/sign-in", data=same).status_code == 303
    assert list(sent_mail) == []


def test_a_zone_set_elsewhere_still_shows(
    admin_client: TestClient, session: Session
) -> None:
    session.get_one(SiteSettings, 1).time_zone = "UTC"
    session.flush()
    assert "<option selected>UTC</option>" in admin_client.get(SETTINGS).text
