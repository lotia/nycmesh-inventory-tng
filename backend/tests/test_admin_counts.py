"""Counts and adjustments (admin/counts.py), and their reasons
(admin/settings.py)."""

import re
import uuid
from decimal import Decimal

import pytest
from conftest import Committed
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session
from test_ledger import Place, received, stocked, write
from test_models import violates
from test_sign_in import text

from inventory_tng.admin.counts import ADJUSTMENTS, COUNTS
from inventory_tng.admin.settings import SETTINGS
from inventory_tng.ledger import expected, lock_place, moment
from inventory_tng.models import (
    AdjustmentReason,
    Admin,
    LedgerTransaction,
    Movement,
)


def key_on(response: Response) -> str:
    match = re.search(r'name="key" value="([0-9a-f-]{36})"', response.text)
    assert match is not None
    return match.group(1)


def count_form(client: TestClient, path: str) -> dict[str, str]:
    """The count page's hidden fields, as it was shown: its key, and the
    place's latest ledger entry then."""
    return count_form_of(client.get(path))


def count_form_of(page: Response) -> dict[str, str]:
    found = re.findall(r'type="hidden" name="(key|as_of)" value="([^"]*)"', page.text)
    return dict(found)


def counts_written(session: Session) -> list[LedgerTransaction]:
    return list(
        session.scalars(
            select(LedgerTransaction)
            .where(LedgerTransaction.kind.in_(("count", "adjustment")))
            .order_by(LedgerTransaction.id)
        )
    )


def test_the_reasons_come_seeded(session: Session) -> None:
    names = session.scalars(select(AdjustmentReason.name).order_by(AdjustmentReason.id))
    assert list(names) == [
        "Damaged",
        "Lost or stolen",
        "Found",
        "Used for testing",
        "Records were wrong",
    ]


def test_a_count_puts_the_records_right(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    path = f"{COUNTS}/{place.store.id}"
    assert 'value="5"' in admin_client.get(path).text
    done = admin_client.post(
        path,
        data=count_form(admin_client, path)
        | {
            f"counted_{place.item.id}": "3",
            "extra_item_id": str(place.cable.id),
            "extra_quantity": "10.5",
        },
    )
    assert done.headers["location"] == f"{COUNTS}?notice=counted"
    assert expected(session, place.store.id) == {
        place.item.id: Decimal(3),
        place.cable.id: Decimal("10.5"),
    }
    found, lost = counts_written(session)
    ada = session.scalars(select(Admin)).one()
    assert (found.to_location_id, lost.from_location_id) == (place.store.id,) * 2
    assert {found.admin_id, lost.admin_id} == {ada.id}
    assert "Count" in text(admin_client.get(COUNTS))


def test_a_count_that_matches_is_still_recorded(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    path = f"{COUNTS}/{place.store.id}"
    done = admin_client.post(
        path, data=count_form(admin_client, path) | {f"counted_{place.item.id}": "5"}
    )
    assert done.headers["location"] == f"{COUNTS}?notice=counted_same"
    (clean,) = counts_written(session)
    assert clean.to_location_id == place.store.id
    assert (
        session.scalars(
            select(Movement).where(Movement.transaction_id == clean.id)
        ).all()
        == []
    )
    assert "as the records said" in text(admin_client.get(COUNTS))


def test_a_count_sent_twice_is_written_once(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    path = f"{COUNTS}/{place.store.id}"
    form = count_form(admin_client, path) | {f"counted_{place.item.id}": "2"}
    admin_client.post(path, data=form)
    admin_client.post(path, data=form)
    assert len(counts_written(session)) == 1
    assert expected(session, place.store.id) == {place.item.id: Decimal(2)}


def test_a_count_refuses_what_is_not_a_quantity(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    path = f"{COUNTS}/{place.store.id}"
    shown = count_form(admin_client, path)
    for typed, problem in (("lots", "Type a number"), ("1.5", "whole units")):
        refused = admin_client.post(
            path, data=shown | {f"counted_{place.item.id}": typed}
        )
        assert refused.status_code == 400
        assert problem in text(refused)
    assert counts_written(session) == []


def test_found_and_written_off(admin_client: TestClient, session: Session) -> None:
    place = stocked(session)
    damaged = session.scalars(
        select(AdjustmentReason).where(AdjustmentReason.name == "Damaged")
    ).one()
    form = {
        "key": key_on(admin_client.get(COUNTS)),
        "location_id": str(place.store.id),
        "item_id": str(place.item.id),
        "amount": "2",
        "direction": "off",
        "reason_id": str(damaged.id),
    }
    done = admin_client.post(ADJUSTMENTS, data=form)
    assert done.headers["location"] == f"{COUNTS}?notice=adjusted"
    admin_client.post(ADJUSTMENTS, data=form)  # Twice: written once.
    (written,) = counts_written(session)
    assert (written.kind, written.reason_id) == ("adjustment", damaged.id)
    assert expected(session, place.store.id) == {place.item.id: Decimal(3)}
    assert "Damaged: -2 each" in " ".join(text(admin_client.get(COUNTS)).split())


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"location_id": ""}, "Pick a place."),
        ({"item_id": "999999"}, "Pick an item."),
        ({"amount": "0"}, "Use a number above 0."),
        ({"amount": "1.5"}, "whole units"),
        ({"direction": "sideways"}, "Found or written off?"),
        ({"reason_id": ""}, "Pick a reason."),
    ],
)
def test_an_adjustment_says_what_is_missing(
    admin_client: TestClient, session: Session, change: dict[str, str], problem: str
) -> None:
    place = stocked(session)
    reason = session.scalars(select(AdjustmentReason)).first()
    assert reason is not None
    form = {
        "key": str(uuid.uuid4()),
        "location_id": str(place.store.id),
        "item_id": str(place.item.id),
        "amount": "1",
        "direction": "found",
        "reason_id": str(reason.id),
    } | change
    refused = admin_client.post(ADJUSTMENTS, data=form)
    assert refused.status_code == 400
    assert problem in text(refused)


def test_the_database_holds_counts_to_their_rules(session: Session) -> None:
    """A count needs its admin; an adjustment, its reason too."""
    place = Place(session)
    with violates(session, "ck_ledger_transaction_admin_counts"):
        session.add(
            LedgerTransaction(
                idempotency_key=uuid.uuid4(),
                kind="count",
                to_location_id=place.store.id,
            )
        )
    admin = session.scalars(select(Admin)).first() or Admin(username="ada")
    session.add(admin)
    session.flush()
    with violates(session, "ck_ledger_transaction_adjustment_reason"):
        session.add(
            LedgerTransaction(
                idempotency_key=uuid.uuid4(),
                kind="adjustment",
                to_location_id=place.store.id,
                admin_id=admin.id,
            )
        )


def test_reasons_can_be_added_renamed_retired_and_brought_back(
    admin_client: TestClient, session: Session
) -> None:
    admin_client.post(f"{SETTINGS}/reasons", data={"name": "Given away"})
    given = session.scalars(
        select(AdjustmentReason).where(AdjustmentReason.name == "Given away")
    ).one()
    path = f"{SETTINGS}/reasons/{given.id}"
    admin_client.post(path, data={"name": "Donated", "action": "rename"})
    assert given.name == "Donated"
    admin_client.post(path, data={"name": "Donated", "action": "retire"})
    assert given.retired_at is not None
    assert (
        "Donated"
        not in text(admin_client.get(COUNTS)).split("Why")[1].split("</select>")[0]
    )
    admin_client.post(path, data={"name": "Donated", "action": "restore"})
    assert given.retired_at is None
    taken = admin_client.post(f"{SETTINGS}/reasons", data={"name": "damaged"})
    assert "already a reason by that name" in text(taken)


def with_cable(session: Session, place: Place) -> None:
    """Twenty metres of cable received into the store, beside the radios."""
    received(session, place, [{"item_id": place.cable.id, "quantity": "20"}])


def test_a_save_during_a_count_still_applies(
    admin_client: TestClient, session: Session
) -> None:
    """The count is true for when its form was shown: three radios moved to
    the van meanwhile, and only the cable was counted, so it saves in one go
    and the move stands."""
    place = stocked(session)
    with_cable(session, place)
    path = f"{COUNTS}/{place.store.id}"
    shown = count_form(admin_client, path)
    write(session, place)  # Three radios to the van.
    done = admin_client.post(
        path,
        data=shown
        | {f"counted_{place.item.id}": "", f"counted_{place.cable.id}": "18"},
    )
    assert done.headers["location"] == f"{COUNTS}?notice=counted"
    assert expected(session, place.store.id) == {
        place.item.id: Decimal(2),
        place.cable.id: Decimal(18),
    }


def test_an_item_that_moved_while_counted_is_counted_again(
    admin_client: TestClient, session: Session
) -> None:
    """Three radios went to the van after the page was shown: the count of
    radios may be from before or after, so only the cable is saved, and the
    page asks for the radios again, as they are now."""
    place = stocked(session)
    with_cable(session, place)
    path = f"{COUNTS}/{place.store.id}"
    shown = count_form(admin_client, path)
    write(session, place)  # Three radios to the van.
    asked = admin_client.post(
        path,
        data=shown
        | {f"counted_{place.item.id}": "5", f"counted_{place.cable.id}": "18"},
    )
    assert asked.status_code == 409
    page = text(asked)
    assert "Saved 1 line. These moved while you counted" in page
    assert "0 in, 3 out; you typed 5" in page
    assert place.cable.name not in page.split("Something else here")[0]
    assert expected(session, place.store.id) == {
        place.item.id: Decimal(2),
        place.cable.id: Decimal(18),
    }
    again = count_form_of(asked)
    assert again["key"] != shown["key"]
    assert again["as_of"] != shown["as_of"]
    done = admin_client.post(path, data=again | {f"counted_{place.item.id}": "1"})
    assert done.headers["location"] == f"{COUNTS}?notice=counted"
    assert expected(session, place.store.id)[place.item.id] == Decimal(1)


def test_a_recount_sent_back_with_a_problem_asks_for_its_items_only(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    with_cable(session, place)
    path = f"{COUNTS}/{place.store.id}"
    shown = count_form(admin_client, path)
    write(session, place)  # Three radios to the van.
    asked = admin_client.post(
        path,
        data=shown
        | {f"counted_{place.item.id}": "5", f"counted_{place.cable.id}": "18"},
    )
    again = count_form_of(asked) | {"recounting": "1"}
    refused = admin_client.post(
        path, data=again | {f"counted_{place.item.id}": "x", "counted_bad": "1"}
    )
    assert refused.status_code == 400
    page = refused.text
    assert f'name="counted_{place.item.id}"' in page
    assert f'name="counted_{place.cable.id}"' not in page
    assert "Count them again now." in text(refused)


def test_a_count_whose_every_line_moved_writes_nothing(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    path = f"{COUNTS}/{place.store.id}"
    shown = count_form(admin_client, path)
    write(session, place)  # Three radios to the van.
    asked = admin_client.post(path, data=shown | {f"counted_{place.item.id}": "5"})
    assert asked.status_code == 409
    assert "Saved" not in text(asked)
    assert counts_written(session) == []


def test_a_count_needs_the_moment_it_was_shown(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    path = f"{COUNTS}/{place.store.id}"
    shown = count_form(admin_client, path)
    for as_of in ("", "²", "-1", str(2**31)):
        refused = admin_client.post(
            path, data=shown | {"as_of": as_of, f"counted_{place.item.id}": "5"}
        )
        assert refused.status_code == 404
    assert counts_written(session) == []


def test_a_count_shown_again_for_a_problem_keeps_its_moment(
    admin_client: TestClient, session: Session
) -> None:
    """What was typed is as of when the form was first shown, so the form
    shown again to fix a line keeps that moment, not a later one."""
    place = stocked(session)
    with_cable(session, place)
    path = f"{COUNTS}/{place.store.id}"
    shown = count_form(admin_client, path)
    write(session, place, lines=[{"item_id": place.item.id, "quantity": "5"}])
    refused = admin_client.post(
        path,
        data=shown
        | {f"counted_{place.item.id}": "5", f"counted_{place.cable.id}": "x"},
    )
    assert refused.status_code == 400
    assert count_form_of(refused)["as_of"] == shown["as_of"]
    # Every radio has gone since, but the radios' line is still there.
    assert f'name="counted_{place.item.id}" inputmode="decimal" value="5"' in (
        refused.text
    )


def test_a_count_sent_again_after_a_later_move_is_not_asked_again(
    admin_client: TestClient, session: Session
) -> None:
    """The first send saved every line: a move after it does not make the
    second send ask for the radios again."""
    place = stocked(session)
    path = f"{COUNTS}/{place.store.id}"
    form = count_form(admin_client, path) | {f"counted_{place.item.id}": "4"}
    assert admin_client.post(path, data=form).status_code == 303
    write(session, place)  # Three radios to the van.
    again = admin_client.post(path, data=form)
    assert again.headers["location"] == f"{COUNTS}?notice=counted"
    assert expected(session, place.store.id) == {place.item.id: Decimal(1)}


def a_store(committed: Committed) -> Place:
    setup = committed.session()
    place = Place(setup)
    setup.commit()
    return place


def receive(session: Session, place: Place) -> None:
    """Three radios received into the store. Committed tests may not write
    the ledger (APPEND_ONLY), so the caller rolls it back."""
    received(session, place)


@pytest.mark.concurrency
def test_a_count_form_waits_for_a_save_under_way_at_the_place(
    committed: Committed,
) -> None:
    """Its moment must not leave out a save that commits later with a lower
    ledger id."""
    place = a_store(committed)
    saving = committed.session()
    receive(saving, place)
    counting = committed.session()
    worker = committed.blocked(counting, lambda: moment(counting, place.store.id))
    saving.rollback()
    assert worker.finished().error is None


@pytest.mark.concurrency
def test_a_count_form_does_not_hold_up_saves(committed: Committed) -> None:
    """The form keeps its moment, not the lock: a save at the place goes
    ahead while the form is still being made."""
    place = a_store(committed)
    counting = committed.session()
    moment(counting, place.store.id)
    saving = committed.session()
    receive(saving, place)
    saving.rollback()
    counting.rollback()


@pytest.mark.concurrency
def test_a_save_at_the_place_waits_for_a_count(committed: Committed) -> None:
    """Before it has an id: one drawn first could be committed after the
    count's moment, though lower (inventory-tng-099)."""
    place = a_store(committed)
    counting = committed.session()
    lock_place(counting, place.store.id)
    before = ids_drawn(counting)
    saving = committed.session()
    worker = committed.blocked(saving, lambda: receive(saving, place))
    assert ids_drawn(committed.session()) == before
    counting.rollback()
    assert worker.finished().error is None
    saving.rollback()


def ids_drawn(session: Session) -> tuple[int, bool]:
    """How far the ledger's ids have been handed out."""
    last, called = session.execute(
        sql_text("SELECT last_value, is_called FROM ledger_transaction_id_seq")
    ).one()
    return last, called


def test_leftovers_of_a_retired_item_can_be_written_off(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    place.item.retired_at = place.item.created_at
    session.flush()
    page = text(admin_client.get(COUNTS))
    assert place.item.name in page
    lost = session.scalars(
        select(AdjustmentReason).where(AdjustmentReason.name == "Lost or stolen")
    ).one()
    form = {
        "key": key_on(admin_client.get(COUNTS)),
        "location_id": str(place.store.id),
        "item_id": str(place.item.id),
        "amount": "5",
        "direction": "off",
        "reason_id": str(lost.id),
    }
    assert admin_client.post(ADJUSTMENTS, data=form).status_code == 303
    assert expected(session, place.store.id) == {}
