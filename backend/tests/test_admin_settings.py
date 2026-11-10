"""The settings page: how the scan page behaves (admin/settings.py)."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng import activity
from inventory_tng.admin.settings import SETTINGS
from inventory_tng.models import (
    AdminSession,
    ScanSettings,
    SignInSettings,
    SiteSettings,
)

from helpers.mail import SentMail
from helpers.rows import history, shown
from helpers.sign_in import code, new_admin, sign_in_with, text


def test_shows_and_changes_the_name_check(
    admin_client: TestClient, session: Session
) -> None:
    assert 'value="15"' in admin_client.get(SETTINGS).text
    scan = session.get_one(ScanSettings, 1)
    form = {"name_check_minutes": "30", "scan_version": shown(scan)}
    done = admin_client.post(f"{SETTINGS}/scan", data=form)
    assert (
        done.headers["location"]
        == f"{SETTINGS}?notice=settings_saved&section=scan-page#scan-page"
    )
    assert session.get_one(ScanSettings, 1).name_check_minutes == 30
    change = history(session, "scan_settings")[-1]
    assert (change.actor or "").startswith("admin:")
    # The scan page reads it with the catalogue.
    assert "within ten minutes" in text(admin_client.get(done.headers["location"]))


@pytest.mark.parametrize("typed", ["", "-1", "soon", "1441"])
def test_refuses_what_is_not_minutes(admin_client: TestClient, typed: str) -> None:
    refused = admin_client.post(f"{SETTINGS}/scan", data={"name_check_minutes": typed})
    assert refused.status_code == 400
    assert "A whole number of minutes" in refused.text


def test_zero_never_asks(admin_client: TestClient, session: Session) -> None:
    form = {
        "name_check_minutes": "0",
        "scan_version": shown(session.get_one(ScanSettings, 1)),
    }
    admin_client.post(f"{SETTINGS}/scan", data=form)
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
    version = shown(session.get_one(SignInSettings, 1))
    done = client.post(
        f"{SETTINGS}/sign-in", data=SIGN_IN_FORM | {"sign_in_version": version}
    )
    assert (
        done.headers["location"] == f"{SETTINGS}?notice=saved&section=sign-in#sign-in"
    )
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
    # And in the feed, for carol, without an address.
    assert activity.recent(session)[0].words == "ada changed the sign-in settings"


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
    site = session.get_one(SiteSettings, 1)
    form = {"time_zone": "Europe/London", "site_version": shown(site)}
    done = admin_client.post(f"{SETTINGS}/site", data=form)
    assert (
        done.headers["location"]
        == f"{SETTINGS}?notice=site_saved&section=time-zone#time-zone"
    )
    assert session.get_one(SiteSettings, 1).time_zone == "Europe/London"
    with capture_logs() as logs:
        again = admin_client.post(
            f"{SETTINGS}/site", data=form | {"site_version": shown(site)}
        )
    assert (
        again.headers["location"]
        == f"{SETTINGS}?notice=site_saved&section=time-zone#time-zone"
    )
    assert not any(line["event"] == "site time zone changed" for line in logs)
    form = {"time_zone": "Mars/Olympus", "site_version": shown(site)}
    refused = admin_client.post(f"{SETTINGS}/site", data=form)
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
    same |= {"sign_in_version": shown(session.get_one(SignInSettings, 1))}
    assert client.post(f"{SETTINGS}/sign-in", data=same).status_code == 303
    assert list(sent_mail) == []


def test_a_zone_set_elsewhere_still_shows(
    admin_client: TestClient, session: Session
) -> None:
    session.get_one(SiteSettings, 1).time_zone = "UTC"
    session.flush()
    assert "<option selected>UTC</option>" in admin_client.get(SETTINGS).text
