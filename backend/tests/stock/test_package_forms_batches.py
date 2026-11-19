"""Sealed packages in the scan page's batches (POST /api/transactions,
docs/decisions/0020): saved, they arrive sealed, and a line of a package
or a size that cannot be is refused."""

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.models.catalogue import ItemPackage
from inventory_tng.scan.batch_schemas import BodyRefusedReply, SizeProblem

from helpers.package_forms import quantities_by_form
from helpers.rows import add, tag
from helpers.stock import URL, Spools, sealed_line

# The scan page's batches (POST /api/transactions): sealed packages stay
# sealed.


def test_sealed_packages_saved_arrive_sealed(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    store, van, spool = spools.store.id, spools.van.id, spools.spool.id
    lines = [sealed_line(spools, "305"), {"item_id": spools.cable.id, "quantity": "5"}]
    batch = spools.place.batch(lines=lines)
    assert registered_client.post(URL, json=batch).status_code == 201
    assert registered_client.post(URL, json=batch).status_code == 200
    assert quantities_by_form(session, spools.cable) == {
        (store, spool, None): 305,
        (store, None, None): -5,
        (van, spool, None): 305,
        (van, None, None): 5,
    }
    other = batch | {"lines": [sealed_line(spools, "610")]}
    assert registered_client.post(URL, json=other).status_code == 409
    receipt = spools.place.batch(
        kind="receipt",
        from_location_id=None,
        to_location_id=store,
        lines=[sealed_line(spools, "305")],
    )
    assert registered_client.post(URL, json=receipt).status_code == 201
    assert quantities_by_form(session, spools.cable)[(store, spool, None)] == 610


def test_a_batch_saved_before_packages_were_kept_sealed_saves_loose(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    """An outbox's batch from an older page, of units alone."""
    lines = [{"item_id": spools.cable.id, "quantity": "305"}]
    response = registered_client.post(URL, json=spools.place.batch(lines=lines))
    assert response.status_code == 201
    assert quantities_by_form(session, spools.cable)[(spools.van.id, None, None)] == 305


def test_a_line_sent_in_a_size_its_package_no_longer_has_is_refused(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    """A batch built before its package was resized, waiting in an outbox
    (inventory-tng-5f7): its count of boxes is of the old size, so it is
    refused, not saved as another number of them; an opening's too."""
    box = add(session, ItemPackage(item_id=spools.cable.id, name=f"B {tag()}", size=50))
    line = {"item_id": spools.cable.id, "quantity": "50", "package_id": box.id}
    built = spools.place.batch(lines=[line | {"package_size": "50"}])
    opening = spools.place.batch(
        kind="open",
        to_location_id=spools.store.id,
        lines=[sealed_line(spools, "305") | {"package_size": "305"}],
    )
    box.size = Decimal(25)
    spools.spool.size = Decimal(300)
    session.flush()
    for batch in (built, opening):
        response = registered_client.post(URL, json=batch)
        assert response.status_code == 422
        assert [(p["loc"][1:], p["type"]) for p in response.json()["detail"]] == [
            (["lines", 0, "package_size"], "package_resized")
        ]
    assert (spools.van.id, box.id, None) not in quantities_by_form(
        session, spools.cable
    )
    # Its rules read in the size it was counted in: a part of a unit is
    # still said, and an opening is not too many packages in the new size.
    radios = add(
        session, ItemPackage(item_id=spools.place.item.id, name="Pack", size=4)
    )
    halves = {
        "item_id": spools.place.item.id,
        "quantity": "2.5",
        "package_id": radios.id,
    }
    many = sealed_line(spools, "610") | {"package_size": "305"}
    spools.spool.size = Decimal(1)
    session.flush()
    for batch, problems in (
        (
            spools.place.batch(lines=[halves | {"package_size": "0.5"}]),
            [
                (["lines", 0, "package_size"], "package_resized"),
                (["lines", 0, "quantity"], "not_whole"),
            ],
        ),
        (
            spools.place.batch(
                kind="open", to_location_id=spools.store.id, lines=[many]
            ),
            [(["lines", 0, "package_size"], "package_resized")],
        ),
    ):
        response = registered_client.post(URL, json=batch)
        assert [
            (p["loc"][1:], p["type"]) for p in response.json()["detail"]
        ] == problems
    # In the size it has: saved.
    now = spools.place.batch(lines=[line | {"package_size": "25.000"}])
    assert registered_client.post(URL, json=now).status_code == 201


def test_a_size_is_said_only_of_a_package_of_the_lines_item(
    registered_client: TestClient, spools: Spools
) -> None:
    """Another item's package is not found, whatever size it is said to be;
    a size said of no package, or a package without one (an outbox's batch
    from before inventory-tng-5f7, on a dev phone: inventory-tng-1p8), is
    refused as a page's bug, on the line's package_size; a package_id
    refused already, on that alone."""
    other = sealed_line(spools, "300") | {
        "item_id": spools.place.item.id,
        "package_size": "1",
    }
    response = registered_client.post(URL, json=spools.place.batch(lines=[other]))
    assert [(p["loc"][1:], p["type"]) for p in response.json()["detail"]] == [
        (["lines", 0, "package_id"], "not_found")
    ]
    # On package_size itself, in words naming both fields, for whoever
    # writes a client; the scan page says it in its own (outbox-notes.ts).
    missing = (
        "package_size_required",
        (
            "package_size is required when package_id is given: the size, in "
            "the item's unit, the package was counted in, from the catalogue"
        ),
    )
    unsized = {
        k: v for k, v in sealed_line(spools, "305").items() if k != "package_size"
    }
    cases = [
        (unsized, missing),
        (unsized | {"package_size": None}, missing),
        (
            {"item_id": spools.cable.id, "quantity": "5", "package_size": "5"},
            (
                "package_size_without_package",
                "package_size is only allowed with package_id",
            ),
        ),
    ]
    for line, (kind, words) in cases:
        response = registered_client.post(URL, json=spools.place.batch(lines=[line]))
        assert response.status_code == 422
        # As the schema says it: a SizeProblem, of a kind the page words.
        refused = BodyRefusedReply.model_validate(response.json())
        assert [(type(p), p.loc[1:], p.type, p.msg) for p in refused.detail] == [
            (SizeProblem, ["lines", 0, "package_size"], kind, words)
        ]
    bad = sealed_line(spools, "305") | {"package_id": 0}
    response = registered_client.post(URL, json=spools.place.batch(lines=[bad]))
    assert response.status_code == 422
    assert [(p["loc"][1:], p["type"]) for p in response.json()["detail"]] == [
        (["lines", 0, "package_id"], "greater_than")
    ]


def test_a_refusal_names_each_bad_package_line(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    other = add(
        session, ItemPackage(item_id=spools.place.item.id, name=f"Box {tag()}", size=10)
    )
    lines = [
        sealed_line(spools, "300"),
        {
            "item_id": spools.cable.id,
            "quantity": "10",
            "package_id": other.id,
            "package_size": "10",
        },
        sealed_line(spools, "305"),
        {"item_id": spools.cable.id, "quantity": "1"},
    ]
    response = registered_client.post(URL, json=spools.place.batch(lines=lines))
    assert response.status_code == 422
    assert [(p["loc"][1:], p["type"]) for p in response.json()["detail"]] == [
        (["lines", 0, "item_id"], "repeated"),
        (["lines", 0, "quantity"], "not_whole_packages"),
        (["lines", 1, "package_id"], "not_found"),
        (["lines", 2, "item_id"], "repeated"),
    ]


def test_a_retired_package_is_never_received(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    """As a retired item is not (docs/decisions/0003)."""
    spools.spool.retired_at = datetime.now(UTC)
    session.flush()
    receipt = spools.place.batch(
        kind="receipt",
        from_location_id=None,
        to_location_id=spools.store.id,
        lines=[sealed_line(spools, "305")],
    )
    response = registered_client.post(URL, json=receipt)
    assert response.status_code == 422
    assert [(p["loc"][1:], p["type"]) for p in response.json()["detail"]] == [
        (["lines", 0, "package_id"], "retired_package")
    ]
