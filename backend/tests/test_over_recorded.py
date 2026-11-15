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

from inventory_tng import adjustment_reasons, ledger, over_recorded
from inventory_tng.db import RuleBroken
from inventory_tng.ledger import Entry, Form, _more_written
from inventory_tng.models import (
    AdjustmentReason,
    ItemPackage,
    LedgerTransaction,
    Location,
    Movement,
    OpenPackage,
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
    """`first` recorded, then sent again changed under its key, as a page
    sends a cart put back with `first`'s lines (changed_from, unless
    `changed` says otherwise): the 409."""
    assert client.post(URL, json=first).status_code == 201
    response = client.post(URL, json=first | {"changed_from": first["lines"]} | changed)
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
    assert "Corrected." in text(admin_client.get(response.headers["location"]))
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
    assert (
        "Not changed: someone answered or changed it meanwhile. Reload to see it "
        "as it is." in text(admin_client.get(again.headers["location"]))
    )
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
    assert "Marked checked." in text(admin_client.get(response.headers["location"]))
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


def test_what_a_batch_was_changed_from_does_not_make_it_another(
    registered_client: TestClient, place: Place
) -> None:
    """Sent again with its lines as recorded, it is the same batch, whatever
    it says it was changed from."""
    first = place.batch()
    created = registered_client.post(URL, json=first)
    assert created.status_code == 201
    again = registered_client.post(
        URL,
        json=first | {"changed_from": [{"item_id": place.item.id, "quantity": "9"}]},
    )
    assert again.status_code == 200
    assert again.json() == created.json()


def moved_whole(client: TestClient, spools: Spools) -> OpenPackage:
    """A spool's open package moved whole from the store to the van and
    recorded, then sent again without it: it waits, whole."""
    made = spools.opened()
    whole = cut(made, "305") | {"whole": True}
    loose = {"item_id": spools.place.item.id, "quantity": "1"}
    recorded_then_changed(
        client, spools.place.batch(lines=[whole, loose]), lines=[loose]
    )
    return made


def counted(
    admin_client: TestClient, session: Session, count: str, **form: str
) -> tuple[OverRecorded, Any]:
    """The one waiting, and the answer to its count sent as `count`."""
    (check,) = checks(session)
    data = {"seen": over_recorded.seen(check), "count": count} | form
    return check, admin_client.post(f"{PAGE}/{check.id}/count", data=data)


def adjustments(session: Session) -> list[tuple[object, ...]]:
    return [
        tuple(each)
        for each in session.execute(
            select(
                LedgerTransaction.from_location_id,
                LedgerTransaction.to_location_id,
                Movement.quantity,
                Movement.from_open_package_id,
                Movement.to_open_package_id,
                AdjustmentReason.name,
            )
            .join(Movement, Movement.transaction_id == LedgerTransaction.id)
            .join(AdjustmentReason, AdjustmentReason.id == LedgerTransaction.reason_id)
            .where(LedgerTransaction.kind == "adjustment")
        )
    ]


def test_one_moved_whole_from_an_open_package_asks_for_its_count(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """The open package is now where it arrived, so it cannot be found again
    where it left: its count there is asked for, filled with what it held
    as it moved, under one button (Ali, 2026-11-15)."""
    spools = Spools(session)
    moved_whole(registered_client, spools)
    page = admin_client.get(f"{PAGE}?item={spools.cable.id}").text
    (check,) = checks(session)
    assert (
        f"An open package moved to {spools.van.name}. Confirm or correct count."
        in text(admin_client.get(PAGE))
    )
    assert "Count in the package" in page
    assert 'value="305"' in page
    assert f'<p class="hint" id="count-{check.id}-was" hidden>Was 305 m</p>' in page
    assert "Confirm count" in page
    assert "/checked" not in page
    assert "/correct" not in page


def test_its_count_confirmed_as_it_was_writes_nothing(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    spools = Spools(session)
    moved_whole(registered_client, spools)
    with capture_logs() as logs:
        _, response = counted(admin_client, session, "305")
    assert "over_recorded_confirmed" in response.headers["location"]
    assert checks(session)[0].settled_how == "checked"
    assert adjustments(session) == []
    assert any(line["event"] == "over recorded confirmed" for line in logs)
    assert "Confirmed." in text(admin_client.get(response.headers["location"]))


def test_its_count_corrected_adjusts_it_where_it_arrived(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Written off at the van, from the open package, for the difference."""
    spools = Spools(session)
    made = moved_whole(registered_client, spools)
    with capture_logs() as logs:
        _, response = counted(admin_client, session, "180")
    assert "over_recorded_count_corrected" in response.headers["location"]
    assert checks(session)[0].settled_how == "corrected"
    assert adjustments(session) == [
        (spools.van.id, None, Decimal(125), made.id, None, over_recorded.REASON)
    ]
    assert any(line["event"] == "over recorded count corrected" for line in logs)
    assert "Count corrected." in text(admin_client.get(response.headers["location"]))
    # Sent again, as a second tap would: nothing more.
    _, again = counted(admin_client, session, "180", seen="305")
    assert "over_recorded_unchanged" in again.headers["location"]
    assert len(adjustments(session)) == 1


def test_its_count_corrected_under_a_key_written_already_says_so(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """The notice says how it was settled, not whether anything new was
    written: its adjustment's key written already still corrects it."""
    spools = Spools(session)
    made = moved_whole(registered_client, spools)
    (check,) = checks(session)
    movement = session.get_one(Movement, check.movement_id)
    transaction = session.get_one(LedgerTransaction, movement.transaction_id)
    key = uuid.uuid5(transaction.idempotency_key, f"over recorded {check.id} count")
    admin, _ = new_admin(session, f"ada-{tag()}", totp=False)
    reason = adjustment_reasons.seeded(session, over_recorded.REASON)
    written = ledger.record_adjustment(
        session,
        key,
        admin.id,
        spools.van.id,
        spools.cable.id,
        Decimal(125),
        found=False,
        reason_id=reason.id,
        form=Form(open_package_id=made.id),
    )
    assert written
    session.commit()
    _, response = counted(admin_client, session, "180")
    assert "over_recorded_count_corrected" in response.headers["location"]
    assert checks(session)[0].settled_how == "corrected"


def test_its_count_raised_is_found_in_it(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    spools = Spools(session)
    made = moved_whole(registered_client, spools)
    counted(admin_client, session, "310")
    assert adjustments(session) == [
        (None, spools.van.id, Decimal(5), None, made.id, over_recorded.REASON)
    ]


def test_a_count_not_a_number_is_asked_again(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    spools = Spools(session)
    moved_whole(registered_client, spools)
    for typed, problem in (("lots", "Type a number"), ("-1", "Use 0 or more.")):
        check, response = counted(admin_client, session, typed)
        assert response.status_code == 400
        page = response.text
        assert problem in page
        assert f'value="{typed}"' in page
        # Edited, what it held shows under it.
        assert f'<p class="hint" id="count-{check.id}-was">Was 305 m</p>' in page
    assert checks(session)[0].settled_at is None
    assert adjustments(session) == []


def test_a_count_is_taken_only_for_an_open_package_moved_whole(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
    bags: ItemPackage,
) -> None:
    raised(registered_client, place, bags)
    with capture_logs() as logs:
        _, response = counted(admin_client, session, "50")
    assert "over_recorded_unchanged" in response.headers["location"]
    assert checks(session)[0].settled_at is None
    assert adjustments(session) == []
    refused = [line for line in logs if line["event"] == "over recorded not settled"]
    assert refused[0]["rule"] == "over_recorded_not_whole"


def test_the_page_says_what_it_is_for(admin_client: TestClient) -> None:
    page = " ".join(text(admin_client.get(PAGE)).split())
    assert (
        "Another tab recorded these before a volunteer changed them, so the "
        "inventory shows more moved than was. Correct moves the extra back with "
        "an adjustment. Mark checked if you already fixed it." in page
    )
