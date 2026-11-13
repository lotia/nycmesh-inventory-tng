"""Which build of the scan page sent a batch, and a build too old held back,
not refused (page_builds.py)."""

from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng import openapi, page_builds
from inventory_tng.bundle import BUILD_ID
from inventory_tng.page_builds import HEADER, is_too_old

from helpers.stock import Place

URL = "/api/transactions"
OLDEST = "2026-11-12T10:00:00.000Z"
SERVERS = "2026-11-12T12:00:00.000Z"


@pytest.fixture
def place(session: Session) -> Place:
    return Place(session)


@pytest.fixture
def served(
    registered_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    """A registered device's client, of a server whose pages are SERVERS."""
    app = cast(FastAPI, registered_client.app)
    monkeypatch.setattr(app.state.bundle, "build_id", lambda: SERVERS)
    return registered_client


@pytest.fixture
def oldest(monkeypatch: pytest.MonkeyPatch) -> str:
    """A server that takes no batch from a build before OLDEST."""
    monkeypatch.setattr(page_builds, "OLDEST_BUILD", OLDEST)
    return OLDEST


@pytest.mark.parametrize(
    ("build", "too_old"),
    [
        (OLDEST, False),
        ("2026-11-12T10:00:00.001Z", False),
        ("2026-11-12T09:59:59.999Z", True),
        ("", True),
        ("tomorrow", True),
        ("2026-11-12", True),
    ],
)
def test_too_old_is_before_the_oldest_build_taken(
    oldest: str, build: str, too_old: bool
) -> None:
    assert is_too_old(build) is too_old


@pytest.mark.parametrize("build", ["tomorrow", "1999-01-01T00:00:00.000Z"])
def test_with_no_oldest_build_every_build_is_taken(
    build: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(page_builds, "OLDEST_BUILD", None)
    assert not is_too_old(build)


def test_the_oldest_build_is_none_or_a_builds_id() -> None:
    """Compared as text, an id mistyped would hold back every build, or
    none."""
    oldest = page_builds.OLDEST_BUILD
    assert oldest is None or BUILD_ID.fullmatch(oldest)


def test_a_reply_names_the_servers_build(served: TestClient, place: Place) -> None:
    response = served.post(URL, json=place.batch(), headers={HEADER: SERVERS})
    assert response.status_code == 201
    assert response.headers[HEADER] == SERVERS


def test_without_a_build_a_reply_names_none(
    registered_client: TestClient, place: Place
) -> None:
    response = registered_client.post(URL, json=place.batch())
    assert response.status_code == 201
    assert HEADER not in response.headers


@pytest.mark.usefixtures("oldest")
def test_a_batch_from_a_build_too_old_is_held_back_before_it_is_checked(
    served: TestClient, place: Place, session: Session
) -> None:
    """A field the server now requires, missing from what an older build
    sends, is no 422 the page would refuse it for."""
    batch = place.batch(lines=[{"item_id": place.item.id}])
    with capture_logs() as logs:
        response = served.post(
            URL, json=batch, headers={HEADER: "2026-11-12T09:00:00.000Z"}
        )
    assert (response.status_code, response.json()) == (
        409,
        {
            "reason": "build_too_old",
            "build": "2026-11-12T09:00:00.000Z",
            "oldest": OLDEST,
            "message": page_builds.WHAT_TO_DO,
        },
    )
    assert response.headers[HEADER] == SERVERS
    (line,) = [line for line in logs if line["event"] == "batch from a build too old"]
    assert line["oldest"] == OLDEST


@pytest.mark.usefixtures("oldest")
def test_a_batch_naming_no_build_is_from_another_client_and_saved(
    served: TestClient, place: Place
) -> None:
    """Only the scan page sends HEADER: a client of the API is never too
    old."""
    response = served.post(URL, json=place.batch())
    assert response.status_code == 201


@pytest.mark.usefixtures("oldest")
def test_a_batch_naming_no_build_is_told_which_field_it_lacks(
    served: TestClient, place: Place
) -> None:
    """Another client of the API that sends what the batch no longer takes
    is told what, as by a 422, not that a build is too old."""
    batch = place.batch(lines=[{"item_id": place.item.id}])
    response = served.post(URL, json=batch)
    assert response.status_code == 422
    (problem,) = response.json()["detail"]
    assert problem["loc"] == ["body", "lines", 0, "quantity"]


@pytest.mark.usefixtures("oldest")
def test_a_batch_from_a_build_new_enough_is_saved(
    served: TestClient, place: Place
) -> None:
    response = served.post(URL, json=place.batch(), headers={HEADER: OLDEST})
    assert response.status_code == 201


@pytest.mark.usefixtures("oldest")
def test_a_build_too_old_is_told_so_before_an_unregistered_device(
    served: TestClient, place: Place
) -> None:
    """Loaded again, the page sends the batch, and is told to register."""
    served.cookies.clear()
    response = served.post(URL, json=place.batch(), headers={HEADER: "tomorrow"})
    assert response.json()["reason"] == "build_too_old"


# Each field the batch endpoint requires, as "Model.field", from its schema.
REQUIRED = {
    "Batch.key",
    "Batch.kind",
    "Batch.volunteer_id",
    "Batch.lines",
    "Line.item_id",
    "Line.quantity",
    "LengthReport.open_package_key",
    "LengthReport.left",
}


def required_fields(schema: dict[str, Any]) -> set[str]:
    """Each field required in the batch endpoint's body, at any depth."""
    models = schema["components"]["schemas"]
    body = schema["paths"][URL]["post"]["requestBody"]["content"]["application/json"]
    found: set[str] = set()
    seen: set[str] = set()

    def visit(node: object) -> None:
        if isinstance(node, list):
            for each in node:
                visit(each)
        elif isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                name = ref.rsplit("/", 1)[-1]
                if name not in seen:
                    seen.add(name)
                    found.update(
                        f"{name}.{field}" for field in models[name].get("required", ())
                    )
                    visit(models[name])
            for value in node.values():
                visit(value)

    visit(body["schema"])
    return found


def test_no_field_of_a_batch_becomes_required_unannounced() -> None:
    """A field the scan page's batch comes to require is required only in a
    deploy after the one that added it, with OLDEST_BUILD set to that one's
    build: a tab of the build before would otherwise have its batches
    refused, and its volunteers' work lost."""
    required = required_fields(openapi.schema())
    newly = sorted(required - REQUIRED)
    assert not newly, (
        f"The batch endpoint now requires {', '.join(newly)}. Expand, then "
        "contract (CONTRIBUTING.md, The scan page): add a field as optional, "
        "and require it only in a later deploy, after the one whose page "
        "sends it is live in production. That later change sets OLDEST_BUILD "
        "in backend/src/inventory_tng/page_builds.py to the build of the "
        "deploy that added it: follow DEPLOYMENT.md, Making a field required. "
        "Then add the field to REQUIRED here. A field required only inside "
        "an object that is itself new and optional takes nothing from an "
        "older build: just add it."
    )
    assert required == REQUIRED, (
        f"No longer required: {', '.join(sorted(REQUIRED - required))}. "
        "Take it out of REQUIRED, so requiring it again is caught."
    )
