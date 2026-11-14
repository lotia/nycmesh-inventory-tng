"""A batch changed after another tab recorded it under the same key
(inventory-tng-0gg): the 409 says what was recorded, and what it took off
a line already recorded waits for an administrator to correct it, or mark
it checked, under "Needs your action"."""

import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng import ledger, over_recorded
from inventory_tng.db import RuleBroken
from inventory_tng.ledger import Entry, Form, _more_written
from inventory_tng.models import (
    AdjustmentReason,
    ItemPackage,
    LedgerTransaction,
    Location,
    Movement,
    OverRecorded,
    Volunteer,
)

from helpers.concurrency import Committed
from helpers.rows import add, tag
from helpers.sign_in import new_admin, text
from helpers.stock import Place, Spools, cut, write

URL = "/api/transactions"
PAGE = "/admin/stock/recorded-more"


@pytest.fixture
def place(session: Session) -> Place:
    return Place(session)


@pytest.fixture
def bags(session: Session, place: Place) -> ItemPackage:
    """Bags of 50 of the place's cable."""
    return add(session, ItemPackage(item_id=place.cable.id, name="Bag of 50", size=50))


def bagged(bags: ItemPackage, n: int) -> dict[str, object]:
    return {
        "item_id": bags.item_id,
        "quantity": str(50 * n),
        "package_id": bags.id,
        "package_size": "50",
    }


def checks(session: Session) -> list[OverRecorded]:
    session.expire_all()
    return list(session.scalars(select(OverRecorded).order_by(OverRecorded.id)))


def recorded_then_changed(
    client: TestClient, first: dict[str, Any], **changed: object
) -> dict[str, Any]:
    """`first` recorded, then sent again changed under its key: the 409."""
    assert client.post(URL, json=first).status_code == 201
    response = client.post(URL, json=first | changed)
    assert response.status_code == 409
    return response.json()


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
        response = registered_client.post(URL, json=first | {"lines": lines})
        assert response.status_code == 409

    assert registered_client.post(URL, json=first).status_code == 201
    less("4")
    less("2")
    (check,) = checks(session)
    assert check.quantity == 3
    over_recorded.mark_checked(
        session, check.id, over_recorded.seen(check), admin_id(session)
    )
    less("1")
    (check,) = checks(session)
    assert (check.quantity, check.settled_how) == (3, "checked")


def admin_id(session: Session) -> int:
    admin, _ = new_admin(session, f"ada-{tag()}", totp=False)
    return admin.id


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
    reply = recorded_then_changed(
        registered_client,
        opened,
        install_number=None,
        lines=[opened["lines"][0] | {"open_package_key": str(uuid.uuid4())}],
    )
    (line,) = reply["recorded"]["lines"]
    assert (line["package_id"], line["open_package_key"]) == (spools.spool.id, key)


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
    assert _more_written(written, sent) == {1: Decimal(50), 3: Decimal(2)}


def test_the_reply_to_another_client_leaves_out_nothing_it_holds(
    registered_client: TestClient, session: Session, place: Place
) -> None:
    """A count's key reused by a batch: nothing a volunteer recorded."""
    key = uuid.uuid4()
    reason = add(session, AdjustmentReason(name=f"Reason {tag()}"))
    ledger.record_adjustment(
        session,
        key,
        admin_id(session),
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


# The administrators' page.


def raised(client: TestClient, place: Place, bags: ItemPackage) -> None:
    """Two bags recorded, then one: one bag waits."""
    first = place.batch(lines=[bagged(bags, 2)])
    recorded_then_changed(client, first, lines=[bagged(bags, 1)])


def test_needs_your_action_lists_it(
    admin_client: TestClient,
    registered_client: TestClient,
    place: Place,
    bags: ItemPackage,
) -> None:
    raised(registered_client, place, bags)
    home = text(admin_client.get("/admin"))
    assert "Recorded more than moved" in home
    assert f"{place.cable.name}: 1 \N{MULTIPLICATION SIGN} Bag of 50" in home
    page = text(admin_client.get(f"{PAGE}?item={place.cable.id}"))
    assert f"from {place.store.name} to {place.van.name}" in page
    assert "1 \N{MULTIPLICATION SIGN} Bag of 50" in page


def test_correcting_one_undoes_it_on_each_side(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
    bags: ItemPackage,
) -> None:
    raised(registered_client, place, bags)
    (check,) = checks(session)
    form = {"seen": over_recorded.seen(check)}
    with capture_logs() as logs:
        response = admin_client.post(f"{PAGE}/{check.id}/correct", data=form)
    assert response.status_code == 303
    assert "over_recorded_corrected" in response.headers["location"]
    (check,) = checks(session)
    assert check.settled_how == "corrected"
    # The bag found again in the store, written off from the van, sealed.
    adjusted = session.execute(
        select(
            LedgerTransaction.from_location_id,
            LedgerTransaction.to_location_id,
            Movement.quantity,
            Movement.from_package_id,
            Movement.to_package_id,
        )
        .join(Movement, Movement.transaction_id == LedgerTransaction.id)
        .where(LedgerTransaction.kind == "adjustment")
    ).all()
    assert sorted(adjusted, key=str) == sorted(
        [
            (None, place.store.id, Decimal(50), None, bags.id),
            (place.van.id, None, Decimal(50), bags.id, None),
        ],
        key=str,
    )
    assert any(line["event"] == "over recorded corrected" for line in logs)
    # Sent again, as a second tap would: nothing more.
    again = admin_client.post(f"{PAGE}/{check.id}/correct", data=form)
    assert "over_recorded_unchanged" in again.headers["location"]
    assert "Recorded more than moved" not in text(admin_client.get("/admin"))


def test_marking_one_checked_writes_nothing(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
    bags: ItemPackage,
) -> None:
    raised(registered_client, place, bags)
    (check,) = checks(session)
    response = admin_client.post(
        f"{PAGE}/{check.id}/checked", data={"seen": over_recorded.seen(check)}
    )
    assert "over_recorded_checked" in response.headers["location"]
    assert checks(session)[0].settled_how == "checked"
    assert (
        session.scalar(
            select(LedgerTransaction.id).where(LedgerTransaction.kind == "adjustment")
        )
        is None
    )


def test_one_said_again_since_it_was_shown_is_not_settled(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
    bags: ItemPackage,
) -> None:
    raised(registered_client, place, bags)
    (check,) = checks(session)
    response = admin_client.post(f"{PAGE}/{check.id}/checked", data={"seen": "2"})
    assert "over_recorded_unchanged" in response.headers["location"]
    assert checks(session)[0].settled_at is None


def test_only_its_answers_are_taken(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
    bags: ItemPackage,
) -> None:
    raised(registered_client, place, bags)
    (check,) = checks(session)
    assert admin_client.post(f"{PAGE}/{check.id}/delete").status_code == 404
    assert admin_client.post(f"{PAGE}/{check.id + 1000}/checked").status_code == 404


@pytest.mark.concurrency
def test_one_answered_meanwhile_waits_then_is_refused(own_committed: Committed) -> None:
    """Two administrators answering it at once: the second waits for the
    first's row lock, then finds it settled. On a database of its own, as
    the check's line is in the ledger."""
    setup = own_committed.session()
    place = Place(setup)
    transaction = write(setup, place)
    movement_id = setup.scalar(
        select(Movement.id).where(Movement.transaction_id == transaction.id)
    )
    assert movement_id is not None
    over_recorded.raise_checks(setup, {movement_id: Decimal(1)})
    admin = admin_id(setup)
    setup.commit()
    (check,) = checks(setup)
    first, second = own_committed.session(), own_committed.session()
    over_recorded.mark_checked(first, check.id, over_recorded.seen(check), admin)
    worker = own_committed.blocked(
        second,
        lambda: over_recorded.mark_checked(
            second, check.id, over_recorded.seen(check), admin
        ),
    )
    first.commit()
    finished = worker.finished()
    second.rollback()
    assert isinstance(finished.error, RuleBroken)
    assert finished.error.rule == "over_recorded_unchanged"


def test_an_install_corrected_is_found_again_where_it_left(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
) -> None:
    """An install has no place it arrives at: one adjustment only."""
    first = place.batch(kind="install", to_location_id=None, install_number=12)
    recorded_then_changed(
        registered_client, first, lines=[{"item_id": place.item.id, "quantity": "1"}]
    )
    (check,) = checks(session)
    admin_client.post(
        f"{PAGE}/{check.id}/correct", data={"seen": over_recorded.seen(check)}
    )
    adjusted = session.execute(
        select(LedgerTransaction.to_location_id, Movement.quantity)
        .join(Movement, Movement.transaction_id == LedgerTransaction.id)
        .where(LedgerTransaction.kind == "adjustment")
    ).all()
    assert adjusted == [(place.store.id, Decimal(2))]


def test_a_cut_taken_down_is_listed_from_its_open_package(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    spools = Spools(session)
    made = spools.opened()
    first = spools.place.batch(lines=[cut(made, "20")])
    recorded_then_changed(registered_client, first, lines=[cut(made, "15")])
    page = text(admin_client.get(PAGE))
    assert "5 m from an open package" in page
