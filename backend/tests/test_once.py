"""A form that makes something shown only once, sent again, as Back then
Forward offers, makes nothing new (once.py, inventory-tng-8i4.74)."""

import re
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng import accounts, once
from inventory_tng.account import ACCOUNT
from inventory_tng.admin.admins import ADMINS
from inventory_tng.admin.devices import CODES, DEVICES
from inventory_tng.models import Admin, RegistrationCode, ResetLink

from helpers.mail import NO_MAIL, SentMail
from helpers.rows import tag
from helpers.sign_in import code, later_code, new_admin, sign_in_with, text, with_code

AGAIN = "That form was already sent"
CANT_SHOW = "Back and Forward can't show this page again"


def key_on(page: Response, action: str) -> str:
    """The key of the form on the page that is sent to `action`."""
    match = re.search(
        rf'action="{re.escape(action)}"[^>]*>\s*<input type="hidden" name="once" '
        r'value="([0-9a-f-]+)" autocomplete="off">',
        page.text,
    )
    assert match is not None, action
    return match.group(1)


def sent_twice(
    client: TestClient, page: str, action: str, **form: str
) -> tuple[Response, Response]:
    form["once"] = key_on(client.get(page), action)
    return client.post(action, data=form), client.post(action, data=form)


def sent_twice_with_codes(
    client: TestClient, session: Session, page: str, action: str
) -> tuple[Response, Response]:
    """As sent_twice, for a form that may ask for a code: each send comes
    with a new one of Ada's."""
    key = key_on(client.get(page), action)
    made, again = (
        client.post(action, data=with_code(session) | {"once": key}) for _ in "12"
    )
    return made, again


def assert_sent_again(again: Response, back: str) -> None:
    assert again.status_code == 409
    assert again.headers["cache-control"] == "no-store"
    words = text(again)
    assert AGAIN in words
    assert f'href="{back}"' in words
    # Its own link back is not taken for the way back above the title
    # (admin_base.html's back_link), which it has none of.
    assert 'class="back"' not in words


def test_a_registration_code_is_made_once(
    admin_client: TestClient, session: Session
) -> None:
    label = f"Open day {tag()}"
    form = {"uses": "1", "days": "1", "label": label}
    with capture_logs() as logs:
        made, again = sent_twice(admin_client, DEVICES, CODES, **form)
    assert made.status_code == 200
    assert CANT_SHOW in text(made)
    assert_sent_again(again, DEVICES)
    assert "made a registration code" in text(again)
    made_here = select(func.count()).where(RegistrationCode.label == label)
    assert session.scalar(made_here) == 1
    assert {"event": "form sent again", "made": "registration_code"} in [
        {key: line.get(key) for key in ("event", "made")} for line in logs
    ]


def test_recovery_codes_are_made_once(
    make_client: Callable[..., TestClient], session: Session
) -> None:
    """Sent again, it is answered before a code is asked for, with a way
    back to the page it came from."""
    client = make_client()
    _, key = new_admin(session)
    sign_in_with(client, code=code(key))
    action = f"{ACCOUNT}/recovery-codes"
    later = later_code(key)
    referer = {"referer": f"https://testserver{ACCOUNT}"}
    form = {"once": key_on(client.get(ACCOUNT), action), "confirm_code": later}
    made = client.post(action, data=form, headers=referer)
    again = client.post(action, data=form, headers=referer)
    assert CANT_SHOW in text(made)
    assert_sent_again(again, ACCOUNT)
    # The codes shown the first time still work.
    (ada,) = session.scalars(select(Admin)).all()
    assert accounts.recovery_codes_left(session, ada.id) == accounts.RECOVERY_CODES
    assert re.findall(r"<li>[A-Z0-9-]+</li>", made.text)


def link_count(session: Session, admin: Admin) -> int:
    return (
        session.scalar(select(func.count()).where(ResetLink.admin_id == admin.id)) or 0
    )


def test_a_reset_link_is_made_and_mailed_once(
    make_client: Callable[..., TestClient], session: Session, sent_mail: SentMail
) -> None:
    client = make_client(mailer=sent_mail)
    _, key = new_admin(session)
    sign_in_with(client, code=code(key))
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
    refused = admin_client.post(CODES, data={"uses": "1", "once": "not a key"})
    assert refused.status_code == 400


def test_a_key_that_is_not_text_is_none() -> None:
    """As a form without the field gives (form.get)."""
    assert once.form_key(None) is None


def test_a_form_sent_once_carries_its_page(admin_client: TestClient) -> None:
    """For the page saying it was sent before to link back: browsers send
    no Referer here."""
    assert f'name="back" value="{ACCOUNT}"' in admin_client.get(ACCOUNT).text
