"""A form that makes something shown only once, sent again, as Back then
Forward offers, makes nothing new (web/single_use_forms.py,
inventory-tng-8i4.74)."""

import re
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng.admins import accounts, guards
from inventory_tng.admins.paths import ACCOUNT, ADMINS
from inventory_tng.core.local_env import CLONE
from inventory_tng.core.sent_forms import form_key, spend
from inventory_tng.devices.paths import CODES, DEVICES
from inventory_tng.models.admins import Admin, ResetLink
from inventory_tng.models.devices import RegistrationCode
from inventory_tng.web.single_use_forms import FIELD, SENT_AGAIN, Made

from helpers.mail import NO_MAIL, SentMail
from helpers.markup import addresses, box_values, read, text
from helpers.rows import tag
from helpers.sign_in import (
    TIME_STEP,
    app_clock,
    code,
    new_admin,
    sign_in_with,
    with_code,
)

AGAIN = "That form was already sent"
CANT_SHOW = "Back and Forward can't show this page again"


def key_on(page: Response, action: str) -> str:
    """The key of the form on the page that is sent to `action`, its first
    field, which a browser never fills in again from an earlier visit."""
    form = read(page.text).one("form", action=action)
    field = form.one("input", type="hidden", name=FIELD)
    assert form.find("input") is field, action
    assert field.attrs.get("autocomplete") == "off", action
    assert re.fullmatch("[0-9a-f-]+", field.attrs["value"]), action
    return field.attrs["value"]


def sent_twice(
    client: TestClient, page: str, action: str, **form: str
) -> tuple[Response, Response]:
    form[FIELD] = key_on(client.get(page), action)
    return client.post(action, data=form), client.post(action, data=form)


def sent_twice_with_codes(
    client: TestClient, session: Session, page: str, action: str
) -> tuple[Response, Response]:
    """As sent_twice, for a form that may ask for a code: each send comes
    with a new one of Ada's."""
    key = key_on(client.get(page), action)
    made, again = (
        client.post(action, data=with_code(session) | {FIELD: key}) for _ in "12"
    )
    return made, again


def assert_sent_again(again: Response, back: str) -> None:
    assert again.status_code == 409
    assert again.headers["cache-control"] == "no-store"
    assert AGAIN in text(again)
    assert back in addresses(again.text)
    # Its own link back is not taken for the way back above the title
    # (admin_base.html's back_link), which it has none of.
    assert not read(again.text).all("*", class_="back")


@pytest.mark.parametrize(
    ("count", "said"), [(1, "registration_code"), (4, "registration_codes")]
)
def test_registration_codes_are_made_once(
    admin_client: TestClient, session: Session, count: int, said: Made
) -> None:
    """Sent again, the page says a code, or codes, as the form made."""
    label = f"Open day {tag()}"
    form = {"uses": "1", "days": "1", "label": label, "count": str(count)}
    with capture_logs() as logs:
        made, again = sent_twice(admin_client, DEVICES, CODES, **form)
    assert made.status_code == 200
    assert CANT_SHOW in text(made)
    assert_sent_again(again, DEVICES)
    assert str(SENT_AGAIN[said]) in text(again)
    made_here = select(func.count()).where(RegistrationCode.label == label)
    assert session.scalar(made_here) == count
    assert {"event": "form sent again", "made": said} in [
        {key: line.get(key) for key in ("event", "made")} for line in logs
    ]


def test_recovery_codes_are_made_once(
    make_client: Callable[..., TestClient], session: Session
) -> None:
    """Sent again, it is answered before a code is asked for, with a way
    back to the page it came from."""
    client = make_client()
    _, key = new_admin(session)
    sign_in_with(client, key=key)
    action = f"{ACCOUNT}/recovery-codes"
    referer = {"referer": f"https://testserver{ACCOUNT}"}
    with app_clock() as at:
        later = code(key, at + TIME_STEP)
        form = {
            FIELD: key_on(client.get(ACCOUNT), action),
            "confirm_code": later,
        }
        made = client.post(action, data=form, headers=referer)
        again = client.post(action, data=form, headers=referer)
    assert CANT_SHOW in text(made)
    assert_sent_again(again, ACCOUNT)
    # The codes shown the first time still work.
    (ada,) = session.scalars(select(Admin)).all()
    assert accounts.recovery_codes_left(session, ada.id) == accounts.RECOVERY_CODES
    codes = [item.text for item in read(made.text).all("li")]
    assert [code for code in codes if re.fullmatch(r"[A-Z0-9-]+", code)]


def test_recovery_codes_sent_again_past_the_guard_make_none(
    make_client: Callable[..., TestClient],
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key spent between the guard's look (is_spent) and the route's
    spending, as another sending at once would: the route makes nothing.
    The other sending is stood in for by spending the key in this one's
    transaction; test_requests_at_once holds two transactions."""

    def spent_meanwhile(session: Session, typed: object) -> bool:
        key = form_key(typed)
        assert key is not None
        spend(session, key)
        return False

    monkeypatch.setattr(guards, "is_spent", spent_meanwhile)
    client = make_client()
    ada, key = new_admin(session)
    sign_in_with(client, key=key)
    action = f"{ACCOUNT}/recovery-codes"
    form = {FIELD: key_on(client.get(ACCOUNT), action)}
    with app_clock() as at:
        form["confirm_code"] = code(key, at + TIME_STEP)
        again = client.post(action, data=form)
    assert_sent_again(again, ACCOUNT)
    assert str(SENT_AGAIN["recovery_codes"]) in text(again)
    assert accounts.recovery_codes_left(session, ada.id) == 0


def link_count(session: Session, admin: Admin) -> int:
    return (
        session.scalar(select(func.count()).where(ResetLink.admin_id == admin.id)) or 0
    )


def test_a_reset_link_is_made_and_mailed_once(
    make_client: Callable[..., TestClient], session: Session, sent_mail: SentMail
) -> None:
    client = make_client(mailer=sent_mail)
    _, key = new_admin(session)
    sign_in_with(client, key=key)
    ben, _ = new_admin(session, "ben", email="ben@example.org")
    page = f"{ADMINS}/{ben.id}"
    # The form is on the page asking to confirm; the way back, their page.
    reset = f"{page}/reset"
    made, again = sent_twice_with_codes(client, session, reset, reset)
    assert CANT_SHOW in text(made)
    assert_sent_again(again, page)
    assert link_count(session, ben) == 1
    assert [m.kind for m in sent_mail] == ["reset_link_made"]


def test_bringing_back_and_inviting_are_sent_once(
    admin_client: TestClient, session: Session
) -> None:
    me = session.scalars(select(Admin)).one()
    ben, _ = new_admin(session, "ben")
    accounts.retire_admin(session, ben.id, me.id, datetime.now(UTC), NO_MAIL)
    page = f"{ADMINS}/{ben.id}"
    made, again = sent_twice_with_codes(admin_client, session, page, f"{page}/restore")
    assert made.status_code == 200
    assert_sent_again(again, page)
    made, again = sent_twice(admin_client, ADMINS, ADMINS, username="grace")
    assert made.status_code == 200
    assert_sent_again(again, ADMINS)


def test_a_form_without_its_key_is_a_bad_request(admin_client: TestClient) -> None:
    refused = admin_client.post(CODES, data={"uses": "1", FIELD: "not a key"})
    assert refused.status_code == 400


def test_a_key_that_is_not_text_is_none() -> None:
    """As a form without the field gives (form.get)."""
    assert form_key(None) is None


def test_a_form_sent_once_carries_its_page(admin_client: TestClient) -> None:
    """For the page saying it was sent before to link back: browsers send
    no Referer here."""
    assert ACCOUNT in box_values(admin_client.get(ACCOUNT).text, "back")


@pytest.mark.clone
def test_the_page_script_names_the_field_a_form_sent_once_carries() -> None:
    """frontend/src/admin/single-use-forms.ts, which stops a second sending
    of such a form, finds it by this field."""
    source = (CLONE / "frontend" / "src" / "admin" / "single-use-forms.ts").read_text()
    assert f'export const SINGLE_USE_FIELD = "{FIELD}";' in source
