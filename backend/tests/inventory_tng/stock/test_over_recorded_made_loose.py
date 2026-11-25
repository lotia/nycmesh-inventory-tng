"""A check whose open package is made loose, as turning following off
does: an opening's, or one moved whole. That end is not put right, and its
check asks for a count of the item where it was made loose, which puts it
right (Ali, 2026-11-24, inventory-tng-n7dj, inventory-tng-04z7). The
beads' example: a Spool 305 m of outdoor Cat6 cable at Shelf B, here the
spools' store, opened or moved whole to Van 2, here their van, when it
never was."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.core.db_errors import RuleBroken
from inventory_tng.core.local_time import show_day, site_zone
from inventory_tng.models.places import Location
from inventory_tng.models.stock import LedgerTransaction, OpenPackage, OverRecorded
from inventory_tng.stock import ledger, over_recorded
from inventory_tng.stock.over_recorded_queries import within
from inventory_tng.stock.paths import COUNTS

from helpers.markup import links, words_on
from helpers.over_recorded import (
    PAGE,
    adjustments,
    answered,
    arrived_field,
    checks,
    correct,
    loose_field,
    moved_whole,
    opened_into,
    opening_raised,
    reason_adjustments,
    sealed_field,
)
from helpers.stock import Spools, ada_id, count_at


def made_loose(session: Session, spools: Spools, open_ones: int = 2) -> str:
    """The cable's `open_ones` opened spools no longer followed, as its page
    saves it, by admin_client's administrator: the day, as the page shows
    it."""
    at = datetime.now(UTC)
    assert spools.unfollow(ada_id(session), at) == open_ones
    session.commit()
    return show_day(at, site_zone(session))


def loose_cable(spools: Spools) -> str:
    """The open end made loose, as it reads within a sentence."""
    return f"loose {within(spools.cable.name)}"


def page_of(client: TestClient, spools: Spools) -> str:
    return client.get(f"{PAGE}?item={spools.cable.id}").text


def asks_a_count(
    page: str, spools: Spools, day: str, at: Location | None = None
) -> bool:
    """Whether the check says Ali's words, with a link to count the place
    it was made loose `at`, else the store."""
    at = at or spools.store
    said = (
        f"What was on the open spool became loose units at {at.name} on {day}. "
        f"Count {at.name} to put this right."
    )
    link = f"{COUNTS}/{at.id}?item={spools.cable.id}"
    return said in words_on(page) and link in [href for _, href in links(page)]


def moved_to_van(session: Session, spools: Spools, check: OverRecorded) -> None:
    """The open spool a check's opening arrived in moved whole to the van."""
    on_it = ledger.Form(open_package_id=opened_into(session, check).open_package_id)
    spools.move(ledger.Entry(spools.cable.id, Decimal(305), on_it, on_it))
    session.commit()


def test_made_loose_asks_a_count_which_puts_both_ends_right(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Made loose, the open end is not put right, so Close it is not
    offered; one count of the store, sealed and loose, settles it."""
    spools = Spools(session)
    opening_raised(registered_client, spools)
    day = made_loose(session, spools)
    page = page_of(admin_client, spools)
    assert asks_a_count(page, spools, day)
    assert " closed " not in words_on(page)
    assert "Close it" not in words_on(page)
    count_at(
        admin_client,
        spools.store.id,
        {sealed_field(spools): "1", loose_field(spools): "305"},
    )
    (check,) = checks(session)
    assert check.settled_how == "counted"
    assert check.left_count_id == check.arrived_count_id
    assert check.arrived_count_id is not None
    # Settled, its open end named as what it became.
    settled = words_on(admin_client.get(f"{PAGE}/settled").text)
    assert f"{loose_cable(spools)} {day}" in settled


def test_made_loose_then_its_loose_units_counted_reads_as_after_any_count(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    spools = Spools(session)
    opening_raised(registered_client, spools)
    day = made_loose(session, spools)
    count_at(admin_client, spools.store.id, {loose_field(spools): "305"})
    (check,) = checks(session)
    assert check.settled_at is None
    assert check.arrived_count_id is not None
    said = words_on(page_of(admin_client, spools))
    assert "became loose units" not in said
    counted = f"Loose {within(spools.cable.name)}"
    assert f"{counted} counted {day}. Sealed spool still to put right." in said


def test_one_end_counted_before_it_was_made_loose(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """The sealed spool counted, then the open one made loose: it still
    waits on the loose units, and their count closes it."""
    spools = Spools(session)
    opening_raised(registered_client, spools)
    count_at(admin_client, spools.store.id, {sealed_field(spools): "1"})
    (check,) = checks(session)
    assert (check.left_count_id is not None, check.arrived_count_id) == (True, None)
    day = made_loose(session, spools)
    (check,) = checks(session)
    assert check.settled_at is None
    assert asks_a_count(page_of(admin_client, spools), spools, day)
    count_at(admin_client, spools.store.id, {loose_field(spools): "305"})
    (check,) = checks(session)
    assert check.settled_how == "counted"


def test_correct_writes_what_was_made_loose_off_the_loose_units(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    spools = Spools(session)
    opening_raised(registered_client, spools)
    made_loose(session, spools)
    assert (
        "over_recorded_corrected" in correct(admin_client, session).headers["location"]
    )
    lines = {
        (line.from_package_id, line.from_open_package_id, line.to_package_id)
        for line in reason_adjustments(session, over_recorded.REASON)
    }
    # The sealed spool found again; the loose metres written off.
    assert lines == {(None, None, spools.spool.id), (None, None, None)}


def test_a_written_off_open_package_still_puts_its_end_right(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Closed as used up, not made loose: the words say closed, as before."""
    spools = Spools(session)
    check = opening_raised(registered_client, spools)
    open_id = opened_into(session, check).open_package_id
    done = admin_client.post(
        f"/admin/items/{spools.cable.id}/open-packages/{open_id}/close"
    )
    assert "open_package_closed" in done.headers["location"]
    on = session.get_one(OpenPackage, open_id).closed_at
    assert on is not None
    said = words_on(page_of(admin_client, spools))
    day = show_day(on, site_zone(session))
    assert f"Open spool closed {day}. Sealed spool still to put right." in said
    assert "became loose units" not in said


def test_made_loose_with_its_end_counted_before_reads_as_counted(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """The open spool counted before it was made loose: that end stays put
    right, so the check waits on the sealed spool alone."""
    spools = Spools(session)
    check = opening_raised(registered_client, spools)
    count_at(
        admin_client,
        spools.store.id,
        {arrived_field(spools, opened_into(session, check)): "305"},
    )
    made_loose(session, spools)
    (row,) = checks(session)
    assert row.settled_at is None
    said = words_on(page_of(admin_client, spools))
    assert "became loose units" not in said
    # Named as what was counted then, not what it became after.
    day = show_day(
        session.get_one(LedgerTransaction, row.arrived_count_id).created_at,
        site_zone(session),
    )
    assert f"Open spool counted {day}. Sealed spool still to put right." in said


def test_made_loose_after_it_moved_away_asks_a_count_where_it_was_made_loose(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Opened at Shelf B, moved whole to Van 2, made loose there: the open
    end asks for a count of Van 2, and only one there puts it right."""
    spools = Spools(session)
    moved_to_van(session, spools, opening_raised(registered_client, spools))
    day = made_loose(session, spools)
    assert asks_a_count(page_of(admin_client, spools), spools, day, spools.van)
    count_at(admin_client, spools.store.id, {loose_field(spools): "0"})
    (row,) = checks(session)
    assert row.arrived_count_id is None
    count_at(admin_client, spools.van.id, {loose_field(spools): "305"})
    (row,) = checks(session)
    assert row.settled_at is None
    assert row.arrived_count_id is not None
    said = words_on(page_of(admin_client, spools))
    assert f"Loose {within(spools.cable.name)} counted {day}." in said
    assert "Sealed spool still to put right." in said


def test_correct_writes_what_was_made_loose_off_where_it_was_made_loose(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """The sealed spool found again at Shelf B, the loose metres written
    off at Van 2."""
    spools = Spools(session)
    moved_to_van(session, spools, opening_raised(registered_client, spools))
    made_loose(session, spools)
    assert (
        "over_recorded_corrected" in correct(admin_client, session).headers["location"]
    )
    reason = over_recorded.REASON
    assert sorted(adjustments(session), key=str) == sorted(
        [
            (None, spools.store.id, Decimal(305), None, None, reason),
            (spools.van.id, None, Decimal(305), None, None, reason),
        ],
        key=str,
    )


@pytest.mark.usefixtures("admin_client")
def test_correct_refuses_a_place_made_loose_that_it_does_not_hold(
    registered_client: TestClient, session: Session
) -> None:
    """Made loose at Van 2 after Shelf B alone was locked: Van 2 could be
    locked now only out of the ledger's order, so it is refused as
    changed, and nothing is written."""
    spools = Spools(session)
    check = opening_raised(registered_client, spools)
    moved_to_van(session, spools, check)
    made_loose(session, spools)
    (check,) = checks(session)
    with pytest.raises(RuleBroken, match="over_recorded_unchanged"):
        over_recorded.correct(
            session,
            check.id,
            over_recorded.seen(check),
            ada_id(session),
            [spools.store.id],
        )
    assert adjustments(session) == []


def test_moved_whole_then_made_loose_asks_a_count_there_which_settles_it(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Moved whole to Van 2, never there, then made loose: its count can
    no longer be confirmed, so the row asks for a count of Van 2, which
    settles it, its one end put right."""
    spools = Spools(session)
    moved_whole(registered_client, spools)
    day = made_loose(session, spools, open_ones=1)
    page = page_of(admin_client, spools)
    assert asks_a_count(page, spools, day, spools.van)
    assert "Confirm count" not in words_on(page)
    count_at(admin_client, spools.van.id, {loose_field(spools): "1"})
    (row,) = checks(session)
    assert row.settled_how == "counted"
    settled = words_on(admin_client.get(f"{PAGE}/settled").text)
    assert f"{spools.van.name} {day}" in settled


def test_moved_whole_then_made_loose_is_corrected_off_the_loose_units(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Correct writes the extra off the loose units at Van 2, and nothing
    where it left, as one moved whole has one end only."""
    spools = Spools(session)
    moved_whole(registered_client, spools)
    made_loose(session, spools, open_ones=1)
    assert (
        "over_recorded_corrected" in correct(admin_client, session).headers["location"]
    )
    reason = over_recorded.REASON
    assert adjustments(session) == [
        (spools.van.id, None, Decimal(305), None, None, reason)
    ]
    settled = words_on(admin_client.get(f"{PAGE}/settled").text)
    assert f"Corrected Adjusted at {spools.van.name}" in settled


@pytest.mark.parametrize("count", ["305", "180"])
def test_confirm_count_drawn_before_it_was_made_loose_is_refused(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    count: str,
) -> None:
    """Confirm count sent from a page drawn before its open package was
    made loose: nothing is left to count, so it is refused as changed, and
    the check still waits, writing nothing."""
    spools = Spools(session)
    moved_whole(registered_client, spools)
    made_loose(session, spools, open_ones=1)
    sent = answered(admin_client, session, "count", count=count, unit=spools.cable.unit)
    assert "over_recorded_unchanged" in sent.headers["location"]
    (row,) = checks(session)
    assert row.settled_at is None
    assert adjustments(session) == []


def test_moved_whole_and_written_off_still_asks_for_its_count(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Closed as used up at Van 2, not made loose: the row reads as one
    moved whole does, as before."""
    spools = Spools(session)
    made = moved_whole(registered_client, spools)
    done = admin_client.post(
        f"/admin/items/{spools.cable.id}/open-packages/{made.id}/close"
    )
    assert "open_package_closed" in done.headers["location"]
    said = words_on(page_of(admin_client, spools))
    assert f"An open package moved to {spools.van.name}." in said
    assert "became loose units" not in said
