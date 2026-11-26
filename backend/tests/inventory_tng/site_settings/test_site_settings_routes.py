"""The settings page: how the scan page behaves
(site_settings/site_settings_routes.py)."""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng.admins import activity, mail_messages
from inventory_tng.models.site_settings import (
    NAME_CHECK_MINUTES,
    ScanSettings,
    SignInSettings,
    SiteSettings,
)
from inventory_tng.models.stock import AdjustmentReason
from inventory_tng.site_settings.forms import REASON_TAKEN, whole_minutes
from inventory_tng.site_settings.paths import SETTINGS
from inventory_tng.web.forms import PICK_A_ZONE

from helpers.mail import SentMail
from helpers.markup import (
    Element,
    box_values,
    described_and_invalid,
    hidden_fields,
    options,
    read,
    text,
)
from helpers.rows import add, history, shown
from helpers.sign_in import confirmed_long_ago, new_admin, sign_in_with, with_code


def test_shows_and_changes_the_name_check(
    admin_client: TestClient, session: Session
) -> None:
    page = admin_client.get(SETTINGS).text
    assert box_values(page, "name_check_minutes") == ["15"]
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
def test_refuses_what_is_not_minutes(
    admin_client: TestClient, session: Session, typed: str
) -> None:
    form = {
        "name_check_minutes": typed,
        "scan_version": shown(session.get_one(ScanSettings, 1)),
    }
    refused = admin_client.post(f"{SETTINGS}/scan", data=form)
    assert refused.status_code == 400
    assert whole_minutes(*NAME_CHECK_MINUTES) in refused.text


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
    "account_change_minutes": "5",
}


def test_sign_in_settings_change_and_everyone_is_told(
    make_client: Callable[..., TestClient], session: Session, sent_mail: SentMail
) -> None:
    client = make_client(mailer=sent_mail)
    _, key = new_admin(session, email="ada@example.org")
    new_admin(session, "ben", email="ben@example.org")
    new_admin(session, "carol")  # No address: not told.
    sign_in_with(client, key=key)
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
        settings.account_change_minutes,
    ) == ("optional", 3, 12, 30, 5)
    assert sorted(m.to for m in sent_mail) == ["ada@example.org", "ben@example.org"]
    assert sent_mail[0] == mail_messages.sign_in_settings_changed(settings)(
        sent_mail[0].to, sent_mail[0].admin_id
    )
    assert (history(session, "sign_in_settings")[-1].actor or "").startswith("admin:")
    # And in the feed, for carol, without an address.
    assert activity.recent(session)[0].words == activity.WORDS[
        "sign_in_settings_changed"
    ]("ada", "")


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
    version = shown(session.get_one(SignInSettings, 1))
    form = SIGN_IN_FORM | {field: typed, "sign_in_version": version}
    refused = admin_client.post(f"{SETTINGS}/sign-in", data=form)
    assert refused.status_code == 400
    assert session.get_one(SignInSettings, 1).totp_policy == "required"


def test_refused_totp_policy_radios_are_tied_to_their_problem(
    admin_client: TestClient, session: Session
) -> None:
    """Its problem follows the radios' labels, so each radio is described
    by it and marked invalid; unrefused, neither (Ali, 2026-11-26,
    inventory-tng-l361)."""
    radios = read(admin_client.get(SETTINGS).text).all("input", name="totp_policy")
    assert {described_and_invalid(each) for each in radios} == {(None, None)}
    version = shown(session.get_one(SignInSettings, 1))
    form = SIGN_IN_FORM | {"totp_policy": "sometimes", "sign_in_version": version}
    refused = read(admin_client.post(f"{SETTINGS}/sign-in", data=form).text)
    radios = refused.all("input", name="totp_policy")
    tied = {described_and_invalid(each) for each in radios}
    assert tied == {("totp_policy-problem", "true")}
    assert refused.one("*", id="totp_policy-problem").text


def test_a_refused_reason_rename_is_tied_to_its_problem(
    admin_client: TestClient, session: Session
) -> None:
    """Its problem follows its form, so only that reason's box is described
    by it and marked invalid (Ali, 2026-11-26, inventory-tng-l361)."""
    given = add(session, AdjustmentReason(name="Given away"))
    other = add(session, AdjustmentReason(name="Donated"))
    form = {"name": "Donated", "version": shown(given), "action": "rename"}
    refused = admin_client.post(f"{SETTINGS}/reasons/{given.id}", data=form)
    assert refused.status_code == 400
    page = read(refused.text)

    def box(reason: AdjustmentReason) -> Element:
        return page.one("form", action=f"{SETTINGS}/reasons/{reason.id}").one(
            "input", name="name"
        )

    problem = f"reason_{given.id}-problem"
    assert described_and_invalid(box(given)) == (problem, "true")
    assert page.one("*", id=problem).text == str(REASON_TAKEN)
    assert described_and_invalid(box(other)) == (None, None)


def test_a_refused_reason_rename_keeps_the_name_typed(
    admin_client: TestClient, session: Session
) -> None:
    """Refused for its own problem, its box holds what was typed, and
    another reason's its stored name; refused as changed since shown, the
    page is shown as it is now, its box holding the name stored
    (inventory-tng-jlsi, inventory-tng-ryjv)."""
    given = add(session, AdjustmentReason(name="Given away"))
    other = add(session, AdjustmentReason(name="Lent out"))
    add(session, AdjustmentReason(name="Donated"))
    form = {"name": "Donated", "version": shown(given), "action": "rename"}
    path = f"{SETTINGS}/reasons/{given.id}"

    def names(page: str) -> list[list[str | None]]:
        return [
            box_values(
                read(page).one("form", action=f"{SETTINGS}/reasons/{each}"), "name"
            )
            for each in (given.id, other.id)
        ]

    refused = admin_client.post(path, data=form)
    assert refused.status_code == 400
    assert names(refused.text) == [["Donated"], ["Lent out"]]
    given.name = "Gifted"
    session.flush()
    stale = admin_client.post(path, data=form | {"name": "Dropped"})
    assert stale.status_code == 409
    assert names(stale.text) == [["Gifted"], ["Lent out"]]


def test_sign_in_settings_ask_for_a_fresh_code(
    admin_client: TestClient, session: Session
) -> None:
    confirmed_long_ago(session)
    asked = admin_client.post(f"{SETTINGS}/sign-in", data=SIGN_IN_FORM)
    assert asked.status_code == 403
    assert session.get_one(SignInSettings, 1).totp_policy == "required"


def test_sign_in_settings_go_on_through_the_confirm_page(
    admin_client: TestClient, session: Session
) -> None:
    """Only a password typed is asked for again: the settings' own fields
    are carried, though the log masker masks their values (inventory-tng-pem)."""
    confirmed_long_ago(session)
    version = shown(session.get_one(SignInSettings, 1))
    form = SIGN_IN_FORM | {"sign_in_version": version}
    asked = admin_client.post(f"{SETTINGS}/sign-in", data=form)
    assert asked.status_code == 403
    assert not read(asked.text).all("input", autocomplete="new-password")  # None.
    carried = dict(hidden_fields(asked.text))
    assert carried == form
    done = admin_client.post(f"{SETTINGS}/sign-in", data=with_code(session, **carried))
    assert (
        done.headers["location"] == f"{SETTINGS}?notice=saved&section=sign-in#sign-in"
    )
    settings = session.get_one(SignInSettings, 1)
    assert (settings.totp_policy, settings.session_days) == ("optional", 3)


def test_the_time_zone(admin_client: TestClient, session: Session) -> None:
    page = admin_client.get(SETTINGS).text
    assert (None, "America/New_York", True) in options(page)
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
    assert str(PICK_A_ZONE) in refused.text


def test_saving_the_same_sign_in_settings_tells_nobody(
    make_client: Callable[..., TestClient], session: Session, sent_mail: SentMail
) -> None:
    client = make_client(mailer=sent_mail)
    _, key = new_admin(session, email="ada@example.org")
    sign_in_with(client, key=key)
    same = SIGN_IN_FORM | {"totp_policy": "required", "session_days": "7"}
    same |= {"confirm_hours": "24", "confirm_idle_minutes": "60"}
    same |= {"account_change_minutes": "30"}
    same |= {"sign_in_version": shown(session.get_one(SignInSettings, 1))}
    assert client.post(f"{SETTINGS}/sign-in", data=same).status_code == 303
    assert list(sent_mail) == []


def test_a_zone_set_elsewhere_still_shows(
    admin_client: TestClient, session: Session
) -> None:
    session.get_one(SiteSettings, 1).time_zone = "UTC"
    session.flush()
    assert (None, "UTC", True) in options(admin_client.get(SETTINGS).text)
