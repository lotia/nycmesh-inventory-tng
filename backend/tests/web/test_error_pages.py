"""What a person meets when there is no page, or something goes wrong: a
page of the app's own, with a way on; the API keeps answering JSON
(inventory-tng-nrb). The site's root sends phones to the scan page (pkv).
Under /admin, once the request checked the sign-in, the page keeps the
administrator's masthead, read without the database (8i4.80)."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import NoReturn

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.admins import accounts
from inventory_tng.admins.guards import admin_signed_in
from inventory_tng.admins.paths import SIGN_OUT
from inventory_tng.catalogue.paths import ITEMS
from inventory_tng.web import pages
from inventory_tng.web.frontend_bundle import SCAN

from helpers.apps import with_failures
from helpers.markup import addresses, forms, read, words_on
from helpers.query_counts import counting


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_the_root_goes_to_the_scan_page(
    make_client_without_database: Callable[..., TestClient], method: str
) -> None:
    answer = make_client_without_database().request(method, "/")
    assert (answer.status_code, answer.headers["location"]) == (302, SCAN)


@pytest.mark.parametrize(
    ("method", "path", "status", "title"),
    [
        ("GET", "/nowhere", 404, pages.NOT_FOUND),
        ("POST", SCAN, 405, pages.NOT_HERE),
    ],
)
def test_a_page_error_is_a_page(
    make_client_without_database: Callable[..., TestClient],
    method: str,
    path: str,
    status: int,
    title: object,
) -> None:
    answer = make_client_without_database().request(method, path)
    assert answer.status_code == status
    assert answer.headers["content-type"].startswith("text/html")
    assert read(answer.text).all("h1", str(title))
    assert SCAN in addresses(answer.text)
    if status == 405:
        # In no fixed order.
        assert set(answer.headers["allow"].split(", ")) == {"GET", "HEAD"}


def test_nothing_by_that_id_is_the_same_page(admin_client: TestClient) -> None:
    answer = admin_client.get(f"{ITEMS}/999999")
    assert answer.status_code == 404
    assert read(answer.text).all("h1", str(pages.NOT_FOUND))


@pytest.mark.parametrize(
    ("path", "home"), [("/nowhere", SCAN), ("/admin/x/y", "/admin")]
)
def test_the_logo_goes_where_they_likely_came_from(
    make_client_without_database: Callable[..., TestClient], path: str, home: str
) -> None:
    page = make_client_without_database().get(path).text
    assert read(page).all("a", class_="brand", href=home)


@pytest.mark.parametrize(
    ("method", "path", "status", "detail"),
    [
        ("GET", "/api/nowhere", 404, "Not Found"),
        ("POST", "/api/healthz", 405, "Method Not Allowed"),
    ],
)
def test_the_api_answers_json(
    make_client_without_database: Callable[..., TestClient],
    method: str,
    path: str,
    status: int,
    detail: str,
) -> None:
    answer = make_client_without_database().request(method, path)
    assert (answer.status_code, answer.json()) == (status, {"detail": detail})


@pytest.fixture
def failing(make_app_without_database: Callable[..., FastAPI]) -> Iterator[TestClient]:
    """An app with a page and an API route that fail, its errors answered
    as a browser would get them, not raised into the test. With no database:
    the failures here are the app's own, never a database's 503."""
    app = with_failures(make_app_without_database())
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_a_failure_on_a_page_is_a_page(failing: TestClient) -> None:
    answer = failing.get("/broken")
    assert answer.status_code == 500
    assert read(answer.text).all("h1", str(pages.WENT_WRONG))
    assert "a bug" not in answer.text


def test_a_failure_in_the_api_is_json(failing: TestClient) -> None:
    answer = failing.get("/api/broken")
    assert (answer.status_code, answer.json()) == (
        500,
        {"detail": "Internal Server Error"},
    )


def test_a_failure_page_that_fails_falls_back_to_text(
    failing: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: object, **_kwargs: object) -> NoReturn:
        raise RuntimeError("the template too")

    monkeypatch.setattr(pages, "page", broken)
    answer = failing.get("/broken")
    assert (answer.status_code, answer.text) == (500, "Internal Server Error")


def test_a_request_the_app_cannot_take_says_so(failing: TestClient) -> None:
    answer = failing.get("/refused/400")
    assert answer.status_code == 400
    assert read(answer.text).all("h1", str(pages.DID_NOT_WORK))


def test_below_400_is_no_error_page(failing: TestClient) -> None:
    answer = failing.get("/refused/304")
    assert (answer.status_code, answer.content) == (304, b"")


def has_masthead(page: str) -> bool:
    """Whether the page shows an administrator signed in: their name, the
    sections and a way to sign out."""
    return (
        "Signed in as ada" in words_on(page)
        and ITEMS in addresses(page)
        and ("post", SIGN_OUT) in forms(page)
    )


def test_nothing_by_that_id_keeps_the_masthead(admin_client: TestClient) -> None:
    answer = admin_client.get(f"{ITEMS}/999999")
    assert answer.status_code == 404
    assert has_masthead(answer.text)


def test_a_mistyped_address_stays_plain(admin_client: TestClient) -> None:
    answer = admin_client.get("/admin/x/y")
    assert answer.status_code == 404
    assert not has_masthead(answer.text)


@dataclass
class Sent:
    """The statements requests sent, and how many had been sent when each
    page failed."""

    statements: list[str]
    at_failure: list[int] = field(default_factory=list)


@pytest.fixture
def admin_failing(admin_client: TestClient, session: Session) -> Iterator[Sent]:
    """admin_client's app with pages under /admin that fail after the
    sign-in is checked, or before it."""
    with counting(session) as statements:
        sent = Sent(statements)

        def failing(error: Exception) -> Callable[[], None]:
            def route() -> None:
                sent.at_failure.append(len(statements))
                raise error

            return route

        app = admin_client.app
        assert isinstance(app, FastAPI)
        guarded = [Depends(admin_signed_in)]
        missing, broken = failing(HTTPException(404)), failing(RuntimeError("a bug"))
        app.add_api_route("/admin/test/missing", missing, dependencies=guarded)
        app.add_api_route("/admin/test/broken", broken, dependencies=guarded)
        app.add_api_route("/admin/test/broken-early", broken)
        yield sent


@pytest.mark.parametrize(
    ("path", "status", "masthead"),
    [
        ("/admin/test/missing", 404, True),
        ("/admin/test/broken", 500, True),
        ("/admin/test/broken-early", 500, False),
    ],
)
def test_an_error_page_under_admin_reads_no_database(
    admin_client: TestClient,
    admin_failing: Sent,
    path: str,
    status: int,
    masthead: bool,
) -> None:
    answer = admin_client.get(path)
    assert answer.status_code == status
    assert has_masthead(answer.text) == masthead
    # Nothing was read once the page failed: its error page included. The
    # sign-in check's reads are counted, so the count sees the app's.
    assert bool(admin_failing.statements) == masthead
    assert admin_failing.at_failure == [len(admin_failing.statements)]


def test_a_failure_within_the_sign_in_check_stays_plain(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_session: Session) -> NoReturn:
        raise RuntimeError("a bug")

    monkeypatch.setattr(accounts, "sign_in_settings", broken)
    answer = admin_client.get(ITEMS)
    assert answer.status_code == 500
    assert not has_masthead(answer.text)
