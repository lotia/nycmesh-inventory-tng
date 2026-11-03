"""The administrators' home page, and its warnings (admin/home.py)."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from conftest import SentMail
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_sign_in import code, new_admin, sign_in_with, text

from inventory_tng.admin.home import ALONE, NO_EMAIL


def test_a_lone_administrator_is_told(admin_client: TestClient) -> None:
    assert ALONE in text(admin_client.get("/admin"))


def test_two_administrators_are_not(admin_client: TestClient, session: Session) -> None:
    new_admin(session, "ben")
    assert ALONE not in text(admin_client.get("/admin"))


def test_an_invite_not_taken_up_does_not_count(
    admin_client: TestClient, session: Session
) -> None:
    """Grace has no password yet, so she could let nobody back in."""
    admin_client.post("/admin/admins", data={"username": "grace"})
    assert ALONE in text(admin_client.get("/admin"))


def test_no_email_is_told_only_where_mail_is_set_up(
    make_client: Callable[..., TestClient], session: Session, sent_mail: SentMail
) -> None:
    with_mail = make_client(mailer=sent_mail)
    ada, key = new_admin(session)
    assert sign_in_with(with_mail, code=code(key)).status_code == 303
    assert NO_EMAIL in text(with_mail.get("/admin"))
    ada.email = "ada@example.org"
    session.flush()
    assert NO_EMAIL not in text(with_mail.get("/admin"))
    without = make_client(address="192.0.2.80")
    later = code(key, datetime.now(UTC) + timedelta(seconds=30))
    assert sign_in_with(without, code=later).status_code == 303
    assert NO_EMAIL not in text(without.get("/admin"))
