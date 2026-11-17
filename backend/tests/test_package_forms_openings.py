"""Opening a package on the scan page, and cutting from an open package
or moving one whole by its key (docs/decisions/0020)."""

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng import ledger, open_packages
from inventory_tng.models import Admin, OpenPackage

from helpers.package_forms import URL, forms, sealed_line, taken
from helpers.rows import add, tag
from helpers.stock import Spools

# Opening on the scan page.


def opening(spools: Spools, *lines: dict[str, object]) -> dict[str, object]:
    store = spools.store.id
    return spools.place.batch(
        kind="open", from_location_id=store, to_location_id=store, lines=list(lines)
    )


def opened_line(spools: Spools, **fields: object) -> dict[str, object]:
    return sealed_line(spools, "305") | {"open_package_key": str(uuid.uuid4())} | fields


def test_a_package_opened_and_followed_is_made_once(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    line = opened_line(spools)
    batch = opening(spools, line)
    assert registered_client.post(URL, json=batch).status_code == 201
    assert registered_client.post(URL, json=batch).status_code == 200
    made = session.scalars(
        select(OpenPackage).where(
            OpenPackage.key == uuid.UUID(str(line["open_package_key"]))
        )
    ).one()
    store, spool = spools.store.id, spools.spool.id
    assert (made.package_id, made.location_id) == (spool, store)
    assert forms(session, spools.cable) == {
        (store, spool, None): 305,
        (store, None, made.id): 305,
    }
    listed = registered_client.get("/api/catalogue").json()["open_packages"]
    mine = [entry for entry in listed if entry["id"] == made.id]
    assert mine == [
        {
            "id": made.id,
            "key": str(made.key),
            "item_id": spools.cable.id,
            "package_id": spool,
            "package_name": spools.spool.name,
            "location_id": store,
            "opened_at": mine[0]["opened_at"],
            "left": "305.000",
            "short_below": None,
        }
    ]
    package = next(
        pack
        for item in registered_client.get("/api/catalogue").json()["items"]
        if item["id"] == spools.cable.id
        for pack in item["packages"]
    )
    assert package["track_opened"] is True


def test_a_package_its_item_does_not_follow_opens_loose(
    registered_client: TestClient, session: Session
) -> None:
    """Whatever key the page sent: the server's setting decides."""
    spools = Spools(session, tracked=False)
    batch = opening(spools, opened_line(spools, quantity="610"))
    assert registered_client.post(URL, json=batch).status_code == 201
    store = spools.store.id
    assert forms(session, spools.cable) == {(store, None, None): 610}


def test_an_opening_without_a_key_makes_one_from_the_batch(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    batch = opening(spools, sealed_line(spools, "305"))
    assert registered_client.post(URL, json=batch).status_code == 201
    assert registered_client.post(URL, json=batch).status_code == 200
    made = session.scalars(
        select(OpenPackage).where(OpenPackage.item_id == spools.cable.id)
    ).all()
    assert len(made) == 1


def test_a_followed_package_opens_one_a_line(
    registered_client: TestClient, spools: Spools
) -> None:
    batch = opening(spools, opened_line(spools, quantity="610"))
    response = registered_client.post(URL, json=batch)
    assert response.status_code == 422
    assert [(p["loc"][1:], p["type"]) for p in response.json()["detail"]] == [
        (["lines", 0, "quantity"], "one_package")
    ]


def test_packages_the_page_thought_loose_open_one_each(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    """Tracking turned on since the page loaded (inventory-tng-1o0): its
    one line of two spools, with no key, becomes two open packages, under
    keys made from the batch's, and sent again, is found written."""
    batch = opening(spools, sealed_line(spools, "610"))
    with capture_logs() as logs:
        assert registered_client.post(URL, json=batch).status_code == 201
    assert registered_client.post(URL, json=batch).status_code == 200
    made = session.scalars(
        select(OpenPackage)
        .where(OpenPackage.item_id == spools.cable.id)
        .order_by(OpenPackage.id)
    ).all()
    assert len(made) == 2
    store = spools.store.id
    assert forms(session, spools.cable) == {
        (store, None, made[0].id): 305,
        (store, None, made[1].id): 305,
    }
    (split,) = [
        x for x in logs if x["event"] == "opening line split into open packages"
    ]
    assert (split["item_package_id"], split["packages"]) == (spools.spool.id, 2)
    # The same key with one spool fewer is another batch, as is one of
    # more than a batch could open, found so without making their keys.
    for quantity in ("305", "305000000"):
        other = batch | {"lines": [sealed_line(spools, quantity)]}
        assert registered_client.post(URL, json=other).status_code == 409
    # And one of a package there is none of.
    gone = batch | {"lines": [sealed_line(spools, "610") | {"package_id": 999999}]}
    assert registered_client.post(URL, json=gone).status_code == 409


def test_an_opening_written_loose_is_found_after_tracking_is_turned_on(
    registered_client: TestClient, session: Session
) -> None:
    spools = Spools(session, tracked=False)
    batch = opening(spools, sealed_line(spools, "610"))
    assert registered_client.post(URL, json=batch).status_code == 201
    spools.cable.track_opened = True
    session.flush()
    assert registered_client.post(URL, json=batch).status_code == 200
    assert forms(session, spools.cable) == {(spools.store.id, None, None): 610}


def test_an_opening_of_more_packages_than_lines_is_refused(
    registered_client: TestClient, spools: Spools, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ledger, "MAX_LINES", 1)
    batch = opening(spools, sealed_line(spools, "610"))
    response = registered_client.post(URL, json=batch)
    assert response.status_code == 422
    assert [(p["loc"][1:], p["type"]) for p in response.json()["detail"]] == [
        (["lines", 0, "quantity"], "one_package")
    ]


def test_an_opening_of_part_of_a_package_is_refused(
    registered_client: TestClient, spools: Spools
) -> None:
    batch = opening(spools, sealed_line(spools, "400"))
    response = registered_client.post(URL, json=batch)
    assert response.status_code == 422
    assert [(p["loc"][1:], p["type"]) for p in response.json()["detail"]] == [
        (["lines", 0, "quantity"], "not_whole_packages")
    ]


def test_an_opening_that_cannot_be_is_refused(
    registered_client: TestClient, spools: Spools
) -> None:
    """At one place, of packages: else FastAPI's 422, before anything."""
    batch = opening(spools, opened_line(spools))
    for change in (
        {"to_location_id": None},
        {"to_location_id": spools.van.id},
        {"lines": [{"item_id": spools.cable.id, "quantity": "1"}]},
    ):
        response = registered_client.post(URL, json=batch | change)
        assert response.status_code == 422, change
    same = spools.place.batch(to_location_id=spools.store.id)
    assert registered_client.post(URL, json=same).status_code == 422


def test_only_an_opening_names_an_open_package_key(
    registered_client: TestClient, spools: Spools
) -> None:
    batch = spools.place.batch(lines=[opened_line(spools)])
    assert registered_client.post(URL, json=batch).status_code == 422


def test_an_opening_the_server_cannot_take_is_refused_saying_why(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    """Never a 500, which the outbox would send again and again."""
    gone = opening(spools, opened_line(spools, package_id=999999))
    response = registered_client.post(URL, json=gone)
    assert response.status_code == 422
    assert [(p["loc"][1:], p["type"]) for p in response.json()["detail"]] == [
        (["lines", 0, "package_id"], "not_found")
    ]
    # A key used for another place: a rule only the write can see.
    key = uuid.uuid4()
    open_packages.make(session, key, spools.spool.id, spools.van.id)
    reused = opening(spools, opened_line(spools, open_package_key=str(key)))
    response = registered_client.post(URL, json=reused)
    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "refused"


def test_packages_the_page_thought_followed_open_loose_together(
    registered_client: TestClient, session: Session
) -> None:
    """The setting changed since the page loaded: two lines, one each,
    become one loose line, and sent again, are found written."""
    spools = Spools(session, tracked=False)
    batch = opening(spools, opened_line(spools), opened_line(spools))
    assert registered_client.post(URL, json=batch).status_code == 201
    assert registered_client.post(URL, json=batch).status_code == 200
    store = spools.store.id
    assert forms(session, spools.cable) == {(store, None, None): 610}


# Cutting from an open package, and moving one whole, on the scan page.


def test_cut_from_an_open_package_or_moved_whole_by_its_key(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    made = spools.opened()
    store, van = spools.store.id, spools.van.id
    cut = spools.place.batch(lines=[taken(made, "20")])
    assert registered_client.post(URL, json=cut).status_code == 201
    assert registered_client.post(URL, json=cut).status_code == 200
    assert forms(session, spools.cable)[(store, None, made.id)] == 285
    assert forms(session, spools.cable)[(van, None, None)] == 20
    whole = spools.place.batch(lines=[taken(made, "285", whole=True)])
    assert registered_client.post(URL, json=whole).status_code == 201
    session.refresh(made)
    assert made.location_id == van
    assert forms(session, spools.cable)[(van, None, made.id)] == 285
    # Cut at an install, from the van.
    install = spools.place.batch(
        kind="install",
        from_location_id=van,
        to_location_id=None,
        lines=[taken(made, "30")],
    )
    assert registered_client.post(URL, json=install).status_code == 201
    assert forms(session, spools.cable)[(van, None, made.id)] == 255


def test_taking_from_an_open_package_is_refused_saying_why(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    made = spools.opened()
    van = spools.van.id

    def refused(batch: dict[str, object]) -> list[tuple[list[object], str]]:
        response = registered_client.post(URL, json=batch)
        assert response.status_code == 422
        return [(p["loc"][1:], p["type"]) for p in response.json()["detail"]]

    unknown = taken(made, "1", open_package_key=str(uuid.uuid4()))
    assert refused(spools.place.batch(lines=[unknown])) == [
        (["lines", 0, "open_package_key"], "not_found")
    ]
    assert refused(spools.place.batch(lines=[taken(made, "5", whole=True)])) == [
        (["lines", 0, "quantity"], "open_package_whole")
    ]
    elsewhere = spools.place.batch(
        from_location_id=van, to_location_id=spools.store.id, lines=[taken(made, "1")]
    )
    assert refused(elsewhere) == [
        (["lines", 0, "open_package_key"], "open_package_elsewhere")
    ]
    closer = add(session, Admin(username=f"closer-{tag()}"))
    made.closed_at, made.closed_by_admin_id = datetime.now(UTC), closer.id
    session.flush()
    assert refused(spools.place.batch(lines=[taken(made, "1")])) == [
        (["lines", 0, "open_package_key"], "open_package_closed")
    ]


def test_a_line_taken_from_an_open_package_that_cannot_be_is_refused(
    registered_client: TestClient, spools: Spools
) -> None:
    made = spools.opened()
    store = spools.store.id
    for batch in (
        spools.place.batch(
            lines=[taken(made, "1", package_id=spools.spool.id, package_size="305")]
        ),
        spools.place.batch(
            kind="receipt",
            from_location_id=None,
            to_location_id=store,
            lines=[taken(made, "1")],
        ),
        spools.place.batch(
            kind="install",
            to_location_id=None,
            lines=[taken(made, "1", whole=True)],
        ),
        spools.place.batch(
            lines=[{"item_id": made.item_id, "quantity": "1", "whole": True}]
        ),
    ):
        assert registered_client.post(URL, json=batch).status_code == 422


def test_an_open_package_named_on_two_lines_is_refused_as_twice(
    registered_client: TestClient, spools: Spools
) -> None:
    made = spools.opened()
    both = spools.place.batch(lines=[taken(made, "305", whole=True), taken(made, "20")])
    response = registered_client.post(URL, json=both)
    assert response.status_code == 422
    assert [(p["loc"][1:], p["type"]) for p in response.json()["detail"]] == [
        (["lines", 0, "open_package_key"], "repeated"),
        (["lines", 1, "open_package_key"], "repeated"),
    ]
    whole_opening = opening(spools, opened_line(spools, whole=True))
    assert registered_client.post(URL, json=whole_opening).status_code == 422
