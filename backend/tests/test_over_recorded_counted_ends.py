"""A count of an end puts that end right, and an opening's ends, both at
one place, are put right only by their form (Ali, 2026-11-15,
inventory-tng-qs8, inventory-tng-r5z)."""

import uuid
from decimal import Decimal
from functools import partial

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng import ledger, open_packages, over_recorded
from inventory_tng.admin import length_checks
from inventory_tng.admin import over_recorded as over_recorded_page
from inventory_tng.admin.counts import COUNTS
from inventory_tng.db import attempt
from inventory_tng.ledger import expected
from inventory_tng.local_time import show_day, site_zone
from inventory_tng.models import ItemPackage, LedgerTransaction, Movement

from helpers.markup import read
from helpers.over_recorded import (
    PAGE,
    admin_id,
    arrived_field,
    checks,
    count_ids,
    moved_whole,
    opened_into,
    opening_raised,
    raised,
    recorded_then_changed,
    text_of,
)
from helpers.sign_in import text
from helpers.stock import (
    URL,
    Place,
    Spools,
    count_at,
    count_form_at,
    length,
    received,
    report,
    retire,
)

# A count of an end puts it right (Ali, 2026-11-15, inventory-tng-qs8).


def radios_raised(client: TestClient, place: Place) -> None:
    """Three radios recorded moved from the store to the van, then one: two
    wait, loose at each end."""
    recorded_then_changed(
        client,
        place.batch(),
        lines=[{"item_id": place.item.id, "quantity": "1"}],
    )


def test_a_count_after_it_puts_that_end_right_and_says_what_is_left(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
) -> None:
    radios_raised(registered_client, place)
    with capture_logs() as logs:
        count_at(admin_client, place.van.id, {f"counted_{place.item.id}": "1"})
    (counted,) = count_ids(session)
    (check,) = checks(session)
    assert (check.left_count_id, check.arrived_count_id) == (None, counted)
    assert check.settled_at is None
    assert {
        "event": "over recorded end counted",
        "over_recorded_id": check.id,
        "end": "arrived",
        "ledger_transaction_id": counted,
        "admin_id": session.get_one(LedgerTransaction, counted).admin_id,
        "log_level": "info",
    } in logs
    on = session.get_one(LedgerTransaction, counted).created_at
    day = show_day(on, site_zone(session))
    page = admin_client.get(f"{PAGE}?item={place.item.id}").text
    assert (
        f"{place.van.name} counted {day}. {place.store.name} still to put right."
        in text_of(page)
    )
    assert says(page, f"Correct {place.store.name}", "button")
    link = f"{COUNTS}/{place.store.id}?item={place.item.id}"
    assert says(page, f"Count {place.store.name}", "a", href=link)
    assert "Mark checked" in page
    # Still waiting under "Needs your action".
    assert str(over_recorded_page.TITLE) in text(admin_client.get("/admin"))


def test_the_count_its_link_opens_counts_the_item_and_closes_it(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
) -> None:
    """The store's count, of the radios alone (inventory-tng-hnv), puts its
    end right too; the cable there is left as it is."""
    received(session, place, [{"item_id": place.cable.id, "quantity": "20"}])
    radios_raised(registered_client, place)
    count_at(admin_client, place.van.id, {f"counted_{place.item.id}": "1"})
    link = f"{COUNTS}/{place.store.id}?item={place.item.id}"
    assert f'name="counted_{place.cable.id}"' not in admin_client.get(link).text
    form = count_form_at(admin_client, place.store.id)
    done = admin_client.post(link, data=form | {f"counted_{place.item.id}": "2"})
    assert done.status_code == 303
    (check,) = checks(session)
    assert check.left_count_id is not None
    assert check.settled_at is not None
    assert expected(session, place.store.id)[place.cable.id] == Decimal(20)


def test_a_place_that_cannot_be_counted_is_not_offered_for_counting(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
) -> None:
    """Retired holding nothing: Correct says so, and offers to bring it
    back."""
    received(session, place)
    radios_raised(registered_client, place)
    count_at(admin_client, place.van.id, {f"counted_{place.item.id}": "1"})
    retire(session, place.store)
    page = admin_client.get(f"{PAGE}?item={place.item.id}").text
    assert says(page, f"Correct {place.store.name}", "button")
    assert f"{COUNTS}/{place.store.id}" not in page


def test_both_ends_counted_it_closes_as_counted_once(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
) -> None:
    radios_raised(registered_client, place)
    count_at(admin_client, place.van.id, {f"counted_{place.item.id}": "1"})
    shown = count_form_at(admin_client, place.store.id)
    with capture_logs() as logs:
        count_at(admin_client, place.store.id, {f"counted_{place.item.id}": "2"}, shown)
    at_van, at_store = count_ids(session)
    (check,) = checks(session)
    closing = session.get_one(LedgerTransaction, at_store)
    assert (
        check.settled_how,
        check.settled_by_admin_id,
        check.settled_at,
        check.left_count_id,
        check.arrived_count_id,
    ) == ("counted", closing.admin_id, closing.created_at, at_store, at_van)
    assert any(line["event"] == "over recorded counted" for line in logs)
    assert str(over_recorded_page.TITLE) not in text(admin_client.get("/admin"))
    # Sent again, as a second tap would: nothing more.
    count_at(admin_client, place.store.id, {f"counted_{place.item.id}": "2"}, shown)
    assert count_ids(session) == [at_van, at_store]


def test_a_count_shown_before_it_moved_puts_nothing_right(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
) -> None:
    """As of a moment before the move, it does not know of the move: the
    item moved since, so it is asked again, and nothing is put right."""
    shown = count_form_at(admin_client, place.van.id)
    radios_raised(registered_client, place)
    count_at(
        admin_client, place.van.id, {f"counted_{place.item.id}": "1"}, shown, status=409
    )
    (check,) = checks(session)
    assert (check.left_count_id, check.arrived_count_id) == (None, None)


def test_only_a_count_as_of_its_movement_or_after_puts_an_end_right(
    registered_client: TestClient, session: Session, place: Place
) -> None:
    radios_raised(registered_client, place)
    (check,) = checks(session)
    moved = session.get_one(Movement, check.movement_id).transaction_id
    admin = admin_id(session)
    # The movement's own transaction, as the count's: any will do here.
    written = [ledger.Written(moved, "count", 1)]
    at_van = partial(
        over_recorded.counted, session, admin, place.van.id, written=written
    )
    assert at_van(moved - 1, put_right=[(place.item.id, None)]) == 0
    # Another item, or nothing written (a count sent again), neither.
    assert at_van(moved, put_right=[(place.cable.id, None)]) == 0
    assert (
        over_recorded.counted(
            session, admin, place.van.id, moved, [], [(place.item.id, None)]
        )
        == 0
    )
    assert checks(session)[0].arrived_count_id is None
    assert at_van(moved, put_right=[(place.item.id, None)]) == 0
    session.flush()
    assert checks(session)[0].arrived_count_id == moved


def test_a_count_of_another_form_puts_nothing_right(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
    bags: ItemPackage,
) -> None:
    """Counted by form, an end is put right by the form it is in there: the
    bags arrived sealed, so their loose cable is no count of them
    (docs/decisions/0020)."""
    raised(registered_client, place, bags)
    for where in (place.van, place.store):
        count_at(admin_client, where.id, {f"loose_{place.cable.id}": "0"})
    (check,) = checks(session)
    assert (check.left_count_id, check.arrived_count_id) == (None, None)
    count_at(admin_client, place.van.id, {f"sealed_{place.cable.id}_{bags.id}": "1"})
    (check,) = checks(session)
    assert check.arrived_count_id is not None


@pytest.mark.parametrize("at_van", [True, False])
def test_one_end_counted_correct_writes_the_other_end_only(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
    at_van: bool,
) -> None:
    radios_raised(registered_client, place)
    (check,) = checks(session)
    before = over_recorded.seen(check)
    counted, other = (place.van, place.store) if at_van else (place.store, place.van)
    count_at(admin_client, counted.id, {f"counted_{place.item.id}": "1"})
    page = text_of(admin_client.get(f"{PAGE}?item={place.item.id}").text)
    assert f"{counted.name} counted " in page
    assert f"{other.name} still to put right." in page
    # Seen before the count: it changed meanwhile.
    stale = admin_client.post(f"{PAGE}/{check.id}/correct", data={"seen": before})
    assert "over_recorded_unchanged" in stale.headers["location"]
    (check,) = checks(session)
    form = {"seen": over_recorded.seen(check)}
    done = admin_client.post(f"{PAGE}/{check.id}/correct", data=form)
    assert "over_recorded_corrected" in done.headers["location"]
    again = admin_client.post(f"{PAGE}/{check.id}/correct", data=form)
    assert "over_recorded_unchanged" in again.headers["location"]
    (adjusted,) = session.scalars(
        select(LedgerTransaction).where(LedgerTransaction.kind == "adjustment")
    )
    moved = session.get_one(
        LedgerTransaction, session.get_one(Movement, check.movement_id).transaction_id
    )
    # Found again where it left, or written off where it arrived, under a
    # key of its own, not a full Correct's.
    side = "found" if at_van else "off"
    assert (adjusted.from_location_id, adjusted.to_location_id) == (
        (None, place.store.id) if at_van else (place.van.id, None)
    )
    assert adjusted.idempotency_key == uuid.uuid5(
        moved.idempotency_key, f"over recorded {check.id} {side} only"
    )
    (check,) = checks(session)
    assert check.settled_how == "corrected"
    assert (check.left_count_id is None, check.arrived_count_id is None) == (
        at_van,
        not at_van,
    )


def test_one_moved_whole_is_closed_by_a_count_of_its_open_package_where_it_arrived(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    spools = Spools(session)
    made = moved_whole(registered_client, spools)
    # Where it left is no end of it; nor is its cable counted by its total,
    # or loose, a count of the open package.
    count_at(admin_client, spools.store.id, {f"counted_{spools.cable.id}": "0"})
    count_at(admin_client, spools.van.id, {f"counted_{spools.cable.id}": "300"})
    count_at(admin_client, spools.van.id, {f"loose_{spools.cable.id}": "0"})
    (check,) = checks(session)
    assert (check.left_count_id, check.arrived_count_id) == (None, None)
    count_at(admin_client, spools.van.id, {f"opened_{made.id}": "300"})
    (check,) = checks(session)
    assert check.settled_how == "counted"
    assert check.left_count_id is None
    assert check.arrived_count_id == count_ids(session)[-1]


def test_one_moved_whole_is_closed_by_setting_its_length_where_it_arrived(
    registered_client: TestClient, session: Session
) -> None:
    spools = Spools(session)
    made = moved_whole(registered_client, spools)
    made.below_zero_at = made.opened_at  # A check to settle.
    session.flush()
    moment = session.scalar(select(func.max(LedgerTransaction.id)))
    assert moment is not None
    answer = length_checks.Answer(
        made.id, open_packages.check_seen(made), Decimal(300), Decimal(305), moment
    )
    assert length_checks._set(session, admin_id(session), answer, [spools.van.id]) == (
        True,
        False,
        1,
    )
    (check,) = checks(session)
    assert check.settled_how == "counted"


def test_only_a_report_after_the_batch_that_moved_it_whole_puts_it_right(
    registered_client: TestClient, session: Session
) -> None:
    """Set to what the batch that recorded the move reported: no count
    after the move, so it still waits. Set to a later report, it closes."""
    spools = Spools(session)
    made = moved_whole(registered_client, spools, reported="290")
    session.refresh(made)
    (check,) = checks(session)
    moved = session.get_one(Movement, check.movement_id).transaction_id
    assert made.length_reported_in_id == moved
    answer = length_checks.Answer(
        made.id, open_packages.check_seen(made), made.length_reported, as_of=moved
    )
    assert length_checks._set(session, admin_id(session), answer, [spools.van.id]) == (
        True,
        False,
        0,
    )
    (check,) = checks(session)
    assert check.settled_at is None
    assert check.arrived_count_id is None
    # Reported again at the van, after the move: that report puts it right.
    later = report(spools, length(made, "280")) | {"from_location_id": spools.van.id}
    assert registered_client.post(URL, json=later).status_code == 201
    session.refresh(made)
    assert made.length_reported_in_id is not None
    assert made.length_reported_in_id > moved
    answer = length_checks.Answer(
        made.id,
        open_packages.check_seen(made),
        made.length_reported,
        as_of=made.length_reported_in_id,
    )
    assert length_checks._set(session, admin_id(session), answer, [spools.van.id]) == (
        True,
        False,
        1,
    )
    (check,) = checks(session)
    assert check.settled_how == "counted"


@pytest.mark.parametrize(
    ("how", "ends", "broken"),
    [
        (None, (True, True), "counted_waiting"),
        ("counted", (False, False), "counted"),
        ("counted", (True, True), None),
        ("checked", (True, True), None),
        ("guessed", (True, False), "how"),
    ],
)
def test_the_database_keeps_counted_ends_only_as_counts_give_them(
    registered_client: TestClient,
    session: Session,
    place: Place,
    how: str | None,
    ends: tuple[bool, bool],
    broken: str | None,
) -> None:
    """At most one end counted while it waits; one closed as counted has
    one at least."""
    radios_raised(registered_client, place)
    (check,) = checks(session)
    moved = session.get_one(Movement, check.movement_id).transaction_id
    admin = admin_id(session)

    def settle() -> None:
        if how is not None:
            check.settled_at = func.now()
            check.settled_by_admin_id = admin
            check.settled_how = how
            check.checked_reason = "both" if how == "checked" else None
        check.left_count_id = moved if ends[0] else None
        check.arrived_count_id = moved if ends[1] else None
        session.flush()

    done = attempt(session, settle)
    assert done == (None, None if broken is None else f"ck_over_recorded_{broken}")


# An opening's ends are both at one place: a count puts each right only
# by its form (Ali, 2026-11-15, inventory-tng-r5z).


def test_a_total_count_of_an_opening_puts_nothing_right(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Screws in boxes of 100, not tracked opened, two in the van recorded
    opened though one was: 0 sealed and 200 loose, really 1 and 100. The
    van holds loose ones alone, and a total, 200, cannot show the split:
    sent as an earlier page asked it, the row waits. The page now asks by
    form (inventory-tng-dd8)."""
    spools = Spools(session, tracked=False)
    opening_raised(registered_client, spools)
    count_at(admin_client, spools.store.id, {f"counted_{spools.cable.id}": "610"})
    (check,) = checks(session)
    assert (check.left_count_id, check.arrived_count_id) == (None, None)
    assert check.settled_at is None


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("Spool", "spool"),
        ("Box", "box"),
        ("Cat6 cable", "cat6 cable"),
        ("PoE injector", "PoE injector"),
        ("SFP+ module", "SFP+ module"),
        ("UPS", "UPS"),
        ("X", "X"),
        ("", ""),
        ("A frame", "A frame"),
        ("X-ray film", "x-ray film"),
        ("Écrou", "écrou"),
        ("E\N{COMBINING ACUTE ACCENT}crou", "e\N{COMBINING ACUTE ACCENT}crou"),
        ("A\N{COMBINING RING ABOVE}SA", "A\N{COMBINING RING ABOVE}SA"),
        ("Σωλήνας", "σωλήνας"),
    ],
)
def test_a_name_within_a_sentence_lowers_its_first_letter_before_lower_case(
    name: str, shown: str
) -> None:
    """Only where the rest of its first word is lower case already, so an
    acronym or a word cased within stays; a combining mark changes
    nothing (Ali, 2026-11-15, inventory-tng-r5z)."""
    assert over_recorded.within(name) == shown


def test_an_openings_one_end_wording_keeps_a_name_cased_within(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    spools = Spools(session)
    spools.spool.name = "SFP+ module"
    session.flush()
    spools.receive(uuid.uuid4())
    opening_raised(registered_client, spools)
    count_at(
        admin_client,
        spools.store.id,
        {f"sealed_{spools.cable.id}_{spools.spool.id}": "1"},
    )
    page = admin_client.get(f"{PAGE}?item={spools.cable.id}").text
    assert "Open SFP+ module still to put right." in text_of(page)
    assert says(page, "Correct open SFP+ module", "button")


@pytest.mark.parametrize("tracked", [True, False])
def test_an_openings_sealed_count_puts_its_end_right_and_names_its_forms(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    tracked: bool,
) -> None:
    spools = Spools(session, tracked=tracked)
    spools.receive(uuid.uuid4())
    check = opening_raised(registered_client, spools)
    into = opened_into(session, check)
    assert (into.open_package_id is not None) == tracked
    sealed = f"sealed_{spools.cable.id}_{spools.spool.id}"
    count_at(admin_client, spools.store.id, {sealed: "1"})
    (counted,) = count_ids(session)
    (check,) = checks(session)
    assert (check.left_count_id, check.arrived_count_id) == (counted, None)
    assert check.settled_at is None
    on = session.get_one(LedgerTransaction, counted).created_at
    day = show_day(on, site_zone(session))
    # within(): "Cable <mark>" reads "cable <mark>".
    cable = "c" + spools.cable.name[1:]
    still = "Open spool" if tracked else f"Loose {cable}"
    page = admin_client.get(f"{PAGE}?item={spools.cable.id}").text
    assert f"Sealed spool counted {day}. {still} still to put right." in text_of(page)
    correct = "open spool" if tracked else f"loose {cable}"
    assert says(page, f"Correct {correct}", "button")
    link = f"{COUNTS}/{spools.store.id}?item={spools.cable.id}"
    assert says(page, f"Count {spools.store.name}", "a", href=link)


@pytest.mark.parametrize("tracked", [True, False])
def test_an_openings_open_count_puts_the_other_end_right_and_both_close_it(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    tracked: bool,
) -> None:
    spools = Spools(session, tracked=tracked)
    spools.receive(uuid.uuid4())
    check = opening_raised(registered_client, spools)
    arrived = arrived_field(spools, opened_into(session, check))
    count_at(admin_client, spools.store.id, {arrived: "305"})
    (at_open,) = count_ids(session)
    (check,) = checks(session)
    assert (check.left_count_id, check.arrived_count_id) == (None, at_open)
    cable = "c" + spools.cable.name[1:]
    still = "Open spool counted" if tracked else f"Loose {cable} counted"
    page = text_of(admin_client.get(f"{PAGE}?item={spools.cable.id}").text)
    assert still in page
    assert "Sealed spool still to put right." in page
    sealed = f"sealed_{spools.cable.id}_{spools.spool.id}"
    count_at(admin_client, spools.store.id, {sealed: "1"})
    _, at_sealed = count_ids(session)
    (check,) = checks(session)
    assert (check.settled_how, check.left_count_id, check.arrived_count_id) == (
        "counted",
        at_sealed,
        at_open,
    )


@pytest.mark.parametrize("tracked", [True, False])
def test_an_opening_with_one_end_counted_corrects_the_other_only(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    tracked: bool,
) -> None:
    """The sealed spool counted, Correct writes off what it became alone:
    the sealed one is not found again twice."""
    spools = Spools(session, tracked=tracked)
    spools.receive(uuid.uuid4())
    check = opening_raised(registered_client, spools)
    into = opened_into(session, check)
    count_at(
        admin_client,
        spools.store.id,
        {f"sealed_{spools.cable.id}_{spools.spool.id}": "1"},
    )
    (check,) = checks(session)
    done = admin_client.post(
        f"{PAGE}/{check.id}/correct", data={"seen": over_recorded.seen(check)}
    )
    assert "over_recorded_corrected" in done.headers["location"]
    (adjusted,) = session.scalars(
        select(LedgerTransaction).where(LedgerTransaction.kind == "adjustment")
    )
    assert (adjusted.from_location_id, adjusted.to_location_id) == (
        spools.store.id,
        None,
    )
    (line,) = session.scalars(
        select(Movement).where(Movement.transaction_id == adjusted.id)
    )
    assert (line.quantity, line.from_package_id, line.from_open_package_id) == (
        305,
        None,
        into.open_package_id,
    )


def says(page: str, words: str, tag: str, **attrs: str) -> bool:
    """Whether a link or button shows these words, then says what it is for
    only to a screen reader, in its last span."""
    return any(
        (only_said := found.all("span", class_="sr-only"))
        and found.text.removesuffix(only_said[-1].text).rstrip() == words
        for found in read(page).all(tag, **attrs)
    )
