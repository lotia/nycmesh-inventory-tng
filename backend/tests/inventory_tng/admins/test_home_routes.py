"""The administrators' home page, and its warnings
(admins/home_routes.py), and the navigation's badge."""

from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.admins.home_routes import ALONE, NO_EMAIL
from inventory_tng.models.places import Location
from inventory_tng.stock.paths import COUNTS
from inventory_tng.web.navigation import retired_holding

from helpers.mail import SentMail
from helpers.markup import links, read, text
from helpers.rows import add, tag
from helpers.sign_in import new_admin, sign_in_with
from helpers.stock import badge, badge_of, retire, stocked, write


def test_a_lone_administrator_is_told(admin_client: TestClient) -> None:
    assert str(ALONE) in text(admin_client.get("/admin"))


def test_two_administrators_are_not(admin_client: TestClient, session: Session) -> None:
    new_admin(session, "ben")
    assert str(ALONE) not in text(admin_client.get("/admin"))


def test_an_invite_not_taken_up_does_not_count(admin_client: TestClient) -> None:
    """Grace has no password yet, so she could let nobody back in."""
    admin_client.post("/admin/admins", data={"username": "grace"})
    assert str(ALONE) in text(admin_client.get("/admin"))


def test_no_email_is_told_only_where_mail_is_set_up(
    make_client: Callable[..., TestClient], session: Session, sent_mail: SentMail
) -> None:
    with_mail = make_client(mailer=sent_mail)
    ada, key = new_admin(session)
    assert sign_in_with(with_mail, key=key).status_code == 303
    assert str(NO_EMAIL) in text(with_mail.get("/admin"))
    ada.email = "ada@example.org"
    session.flush()
    assert str(NO_EMAIL) not in text(with_mail.get("/admin"))
    without = make_client(address="192.0.2.80")
    assert sign_in_with(without, key=key, later=True).status_code == 303
    assert str(NO_EMAIL) not in text(without.get("/admin"))


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
    home = admin_client.get("/admin")
    page = read(home.text)
    assert retired_holding(2) in text(home)
    for retired in (place.van, shelf):
        start = (f"Start counting {retired.name}", f"{COUNTS}/{retired.id}")
        assert start in links(page)
    assert badge(page) == badge_of(2)


def test_the_navigation_marks_the_section_a_page_is_in(
    admin_client: TestClient,
) -> None:
    """Its own page as the page, one inside it as within it, and Home only
    on itself (docs/decisions/0019)."""
    home = admin_client.get("/admin").text
    assert ("Home", "/admin", "page") in current(home)
    assert "/admin/items" not in [href for _, href, _ in current(home)]
    items = admin_client.get("/admin/items").text
    assert ("Items", "/admin/items", "page") in current(items)
    assert "/admin" not in [href for _, href, _ in current(items)]
    stock = admin_client.get("/admin/stock/reorder").text
    assert ("Stock", "/admin/stock", "true") in current(stock)


def current(page: str) -> list[tuple[str, str | None, str | None]]:
    """Each link marked as where the page is: its words, address and how."""
    return [
        (link.text, link.attrs.get("href"), link.attrs.get("aria-current"))
        for link in read(page).all("a")
        if "aria-current" in link.attrs
    ]
