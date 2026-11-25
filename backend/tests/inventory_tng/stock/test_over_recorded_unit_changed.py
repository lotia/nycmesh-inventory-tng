"""A line recorded with more than was moved, in m, its check raised after
its item changed to ft (inventory-tng-y50b): a batch sent again under its
key once the change was saved. Correct and a corrected count write in ft,
sealed spools by how many, the rest exactly (inventory-tng-4fjq)."""

from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.catalogue.items_routes import RECORDED, RECORDED_IN
from inventory_tng.catalogue.paths import ITEMS
from inventory_tng.models.catalogue import Item
from inventory_tng.stock import over_recorded

from helpers.items import shown_item_form
from helpers.over_recorded import (
    adjustment_units,
    adjustments,
    checks,
    correct,
    counted,
)
from helpers.stock import (
    URL,
    Place,
    Spools,
    cut,
    held_by_form,
    received,
    sealed_line,
)


def _to_ft(admin_client: TestClient, item: Item, **fields: str) -> None:
    """The item changed to ft, answering Yes: its amounts were in m."""
    form = shown_item_form(item, unit="ft", **fields)
    answered = admin_client.post(
        f"{ITEMS}/{item.id}", data=form | {RECORDED: "yes", RECORDED_IN: "m"}
    )
    assert answered.status_code == 303


def _sent_again_after_ft(
    session: Session,
    client: TestClient,
    admin_client: TestClient,
    item: Item,
    first: dict[str, Any],
    lines: list[dict[str, object]],
    **fields: str,
) -> None:
    """`first` recorded in m, the item changed to ft, then `first` sent
    again with `lines` under its key: its check is raised in m."""
    assert client.post(URL, json=first).status_code == 201
    _to_ft(admin_client, item, **fields)
    again = first | {"changed_from": first["lines"], "lines": lines}
    assert client.post(URL, json=again).status_code == 409
    assert [check.unit for check in checks(session)] == ["m"]


def test_sealed_spools_are_corrected_by_how_many(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Two spools of 305 m moved, then one: one spool is found again in
    the store and written off the van, each 1000.656 ft, the spool's size
    in ft as kept, so each end holds a whole spool, not a fraction over."""
    spools = Spools(session)
    lines = [sealed_line(spools, "610")]
    first = spools.place.batch(lines=lines)
    _sent_again_after_ft(
        session,
        registered_client,
        admin_client,
        spools.cable,
        first,
        [lines[0] | {"quantity": "305"}],
        track_opened="on",
    )
    response = correct(admin_client, session)
    assert "over_recorded_corrected" in response.headers["location"]
    spool = Decimal("1000.656")
    assert [row[2] for row in adjustments(session)] == [spool, spool]
    assert adjustment_units(session) == ["ft", "ft"]
    for place in (spools.store, spools.van):
        assert held_by_form(session, place.id, spools.cable.id) == {
            (spools.cable.id, spools.sealed): spool
        }


def test_loose_units_are_corrected_exactly(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """5 m moved, then 2 m: 3 m is 9.843 ft (9.8425...)."""
    place = Place(session)
    cable = place.cable
    received(session, place, [{"item_id": cable.id, "quantity": "10", "unit": "m"}])
    line = {"item_id": cable.id, "quantity": "5", "unit": "m"}
    _sent_again_after_ft(
        session,
        registered_client,
        admin_client,
        cable,
        place.batch(lines=[line]),
        [line | {"quantity": "2"}],
    )
    correct(admin_client, session)
    (check,) = checks(session)
    assert check.settled_how == "corrected"
    assert [row[2] for row in adjustments(session)] == [Decimal("9.843")] * 2
    assert adjustment_units(session) == ["ft", "ft"]


def test_a_count_corrected_is_typed_in_m_and_written_in_ft(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """An open spool of 305 m moved whole, then left out: its count is
    asked in m, the line's unit, as the page shows it, and 180 m written
    off the van as 125 m, 410.105 ft (410.1049...)."""
    spools = Spools(session)
    made = spools.opened()
    loose: dict[str, object] = {
        "item_id": spools.place.item.id,
        "quantity": "1",
        "unit": "each",
    }
    first = spools.place.batch(lines=[cut(made, "305") | {"whole": True}, loose])
    _sent_again_after_ft(
        session,
        registered_client,
        admin_client,
        spools.cable,
        first,
        [loose],
        track_opened="on",
    )
    _, response = counted(admin_client, session, "180")
    assert "over_recorded_count_corrected" in response.headers["location"]
    assert adjustments(session) == [
        (spools.van.id, None, Decimal("410.105"), made.id, None, over_recorded.REASON)
    ]
    assert adjustment_units(session) == ["ft"]
