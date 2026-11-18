"""An opening's sealed end counted though the records hold none of it
sealed, an open package closed on a count, and a correction and a count
at once (Ali, 2026-11-15, inventory-tng-he9, inventory-tng-dd8)."""

import re
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.core.db import RuleBroken
from inventory_tng.models.places import Location
from inventory_tng.models.stock import (
    LedgerTransaction,
    Movement,
    OpenPackage,
    OverRecorded,
)
from inventory_tng.stock import counts, moments, open_packages, over_recorded
from inventory_tng.stock.ledger import Form
from inventory_tng.stock.moments import expected
from inventory_tng.stock.paths import COUNTS

from helpers.concurrency import Committed
from helpers.markup import links
from helpers.over_recorded import (
    PAGE,
    arrived_field,
    checks,
    correct_check,
    count_ids,
    moved_whole,
    new_admin_id,
    opened_into,
    opening_raised,
    reason_adjustments,
    sealed_field,
)
from helpers.stock import Place, Spools, count_at, count_form_at, write

# An opening's sealed end can be counted though the records hold none of
# it sealed, and an open package it arrived in closed on a count puts that
# end right (Ali, 2026-11-15, inventory-tng-he9, inventory-tng-dd8).


def sealed_line(page: str, spools: Spools) -> str | None:
    """The value a count page fills a line of sealed spools with, if it has
    one."""
    found = re.search(rf'name="{sealed_field(spools)}"[^>]*value="([^"]*)"', page)
    return found and found.group(1)


@pytest.mark.parametrize("state", ["missing", "used_up"])
def test_its_open_package_closed_on_a_count_puts_that_end_right_then_correct_works(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    state: str,
) -> None:
    """Two sealed spools recorded opened, one really: the phantom open
    package said not there (or used up) on a count is at 0, as a count of 0
    would say, so Correct writes only the sealed spool found again."""
    spools = Spools(session)
    check = opening_raised(registered_client, spools)
    into = opened_into(session, check)
    assert into.open_package_id is not None
    open_id = into.open_package_id
    done = count_at(
        admin_client,
        spools.store.id,
        {f"opened_{open_id}": "", f"state_{open_id}": state},
    )
    assert done.status_code == 303
    (counted_at,) = count_ids(session)
    (check,) = checks(session)
    assert (check.left_count_id, check.arrived_count_id) == (None, counted_at)
    assert check.settled_at is None
    done = admin_client.post(
        f"{PAGE}/{check.id}/correct", data={"seen": over_recorded.seen(check)}
    )
    assert "over_recorded_corrected" in done.headers["location"]
    (line,) = reason_adjustments(session, over_recorded.REASON)
    assert (line.quantity, line.to_package_id, line.to_open_package_id) == (
        305,
        spools.spool.id,
        None,
    )
    session.expire_all()
    forms = moments.expected_forms(
        session,
        spools.store.id,
        moments.moment(session, spools.store.id),
        [spools.cable.id],
    )
    assert forms == {(spools.cable.id, spools.sealed): 305} | {
        (spools.cable.id, Form(open_package_id=other.id)): 305
        for other in session.scalars(
            select(OpenPackage).where(
                OpenPackage.closed_at.is_(None), OpenPackage.id != open_id
            )
        )
    }


def test_its_open_package_not_closed_by_the_count_puts_no_end_right(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closed or moved meanwhile, as closing finds it under its lock: this
    count did not close it, so puts nothing right of it."""
    spools = Spools(session)
    check = opening_raised(registered_client, spools)
    open_id = opened_into(session, check).open_package_id
    monkeypatch.setattr(open_packages, "close", lambda *_, **__: None)
    count_at(
        admin_client,
        spools.store.id,
        {f"opened_{open_id}": "", f"state_{open_id}": "missing"},
    )
    (check,) = checks(session)
    assert (check.left_count_id, check.arrived_count_id) == (None, None)


def test_closing_its_open_package_and_counting_the_sealed_spool_closes_it(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    spools = Spools(session)
    check = opening_raised(registered_client, spools)
    open_id = opened_into(session, check).open_package_id
    page = admin_client.get(f"{COUNTS}/{spools.store.id}").text
    assert sealed_line(page, spools) == "0"
    count_at(
        admin_client,
        spools.store.id,
        {
            sealed_field(spools): "1",
            f"opened_{open_id}": "",
            f"state_{open_id}": "missing",
        },
    )
    (counted_at,) = count_ids(session)
    (check,) = checks(session)
    assert (check.settled_how, check.left_count_id, check.arrived_count_id) == (
        "counted",
        counted_at,
        counted_at,
    )
    assert reason_adjustments(session, over_recorded.REASON) == []


def test_an_open_package_closed_on_a_count_puts_no_move_right(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Moved whole, its open package's own count puts its end right; closed
    on a count, nothing does: Confirm count or Mark checked, as before."""
    spools = Spools(session)
    made = moved_whole(registered_client, spools)
    count_at(
        admin_client,
        spools.van.id,
        {f"opened_{made.id}": "", f"state_{made.id}": "missing"},
    )
    (check,) = checks(session)
    assert (check.left_count_id, check.arrived_count_id) == (None, None)
    assert check.settled_at is None


@pytest.mark.parametrize("tracked", [True, False])
def test_counting_its_place_from_the_row_counts_the_sealed_spool_and_closes_it(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    tracked: bool,
) -> None:
    """Its open or loose end counted, 'Count <place>' leads to the item's
    count with a line of sealed spools, at 0 as the records hold none; not
    tracked, the store holds loose ones alone but is counted by form."""
    spools = Spools(session, tracked=tracked)
    check = opening_raised(registered_client, spools)
    count_at(
        admin_client,
        spools.store.id,
        {arrived_field(spools, opened_into(session, check)): "305"},
    )
    link = f"{COUNTS}/{spools.store.id}?item={spools.cable.id}"
    listed = links(admin_client.get(f"{PAGE}?item={spools.cable.id}").text)
    assert link in [href for _, href in listed]
    page = admin_client.get(link).text
    assert sealed_line(page, spools) == "0"
    assert f'name="counted_{spools.cable.id}"' not in page
    form = count_form_at(admin_client, spools.store.id)
    done = admin_client.post(link, data=form | {sealed_field(spools): "1"})
    assert done.status_code == 303
    (check,) = checks(session)
    assert check.settled_how == "counted"
    session.expire_all()
    store = spools.store.id
    sealed = (spools.cable.id, spools.sealed)
    found = moments.expected_forms(
        session, store, moments.moment(session, store), [spools.cable.id]
    )
    assert found[sealed] == 305


def test_the_sealed_line_is_shown_as_its_item_is_by_the_filter(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    spools = Spools(session, tracked=False)
    opening_raised(registered_client, spools)
    store = f"{COUNTS}/{spools.store.id}"
    for shown in ("", f"?q={spools.cable.name}", f"?item={spools.cable.id}"):
        assert sealed_line(admin_client.get(store + shown).text, spools) == "0"
    other = spools.place.item.id
    for hidden in (f"?item={other}", "?q=nothingcalledthis"):
        assert sealed_line(admin_client.get(store + hidden).text, spools) is None


def test_its_item_is_counted_though_the_records_hold_none_of_it_here(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Both open packages closed on a count, the store holds no cable: the
    sealed end still waits, so the store's count asks for it."""
    spools = Spools(session)
    opening_raised(registered_client, spools)
    shut = {
        field: value
        for row in session.scalars(select(OpenPackage))
        for field, value in ((f"opened_{row.id}", ""), (f"state_{row.id}", "used_up"))
    }
    count_at(admin_client, spools.store.id, shut)
    session.expire_all()
    assert expected(session, spools.store.id).get(spools.cable.id, 0) == 0
    page = admin_client.get(f"{COUNTS}/{spools.store.id}").text
    assert sealed_line(page, spools) == "0"
    count_at(admin_client, spools.store.id, {sealed_field(spools): "1"})
    (check,) = checks(session)
    assert check.settled_how == "counted"


def test_its_sealed_line_left_at_0_writes_nothing_and_counts_that_end(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Left as it is, 0 is what was counted, as any line: nothing written
    of sealed spools, the sealed end put right."""
    spools = Spools(session, tracked=False)
    check = opening_raised(registered_client, spools)
    count_at(
        admin_client,
        spools.store.id,
        {sealed_field(spools): "0", f"loose_{spools.cable.id}": "610"},
    )
    (counted_at,) = count_ids(session)
    assert (
        list(
            session.scalars(
                select(Movement).where(Movement.transaction_id == counted_at)
            )
        )
        == []
    )
    (check,) = checks(session)
    assert check.settled_how == "counted"


def test_its_loose_only_place_counted_by_form_puts_each_form_right(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Not tracked, the store holds 610 loose alone, really one spool sealed
    and 305 loose: counted by form, each form is put right, the total kept,
    and the store's other items as they were."""
    spools = Spools(session, tracked=False)
    opening_raised(registered_client, spools)
    store = spools.store.id
    others = {
        item_id: amount
        for item_id, amount in expected(session, store).items()
        if item_id != spools.cable.id
    }
    count_at(
        admin_client,
        store,
        {sealed_field(spools): "1", f"loose_{spools.cable.id}": "305"},
    )
    (check,) = checks(session)
    assert check.settled_how == "counted"
    session.expire_all()
    found = moments.expected_forms(
        session, store, moments.moment(session, store), [spools.cable.id]
    )
    assert found == {
        (spools.cable.id, spools.sealed): 305,
        (spools.cable.id, Form()): 305,
    }
    now = expected(session, store)
    assert now[spools.cable.id] == 610
    assert {item_id: now[item_id] for item_id in others} == others


def test_a_move_waiting_asks_no_sealed_line(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Where an open package moved whole arrived, the van holds none
    sealed, and none is asked for."""
    spools = Spools(session)
    moved_whole(registered_client, spools)
    for place in (spools.store, spools.van):
        moment = moments.moment(session, place.id)
        assert over_recorded.openings_waiting(session, place.id, moment) == {}
    page = admin_client.get(f"{COUNTS}/{spools.van.id}").text
    assert sealed_line(page, spools) is None


def committed_check(
    own_committed: Committed,
) -> tuple[Place, OverRecorded, int, LedgerTransaction]:
    """A move of three radios from the store to the van with one recorded
    more, committed: its place, the check, an administrator, and the
    move."""
    setup = own_committed.session()
    place = Place(setup)
    moved = write(setup, place)
    movement_id = setup.scalar(
        select(Movement.id).where(Movement.transaction_id == moved.id)
    )
    assert movement_id is not None
    over_recorded.raise_checks(setup, {movement_id: Decimal(1)})
    admin = new_admin_id(setup)
    setup.commit()
    (check,) = checks(setup)
    return place, check, admin, moved


def count_van(
    session: Session, place: Place, admin: int, as_of: int, at: Location | None = None
) -> int:
    """The van, or `at`, counted as of `as_of`, as the count page does: how
    many it closed."""
    where = (at or place.van).id
    counted = counts.record_count(
        session, uuid.uuid4(), admin, where, [(place.item.id, Decimal(2))], as_of
    )
    return over_recorded.counted(
        session, admin, where, as_of, counted.written, counted.put_right
    )


@pytest.mark.concurrency
def test_a_correct_waits_for_a_count_of_its_end_then_is_refused(
    own_committed: Committed,
) -> None:
    """The count holds the van, then the check; Correct takes its places
    first, so waits, then finds an end counted since it was seen."""
    place, check, admin, moved = committed_check(own_committed)
    first, second = own_committed.session(), own_committed.session()
    seen = over_recorded.seen(check)
    assert count_van(first, place, admin, moved.id) == 0
    worker = own_committed.blocked(
        second, lambda: correct_check(second, check, seen, admin)
    )
    first.commit()
    finished = worker.finished()
    second.rollback()
    assert isinstance(finished.error, RuleBroken)
    assert finished.error.rule == "over_recorded_unchanged"
    (check,) = checks(first)
    assert (check.settled_at, check.arrived_count_id) == (None, count_ids(first)[0])


@pytest.mark.concurrency
def test_a_count_waits_for_a_correct_then_puts_nothing_right(
    own_committed: Committed,
) -> None:
    place, check, admin, moved = committed_check(own_committed)
    first, second = own_committed.session(), own_committed.session()
    correct_check(first, check, over_recorded.seen(check), admin)
    worker = own_committed.blocked(
        second, lambda: count_van(second, place, admin, moved.id)
    )
    first.commit()
    finished = worker.finished()
    second.commit()
    assert (finished.error, finished.result) == (None, 0)
    (check,) = checks(first)
    assert (check.settled_how, check.arrived_count_id) == ("corrected", None)


@pytest.mark.concurrency
def test_both_ends_counted_at_once_the_second_closes_it(
    own_committed: Committed,
) -> None:
    """Each count holds the check it puts right, so the second reads the
    first's end once it is in, and closes it, never two ends counted and
    still waiting."""
    place, check, admin, moved = committed_check(own_committed)
    first, second = own_committed.session(), own_committed.session()
    assert count_van(first, place, admin, moved.id) == 0
    worker = own_committed.blocked(
        second, lambda: count_van(second, place, admin, moved.id, place.store)
    )
    first.commit()
    finished = worker.finished()
    second.commit()
    assert (finished.error, finished.result) == (None, 1)
    (check,) = checks(first)
    assert check.settled_how == "counted"
