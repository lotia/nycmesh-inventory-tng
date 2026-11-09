"""The administrators' home page, and its warnings (admin/home.py), and
the navigation's badge."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.admin.counts import COUNTS
from inventory_tng.admin.home import ALONE, NO_EMAIL
from inventory_tng.models import Location

from helpers.mail import SentMail
from helpers.rows import add, tag
from helpers.sign_in import code, new_admin, sign_in_with, text
from helpers.stock import badge_html, retire, stocked, write


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


def test_retired_places_holding_stock_are_told_and_counted_in_the_navigation(
    admin_client: TestClient, session: Session
) -> None:
    """More or less than nothing: three saved into the van and three taken
    from a shelf, each after it was retired."""
    place = stocked(session)
    shelf = add(session, Location(name=f"Shelf {tag()}", kind="store"))
    assert "retired place" not in text(admin_client.get("/admin"))
    retire(session, place.van, shelf)
    write(session, place)
    write(session, place, from_location_id=shelf.id)
    page = text(admin_client.get("/admin"))
    assert "2 retired places hold stock" in page
    for retired in (place.van, shelf):
        assert f'<a href="{COUNTS}/{retired.id}">Count {retired.name}</a>' in page
    assert badge_html(2) in page
