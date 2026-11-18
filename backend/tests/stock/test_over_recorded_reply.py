"""A batch changed after another tab recorded it under the same key
(inventory-tng-0gg): the 409 says what was recorded, and what it took off
a line already recorded waits for an administrator to correct it, or mark
it checked, under "Needs your action"."""

import uuid
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng.models.catalogue import ItemPackage
from inventory_tng.models.places import Location
from inventory_tng.models.stock import (
    AdjustmentReason,
    LedgerTransaction,
    Movement,
    OpenPackage,
)
from inventory_tng.models.volunteers import Volunteer
from inventory_tng.scan.batch import _more_written
from inventory_tng.stock import counts, over_recorded
from inventory_tng.stock.ledger import Entry, Form

from helpers.over_recorded import bagged, checks, new_admin_id, recorded_then_changed
from helpers.rows import add, tag
from helpers.stock import URL, Place, Spools, cut


def test_a_reused_key_is_told_what_was_recorded(
    registered_client: TestClient, place: Place, bags: ItemPackage
) -> None:
    first = place.batch(
        lines=[{"item_id": place.item.id, "quantity": "3"}, bagged(bags, 2)]
    )
    reply = recorded_then_changed(
        registered_client, first, lines=[{"item_id": place.item.id, "quantity": "4"}]
    )
    recorded = reply["recorded"]
    assert reply["reason"] == "key_reused"
    assert {
        key: recorded[key] for key in ("kind", "from_location_id", "to_location_id")
    } == {
        "kind": "move",
        "from_location_id": place.store.id,
        "to_location_id": place.van.id,
    }
    assert recorded["volunteer_id"] == place.volunteer.id
    lines = sorted(recorded["lines"], key=lambda line: line["item_id"])
    assert [
        (
            line["item_id"],
            Decimal(line["quantity"]),
            line["package_id"],
            line["package_size"],
        )
        for line in lines
    ] == sorted(
        [
            (place.item.id, Decimal(3), None, None),
            (place.cable.id, Decimal(100), bags.id, "50.000"),
        ]
    )
    assert (recorded["used_up"], recorded["lengths"]) == ([], [])


def test_what_a_batch_took_off_waits_for_an_administrator(
    registered_client: TestClient, session: Session, place: Place, bags: ItemPackage
) -> None:
    first = place.batch(
        lines=[{"item_id": place.item.id, "quantity": "3"}, bagged(bags, 2)]
    )
    with capture_logs() as logs:
        recorded_then_changed(
            registered_client,
            first,
            # One more radio, which is no check, and one bag fewer.
            lines=[{"item_id": place.item.id, "quantity": "4"}, bagged(bags, 1)],
        )
    (check,) = checks(session)
    movement = session.get_one(Movement, check.movement_id)
    assert (movement.item_id, check.quantity, check.settled_at) == (
        place.cable.id,
        Decimal(50),
        None,
    )
    assert any(
        line["event"] == "over recorded raised" and line["over_recorded_id"] == check.id
        for line in logs
    )
    # Nothing of the changed batch was written.
    assert (
        session.scalar(
            select(LedgerTransaction.id).where(
                LedgerTransaction.idempotency_key == uuid.UUID(first["key"])
            )
        )
        == movement.transaction_id
    )


def test_a_line_taken_off_whole_waits_whole(
    registered_client: TestClient, session: Session, place: Place, bags: ItemPackage
) -> None:
    first = place.batch(
        lines=[{"item_id": place.item.id, "quantity": "3"}, bagged(bags, 2)]
    )
    recorded_then_changed(
        registered_client, first, lines=[{"item_id": place.item.id, "quantity": "3"}]
    )
    assert [check.quantity for check in checks(session)] == [100]


def test_sent_to_another_place_every_line_recorded_waits(
    registered_client: TestClient, session: Session, place: Place
) -> None:
    other = add(session, Location(name=f"Van {tag()}", kind="vehicle"))
    recorded_then_changed(registered_client, place.batch(), to_location_id=other.id)
    assert [check.quantity for check in checks(session)] == [3]


def test_another_volunteer_alone_takes_nothing_off(
    registered_client: TestClient, session: Session, place: Place
) -> None:
    other = add(session, Volunteer(name=f"Volunteer {tag()}"))
    reply = recorded_then_changed(
        registered_client, place.batch(), volunteer_id=other.id
    )
    assert reply["recorded"]["volunteer_id"] == place.volunteer.id
    assert checks(session) == []


def test_said_again_while_waiting_it_says_the_newest_and_settled_stays(
    registered_client: TestClient, session: Session, place: Place
) -> None:
    first = place.batch(lines=[{"item_id": place.item.id, "quantity": "5"}])

    def less(n: str) -> None:
        lines = [{"item_id": place.item.id, "quantity": n}]
        response = registered_client.post(
            URL, json=first | {"lines": lines, "changed_from": first["lines"]}
        )
        assert response.status_code == 409

    assert registered_client.post(URL, json=first).status_code == 201
    less("4")
    less("2")
    (check,) = checks(session)
    assert check.quantity == 3
    over_recorded.mark_checked(
        session, check.id, over_recorded.seen(check), new_admin_id(session), "both"
    )
    less("1")
    (check,) = checks(session)
    assert (check.quantity, check.settled_how) == (3, "checked")


def test_an_opening_and_a_cut_are_said_by_their_open_packages(
    registered_client: TestClient, session: Session
) -> None:
    spools = Spools(session)
    made = spools.opened()
    first = spools.place.batch(lines=[cut(made, "20")])
    reply = recorded_then_changed(registered_client, first, lines=[cut(made, "15")])
    (line,) = reply["recorded"]["lines"]
    assert (line["open_package_key"], line["package_id"], line["whole"]) == (
        str(made.key),
        None,
        None,
    )
    assert [check.quantity for check in checks(session)] == [5]
    opening = {
        "kind": "open",
        "from_location_id": spools.store.id,
        "to_location_id": spools.store.id,
    }
    key = str(uuid.uuid4())
    opened = spools.place.batch(
        key=key,
        lines=[
            {
                "item_id": spools.cable.id,
                "quantity": "305",
                "package_id": spools.spool.id,
                "package_size": "305",
                "open_package_key": key,
            }
        ],
        **opening,
    )
    refused_key = uuid.uuid4()
    reply = recorded_then_changed(
        registered_client,
        opened,
        install_number=None,
        lines=[opened["lines"][0] | {"open_package_key": str(refused_key)}],
    )
    (line,) = reply["recorded"]["lines"]
    assert (line["package_id"], line["open_package_key"]) == (spools.spool.id, key)
    # Nothing of the refused opening is kept: no open package under its key.
    session.expire_all()
    assert (
        session.scalar(select(OpenPackage.id).where(OpenPackage.key == refused_key))
        is None
    )


def test_a_line_moved_whole_is_said_whole(
    registered_client: TestClient, session: Session
) -> None:
    spools = Spools(session)
    made = spools.opened()
    first = spools.place.batch(lines=[cut(made, "305") | {"whole": True}])
    reply = recorded_then_changed(registered_client, first, lines=[cut(made, "5")])
    (line,) = reply["recorded"]["lines"]
    assert line["whole"] is True


def test_only_the_lines_less_was_sent_of_are_over() -> None:
    sealed, loose = Form(package_id=7), Form()
    written = {
        1: Entry(1, Decimal(100), sealed, sealed),
        2: Entry(2, Decimal(3)),
        3: Entry(3, Decimal(2)),
    }
    sent = [
        Entry(1, Decimal(50), sealed, sealed),
        # The same item loose is another line.
        Entry(1, Decimal(50), loose, loose),
        Entry(2, Decimal(4)),
    ]
    assert _more_written(written, sent, list(written.values())) == {
        1: Decimal(50),
        3: Decimal(2),
    }
    # Only what it took off what it started from: another tab's addition,
    # or the line another tab added, is no check.
    started = [Entry(1, Decimal(100), sealed, sealed), Entry(2, Decimal(1))]
    assert _more_written(written, sent, started) == {1: Decimal(50)}
    assert _more_written(written, sent, None) == {}


def test_lines_of_one_item_in_the_same_forms_are_over_once_together() -> None:
    """Two lines of the same item and forms (a batch may send them) are
    taken off together: 5 and 5 recorded, started from 5, sent none, is 5
    over, not 10."""
    written = {1: Entry(1, Decimal(5)), 2: Entry(1, Decimal(5))}
    assert _more_written(written, [], [Entry(1, Decimal(5))]) == {1: Decimal(5)}
    # Started from both, sent 6: 4 over, from the first then the second.
    started = list(written.values())
    assert _more_written(written, [Entry(1, Decimal(6))], started) == {1: Decimal(4)}
    assert _more_written(written, [Entry(1, Decimal(2))], started) == {
        1: Decimal(5),
        2: Decimal(3),
    }


def test_a_batch_older_than_the_one_recorded_takes_nothing_off(
    registered_client: TestClient, session: Session, place: Place, bags: ItemPackage
) -> None:
    """Another tab put the cart back, changed it and recorded it first: this
    batch, from before that change, sent after it (a tab of a build before,
    which sends no changed_from), took nothing off. Its difference is that
    tab's change, which moved (inventory-tng-0gg review)."""
    original = [{"item_id": place.item.id, "quantity": "3"}, bagged(bags, 1)]
    newer = place.batch(
        lines=[{"item_id": place.item.id, "quantity": "5"}, bagged(bags, 2)]
    )
    reply = recorded_then_changed(
        registered_client, newer, lines=original, changed_from=None
    )
    assert reply["recorded"] is not None
    assert checks(session) == []


def test_only_what_this_batch_took_off_since_it_was_put_back_waits(
    registered_client: TestClient, session: Session, place: Place, bags: ItemPackage
) -> None:
    """Put back with 3 radios and 1 bag; another tab raised the bags to 2
    and recorded that; this one took the radios down to 1: only the 2 radios
    wait, not the bag the other tab added."""
    put_back = [{"item_id": place.item.id, "quantity": "3"}, bagged(bags, 1)]
    other = place.batch(lines=[put_back[0], bagged(bags, 2)])
    recorded_then_changed(
        registered_client,
        other,
        lines=[{"item_id": place.item.id, "quantity": "1"}, bagged(bags, 1)],
        changed_from=put_back,
    )
    ((check, item_id),) = [
        (each, session.get_one(Movement, each.movement_id).item_id)
        for each in checks(session)
    ]
    assert (item_id, check.quantity) == (place.item.id, 2)


def test_the_reply_to_another_client_leaves_out_nothing_it_holds(
    registered_client: TestClient, session: Session, place: Place
) -> None:
    """A count's key reused by a batch: nothing a volunteer recorded."""
    key = uuid.uuid4()
    reason = add(session, AdjustmentReason(name=f"Reason {tag()}"))
    counts.record_adjustment(
        session,
        key,
        new_admin_id(session),
        place.store.id,
        place.item.id,
        Decimal(1),
        found=True,
        reason_id=reason.id,
    )
    response = registered_client.post(URL, json=place.batch(key=str(key)))
    assert (response.status_code, response.json()) == (
        409,
        {"reason": "key_reused", "recorded": None},
    )
