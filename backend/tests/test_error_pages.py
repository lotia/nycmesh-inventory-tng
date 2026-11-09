"""What a person meets when there is no page, or something goes wrong: a
page of the app's own, with a way on; the API keeps answering JSON
(inventory-tng-nrb). The site's root sends phones to the scan page (pkv)."""

from collections.abc import Callable, Iterator
from typing import NoReturn

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from inventory_tng import pages
from inventory_tng.admin.items import ITEMS
from inventory_tng.bundle import SCAN

from helpers.sign_in import text


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_the_root_goes_to_the_scan_page(
    make_client_without_database: Callable[..., TestClient], method: str
) -> None:
    answer = make_client_without_database().request(method, "/")
    assert (answer.status_code, answer.headers["location"]) == (302, SCAN)


@pytest.mark.parametrize(
    ("method", "path", "status", "title"),
    [
        ("GET", "/nowhere", 404, "Not found"),
        ("POST", SCAN, 405, "Not here"),
    ],
)
def test_a_page_error_is_a_page(
    make_client_without_database: Callable[..., TestClient],
    method: str,
    path: str,
    status: int,
    title: str,
) -> None:
    answer = make_client_without_database().request(method, path)
    assert answer.status_code == status
    assert answer.headers["content-type"].startswith("text/html")
    page = text(answer)
    assert f"<h1>{title}</h1>" in page
    assert f'href="{SCAN}"' in page
    if status == 405:
        # In no fixed order.
        assert set(answer.headers["allow"].split(", ")) == {"GET", "HEAD"}


def test_nothing_by_that_id_is_the_same_page(admin_client: TestClient) -> None:
    answer = admin_client.get(f"{ITEMS}/999999")
    assert answer.status_code == 404
    assert "<h1>Not found</h1>" in text(answer)


@pytest.mark.parametrize(
    ("path", "home"), [("/nowhere", SCAN), ("/admin/x/y", "/admin")]
)
def test_the_logo_goes_where_they_likely_came_from(
    make_client_without_database: Callable[..., TestClient], path: str, home: str
) -> None:
    assert (
        f'class="brand" href="{home}"' in make_client_without_database().get(path).text
    )


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
    app = make_app_without_database()

    def fail() -> None:
        raise RuntimeError("a bug")

    def refuse(status: int) -> None:
        raise HTTPException(status)

    app.add_api_route("/broken", fail)
    app.add_api_route("/api/broken", fail)
    app.add_api_route("/refused/{status}", refuse)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_a_failure_on_a_page_is_a_page(failing: TestClient) -> None:
    answer = failing.get("/broken")
    assert answer.status_code == 500
    assert "<h1>Something went wrong</h1>" in text(answer)
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
    assert "<h1>That didn't work</h1>" in text(answer)


def test_below_400_is_no_error_page(failing: TestClient) -> None:
    answer = failing.get("/refused/304")
    assert (answer.status_code, answer.content) == (304, b"")
