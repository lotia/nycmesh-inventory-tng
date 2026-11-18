"""An opening whose open package is closed outside a count, before or
after its sealed end is counted, and one with both ends right while it
waits, closed with Close it (Ali, 2026-11-15, inventory-tng-nyl,
inventory-tng-die, inventory-tng-dro)."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng.core.db import RuleBroken
from inventory_tng.core.local_time import show_day, site_zone
from inventory_tng.models.stock import (
    LedgerTransaction,
    Movement,
    OpenPackage,
    OverRecorded,
)
from inventory_tng.stock import counts, ledger, moments, open_packages, over_recorded
from inventory_tng.stock.ledger import Entry, Form
from inventory_tng.stock.paths import COUNTS

from helpers.concurrency import Committed
from helpers.markup import links
from helpers.over_recorded import (
    PAGE,
    checks,
    correct,
    correct_check,
    count_ids,
    moved_whole,
    new_admin_id,
    opened_into,
    opening_raised,
    reason_adjustments,
    recorded_then_changed,
    sealed_field,
    text_of,
)
from helpers.stock import URL, Spools, ada_id, count_at, count_form_at, cut

# An opening's open package closed since (Confirm used up, Close, a count
# whose lines were all withheld): Correct writes the sealed end alone, as
# closing wrote off what it showed (Ali, 2026-11-15, inventory-tng-nyl).


def closed_by_confirm(admin_client: TestClient, spools: Spools, open_id: int) -> None:
    """Reported used up by a volunteer, then confirmed."""
    session = spools.session
    open_packages.report(
        session, [open_id], spools.place.volunteer.id, datetime.now(UTC)
    )
    session.commit()
    done = admin_client.post(f"/admin/stock/used-up/{open_id}/confirm")
    assert "used_up_confirmed" in done.headers["location"]


def closed_from_its_item(
    admin_client: TestClient, spools: Spools, open_id: int
) -> None:
    done = admin_client.post(
        f"/admin/items/{spools.cable.id}/open-packages/{open_id}/close"
    )
    assert "open_package_closed" in done.headers["location"]


def sealed_found_only(session: Session, spools: Spools) -> None:
    """Correct wrote one line: the sealed spool found again."""
    (line,) = reason_adjustments(session, over_recorded.REASON)
    assert (line.quantity, line.to_package_id, line.to_open_package_id) == (
        305,
        spools.spool.id,
        None,
    )


@pytest.mark.parametrize("close", [closed_by_confirm, closed_from_its_item])
def test_its_open_package_closed_outside_a_count_correct_writes_the_sealed_end(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    close: Callable[[TestClient, Spools, int], None],
) -> None:
    spools = Spools(session)
    check = opening_raised(registered_client, spools)
    open_id = opened_into(session, check).open_package_id
    assert open_id is not None
    close(admin_client, spools, open_id)
    on = session.get_one(OpenPackage, open_id).closed_at
    assert on is not None
    day = show_day(on, site_zone(session))
    page = admin_client.get(f"{PAGE}?item={spools.cable.id}").text
    assert f"Open spool closed {day}. Sealed spool still to put right." in text_of(page)
    assert "Correct sealed spool<span" in page
    (check,) = checks(session)
    with capture_logs() as logs:
        done = admin_client.post(
            f"{PAGE}/{check.id}/correct", data={"seen": over_recorded.seen(check)}
        )
    assert "over_recorded_corrected" in done.headers["location"]
    sealed_found_only(session, spools)
    (check,) = checks(session)
    assert (check.settled_how, check.left_count_id, check.arrived_count_id) == (
        "corrected",
        None,
        None,
    )
    assert {
        "event": "over recorded closed end skipped",
        "log_level": "info",
        "over_recorded_id": check.id,
        "open_package_id": open_id,
    } in logs
    # Sent again: settled, so nothing more is written.
    again = admin_client.post(
        f"{PAGE}/{check.id}/correct", data={"seen": over_recorded.seen(check)}
    )
    assert "over_recorded_unchanged" in again.headers["location"]
    sealed_found_only(session, spools)


def test_its_open_package_closed_on_a_count_whose_lines_were_withheld(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Every line of the count withheld as moved meanwhile, it writes no
    count transaction, so puts no end right; its close still closes the
    open package, and Correct then writes the sealed end alone."""
    spools = Spools(session)
    check = opening_raised(registered_client, spools)
    open_id = opened_into(session, check).open_package_id
    (other,) = session.scalars(
        select(OpenPackage).where(
            OpenPackage.item_id == spools.cable.id, OpenPackage.id != open_id
        )
    )
    shown = count_form_at(admin_client, spools.store.id)
    spools.move(Entry(spools.cable.id, Decimal(5), Form(open_package_id=other.id)))
    session.commit()
    count_at(
        admin_client,
        spools.store.id,
        {
            f"opened_{other.id}": "300",
            f"opened_{open_id}": "",
            f"state_{open_id}": "missing",
        },
        shown,
        status=409,
    )
    assert count_ids(session) == []
    (check,) = checks(session)
    assert (check.left_count_id, check.arrived_count_id) == (None, None)
    assert session.get_one(OpenPackage, open_id).closed_at is not None
    assert (
        "over_recorded_corrected" in correct(admin_client, session).headers["location"]
    )
    sealed_found_only(session, spools)


def test_a_cut_from_an_open_package_closed_since_is_still_refused(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Moves unchanged: only an opening skips an end closed since."""
    spools = Spools(session)
    made = spools.opened()
    session.commit()
    first = spools.place.batch(lines=[cut(made, "20")])
    recorded_then_changed(registered_client, first, lines=[cut(made, "15")])
    closed_from_its_item(admin_client, spools, made.id)
    page = admin_client.get(f"{PAGE}?item={spools.cable.id}").text
    assert " closed " not in text_of(page)
    response = correct(admin_client, session)
    assert response.status_code == 409
    assert reason_adjustments(session, over_recorded.REASON) == []
    (check,) = checks(session)
    assert check.settled_at is None


@pytest.mark.concurrency
def test_a_correct_waits_for_a_close_of_its_open_package_then_skips_it(
    own_committed: Committed,
) -> None:
    """The close holds the store, then the open package; Correct takes the
    store first, so waits, then finds it closed and writes the sealed end
    alone."""
    setup = own_committed.session()
    spools = Spools(setup)
    made = spools.opened()
    movement_id = setup.scalar(
        select(Movement.id).where(Movement.to_open_package_id == made.id)
    )
    assert movement_id is not None
    over_recorded.raise_checks(setup, {movement_id: Decimal(305)})
    admin = new_admin_id(setup)
    setup.commit()
    (check,) = checks(setup)
    first, second = own_committed.session(), own_committed.session()
    assert open_packages.close(first, made.id, admin, datetime.now(UTC)) is not None
    worker = own_committed.blocked(
        second, lambda: correct_check(second, check, over_recorded.seen(check), admin)
    )
    first.commit()
    finished = worker.finished()
    assert finished.error is None
    second.commit()
    (line,) = reason_adjustments(first, over_recorded.REASON)
    assert (line.to_package_id, line.to_open_package_id) == (spools.spool.id, None)


@pytest.mark.parametrize("close", [closed_by_confirm, closed_from_its_item])
def test_its_open_package_closed_then_its_sealed_spool_counted_closes_it(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    close: Callable[[TestClient, Spools, int], None],
) -> None:
    """Closed outside a count, the row offers 'Count <place>'; that count
    of the sealed spool puts the other end right, so it closes as counted:
    no Correct is left that would write nothing."""
    spools = Spools(session)
    check = opening_raised(registered_client, spools)
    open_id = opened_into(session, check).open_package_id
    assert open_id is not None
    close(admin_client, spools, open_id)
    link = f"{COUNTS}/{spools.store.id}?item={spools.cable.id}"
    listed = links(admin_client.get(f"{PAGE}?item={spools.cable.id}").text)
    assert link in [href for _, href in listed]
    form = count_form_at(admin_client, spools.store.id)
    with capture_logs() as logs:
        done = admin_client.post(link, data=form | {sealed_field(spools): "1"})
    assert done.status_code == 303
    (counted_at,) = count_ids(session)
    (check,) = checks(session)
    assert (check.settled_how, check.left_count_id, check.arrived_count_id) == (
        "counted",
        counted_at,
        None,
    )
    assert any(each["event"] == "over recorded counted" for each in logs)
    assert reason_adjustments(session, over_recorded.REASON) == []


def test_a_moves_open_package_closed_then_its_place_counted_puts_nothing_right(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Moves unchanged: only an opening's open package closed puts its end
    right."""
    spools = Spools(session)
    made = moved_whole(registered_client, spools)
    closed_from_its_item(admin_client, spools, made.id)
    count_at(admin_client, spools.van.id, {f"loose_{spools.cable.id}": "0"})
    assert count_ids(session) != []
    (check,) = checks(session)
    assert check.settled_at is None
    assert " closed " not in text_of(
        admin_client.get(f"{PAGE}?item={spools.cable.id}").text
    )


# An opening's sealed end counted, then its open package closed outside a
# count: closing closes it as counted, as the close wrote off what the open
# package showed (Ali, 2026-11-15, inventory-tng-die).


def closed_ticked(admin_client: TestClient, spools: Spools, open_id: int) -> None:
    """Reported used up, then ticked and confirmed with the others."""
    session = spools.session
    open_packages.report(
        session, [open_id], spools.place.volunteer.id, datetime.now(UTC)
    )
    session.commit()
    done = admin_client.post(
        "/admin/stock/used-up", data={"open_package": [str(open_id)], "do": "confirm"}
    )
    assert "used_up_confirmed" in done.headers["location"]


def closed_on_a_count(admin_client: TestClient, spools: Spools, open_id: int) -> None:
    """Said used up on a later count of its place: the close settles it
    before the count's own rows (over_recorded.counted), which then leave
    it be."""
    count_at(
        admin_client,
        spools.store.id,
        {f"opened_{open_id}": "", f"state_{open_id}": "used_up"},
    )


@pytest.mark.parametrize(
    "close", [closed_by_confirm, closed_ticked, closed_from_its_item, closed_on_a_count]
)
def test_its_sealed_spool_counted_then_its_open_package_closed_closes_it(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    close: Callable[[TestClient, Spools, int], None],
) -> None:
    spools = Spools(session)
    check = opening_raised(registered_client, spools)
    open_id = opened_into(session, check).open_package_id
    assert open_id is not None
    count_at(admin_client, spools.store.id, {sealed_field(spools): "1"})
    (counted_at,) = count_ids(session)
    with capture_logs() as logs:
        close(admin_client, spools, open_id)
    (check,) = checks(session)
    closed = session.get_one(OpenPackage, open_id)
    assert (check.settled_how, check.left_count_id, check.arrived_count_id) == (
        "counted",
        counted_at,
        None,
    )
    assert (check.settled_by_admin_id, check.settled_at) == (
        closed.closed_by_admin_id,
        closed.closed_at,
    )
    assert {
        "event": "over recorded closed",
        "log_level": "info",
        "over_recorded_id": check.id,
        "open_package_id": open_id,
        "admin_id": closed.closed_by_admin_id,
    } in logs
    assert reason_adjustments(session, over_recorded.REASON) == []
    page = admin_client.get(f"{PAGE}?item={spools.cable.id}").text
    assert f"{PAGE}/{check.id}/" not in page


def test_its_sealed_spool_counted_then_its_open_package_moved_whole_and_closed(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Closed where it went, not where it was opened: the close holds that
    place, not the opening's, and closes it all the same."""
    spools = Spools(session)
    check = opening_raised(registered_client, spools)
    open_id = opened_into(session, check).open_package_id
    assert open_id is not None
    count_at(admin_client, spools.store.id, {sealed_field(spools): "1"})
    made = session.get_one(OpenPackage, open_id)
    whole = cut(made, "305") | {"whole": True}
    moved = registered_client.post(URL, json=spools.place.batch(lines=[whole]))
    assert moved.status_code == 201
    session.expire_all()
    assert session.get_one(OpenPackage, open_id).location_id != spools.store.id
    closed_from_its_item(admin_client, spools, open_id)
    (check,) = checks(session)
    assert (check.settled_how, check.arrived_count_id) == ("counted", None)
    assert reason_adjustments(session, over_recorded.REASON) == []


def test_its_open_package_closed_with_neither_end_counted_leaves_it_waiting(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """The sealed end still to put right: Correct writes it (inventory-tng-nyl)."""
    spools = Spools(session)
    check = opening_raised(registered_client, spools)
    open_id = opened_into(session, check).open_package_id
    assert open_id is not None
    closed_from_its_item(admin_client, spools, open_id)
    (check,) = checks(session)
    assert check.settled_at is None


def test_a_cut_counted_where_it_left_then_its_open_package_closed_still_waits(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Moves unchanged: only an opening's row closes with its open package."""
    spools = Spools(session)
    made = spools.opened()
    session.commit()
    first = spools.place.batch(lines=[cut(made, "20")])
    recorded_then_changed(registered_client, first, lines=[cut(made, "15")])
    count_at(admin_client, spools.store.id, {f"opened_{made.id}": "290"})
    (check,) = checks(session)
    assert check.left_count_id is not None
    closed_from_its_item(admin_client, spools, made.id)
    (check,) = checks(session)
    assert check.settled_at is None


def sealed_counted_check(
    own_committed: Committed, *, whole: bool = False
) -> tuple[OverRecorded, OpenPackage, int]:
    """One sealed spool of the store's two opened, recorded with 305 more,
    its sealed end counted, then its open package moved whole to the van if
    `whole`, committed: the check, its open package and an
    administrator."""
    setup = own_committed.session()
    spools = Spools(setup)
    made = spools.opened()
    movement_id = setup.scalar(
        select(Movement.id).where(Movement.to_open_package_id == made.id)
    )
    assert movement_id is not None
    over_recorded.raise_checks(setup, {movement_id: Decimal(305)})
    admin = new_admin_id(setup)
    as_of = moments.moment(setup, spools.store.id)
    count = counts.record_count(
        setup,
        uuid.uuid4(),
        admin,
        spools.store.id,
        [],
        as_of,
        by_form=[(spools.cable.id, spools.sealed, Decimal(305))],
    )
    over_recorded.counted(
        setup, admin, spools.store.id, as_of, count.written, count.put_right
    )
    if whole:
        held = Form(open_package_id=made.id)
        spools.move(Entry(spools.cable.id, Decimal(305), held, held))
    setup.commit()
    (check,) = checks(setup)
    assert (check.left_count_id is not None, check.settled_at) == (True, None)
    return check, made, admin


@pytest.mark.concurrency
def test_a_correct_waits_for_a_close_that_closes_it_then_is_refused(
    own_committed: Committed,
) -> None:
    """The close holds the store, then the check; Correct takes the store
    first, so waits, then finds it settled."""
    check, made, admin = sealed_counted_check(own_committed)
    first, second = own_committed.session(), own_committed.session()
    done = open_packages.close(first, made.id, admin, datetime.now(UTC))
    assert done is not None
    assert done.over_recorded == 1
    worker = own_committed.blocked(
        second, lambda: correct_check(second, check, over_recorded.seen(check), admin)
    )
    first.commit()
    finished = worker.finished()
    second.rollback()
    assert isinstance(finished.error, RuleBroken)
    assert finished.error.rule == "over_recorded_unchanged"
    (check,) = checks(first)
    assert check.settled_how == "counted"
    assert reason_adjustments(first, over_recorded.REASON) == []


@pytest.mark.concurrency
def test_a_close_waits_for_a_correct_then_closes_only_its_package(
    own_committed: Committed,
) -> None:
    """Correct holds the store, then the check, and writes the open end;
    the close waits for the store, then finds the check settled."""
    check, made, admin = sealed_counted_check(own_committed)
    first, second = own_committed.session(), own_committed.session()
    correct_check(first, check, over_recorded.seen(check), admin)
    worker = own_committed.blocked(
        second, lambda: open_packages.close(second, made.id, admin, datetime.now(UTC))
    )
    first.commit()
    finished = worker.finished()
    second.commit()
    assert finished.error is None
    assert finished.result == open_packages.Closed(False, 0)
    (check,) = checks(first)
    assert check.settled_how == "corrected"
    (line,) = reason_adjustments(first, over_recorded.REASON)
    assert line.from_open_package_id == made.id


# Both ends right while it waits: an opening's sealed end counted, then its
# open package, moved whole, closed where it went while a count held the
# row, so the close skipped it (open_package_closed). The row says so and
# offers Close it, which settles it as counted, writing nothing; Correct
# does the same (Ali, 2026-11-15, inventory-tng-dro).


@pytest.mark.concurrency
def test_a_close_skips_one_a_count_holds_and_correct_then_closes_it_as_counted(
    own_committed: Committed,
) -> None:
    """The open package moved whole to the van: a count at the store holds
    the check (counted() locks those of the items it counts), so a close at
    the van leaves it out rather than wait (inventory-tng-die's residual).
    Both commit with both ends right; Correct writes nothing and settles it
    as counted."""
    check, made, admin = sealed_counted_check(own_committed, whole=True)
    first, second = own_committed.session(), own_committed.session()
    movement = first.get_one(Movement, check.movement_id)
    place = first.get_one(LedgerTransaction, movement.transaction_id).from_location_id
    assert place is not None
    sealed = Form(package_id=movement.from_package_id)
    as_of = moments.moment(first, place)
    count = counts.record_count(
        first,
        uuid.uuid4(),
        admin,
        place,
        [],
        as_of,
        by_form=[(made.item_id, sealed, Decimal(305))],
    )
    assert count.written
    assert (
        over_recorded.counted(
            first, admin, place, as_of, count.written, count.put_right
        )
        == 0
    )
    closed = open_packages.close(second, made.id, admin, datetime.now(UTC))
    assert closed == open_packages.Closed(False, 0)
    second.commit()
    first.commit()
    (waits,) = checks(first)
    assert waits.settled_at is None
    third = own_committed.session()
    ledger.lock_places(third, over_recorded.places_of(third, check.id))
    assert over_recorded.correct(third, check.id, over_recorded.seen(waits), admin) == (
        "counted",
        [],
    )
    third.commit()
    (check,) = checks(first)
    assert (check.settled_how, check.settled_by_admin_id) == ("counted", admin)
    assert (check.left_count_id, check.arrived_count_id) == (waits.left_count_id, None)
    assert reason_adjustments(first, over_recorded.REASON) == []


def both_right(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Spools, OverRecorded, str]:
    """An opening's sealed spool counted, then its open package moved whole
    to the van and closed there, the close skipping the check as if a count
    at the store held it: the spools, the check, and the page's words of
    it."""
    spools = Spools(session)
    check = opening_raised(registered_client, spools)
    open_id = opened_into(session, check).open_package_id
    assert open_id is not None
    count_at(admin_client, spools.store.id, {sealed_field(spools): "1"})
    (counted_at,) = count_ids(session)
    made = session.get_one(OpenPackage, open_id)
    whole = cut(made, "305") | {"whole": True}
    moved = registered_client.post(URL, json=spools.place.batch(lines=[whole]))
    assert moved.status_code == 201
    with monkeypatch.context() as patched:
        patched.setattr(over_recorded, "open_package_closed", lambda *_: 0)
        closed_from_its_item(admin_client, spools, open_id)
    session.expire_all()
    (check,) = checks(session)
    assert (check.settled_at, check.left_count_id) == (None, counted_at)
    zone = site_zone(session)
    sealed = show_day(session.get_one(LedgerTransaction, counted_at).created_at, zone)
    on = session.get_one(OpenPackage, open_id).closed_at
    assert on is not None
    words = (
        f"Both ends are right: sealed spool counted {sealed}, "
        f"open spool closed {show_day(on, zone)}."
    )
    return spools, check, words


def test_both_ends_right_it_says_so_and_offers_close_it(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spools, check, words = both_right(
        admin_client, registered_client, session, monkeypatch
    )
    page = admin_client.get(f"{PAGE}?item={spools.cable.id}").text
    assert words in text_of(page)
    assert f'action="{PAGE}/{check.id}/close?item={spools.cable.id}"' in page
    assert "Close it<span" in page
    assert f"{PAGE}/{check.id}/correct" not in page
    assert "still to put right" not in page


@pytest.mark.parametrize("do", ["close", "correct"])
def test_both_ends_right_close_it_or_correct_settles_it_as_counted(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    do: str,
) -> None:
    """Nothing left to write: settled as counted by the administrator, now,
    writing nothing, whether Close it or a Correct shown before."""
    _, check, _ = both_right(admin_client, registered_client, session, monkeypatch)
    ends = (check.left_count_id, check.arrived_count_id)
    written = session.scalar(select(func.count()).select_from(LedgerTransaction))
    began = session.scalar(select(func.now()))
    with capture_logs() as logs:
        done = admin_client.post(
            f"{PAGE}/{check.id}/{do}", data={"seen": over_recorded.seen(check)}
        )
    assert "over_recorded_counted" in done.headers["location"]
    session.expire_all()
    (check,) = checks(session)
    assert (check.settled_how, check.settled_by_admin_id) == (
        "counted",
        ada_id(session),
    )
    assert check.settled_at == began
    assert (check.left_count_id, check.arrived_count_id) == ends
    assert (
        session.scalar(select(func.count()).select_from(LedgerTransaction)) == written
    )
    assert {
        "event": "over recorded closed as right",
        "log_level": "info",
        "over_recorded_id": check.id,
        "admin_id": ada_id(session),
    } in logs
    page = admin_client.get(f"{PAGE}?notice=over_recorded_counted").text
    assert "Closed." in page
    # Sent again: settled, so refused.
    again = admin_client.post(
        f"{PAGE}/{check.id}/{do}", data={"seen": over_recorded.seen(check)}
    )
    assert "over_recorded_unchanged" in again.headers["location"]


def test_close_it_from_a_page_shown_before_is_refused(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shown before a batch said another amount: changed meanwhile."""
    _, check, _ = both_right(admin_client, registered_client, session, monkeypatch)
    seen = over_recorded.seen(check)
    over_recorded.raise_checks(session, {check.movement_id: Decimal(300)})
    session.commit()
    done = admin_client.post(f"{PAGE}/{check.id}/close", data={"seen": seen})
    assert "over_recorded_unchanged" in done.headers["location"]
    (check,) = checks(session)
    assert check.settled_at is None


def test_close_it_with_an_end_still_to_put_right_is_refused(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
) -> None:
    """Only its sealed end counted: Close it would leave the open end
    wrong, so nothing is settled; Correct still writes that end."""
    spools = Spools(session)
    opening_raised(registered_client, spools)
    count_at(admin_client, spools.store.id, {sealed_field(spools): "1"})
    (check,) = checks(session)
    page = admin_client.get(PAGE).text
    assert f"{PAGE}/{check.id}/close" not in page
    done = admin_client.post(
        f"{PAGE}/{check.id}/close", data={"seen": over_recorded.seen(check)}
    )
    assert "over_recorded_unchanged" in done.headers["location"]
    (check,) = checks(session)
    assert check.settled_at is None
    assert (
        "over_recorded_corrected" in correct(admin_client, session).headers["location"]
    )
