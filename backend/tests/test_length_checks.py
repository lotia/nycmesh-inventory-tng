"""What an open package really has (docs/decisions/0020, 2026-11-12;
inventory-tng-wy7): a volunteer reports it, a save below zero raises a
check, and an administrator sets it or leaves it."""

import uuid
from decimal import Decimal
from functools import partial

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import ledger, open_packages, pages
from inventory_tng.local_env import CLONE
from inventory_tng.models import (
    Admin,
    Item,
    ItemPackage,
    LedgerTransaction,
    OpenPackage,
)

from helpers.queries import counting
from helpers.rows import add, tag
from helpers.sign_in import text
from helpers.stock import Spools, cut, length, report

URL = "/api/transactions"
LENGTHS = "/admin/stock/length-checks"


@pytest.fixture
def spools(session: Session) -> Spools:
    return Spools(session)


def left_on(session: Session, made: OpenPackage) -> Decimal:
    return ledger.left_on(session, [made])[made.id]


def seen(session: Session, made: OpenPackage) -> str:
    """What the check page shows of its check."""
    session.refresh(made)
    return open_packages.check_seen(made)


def set_form(session: Session, made: OpenPackage, left: str) -> dict[str, str]:
    """The check page's own Set form for it, as shown now."""
    return {
        "left": left,
        "seen": seen(session, made),
        "showed": str(left_on(session, made)),
    }


def ticked(session: Session, *made: OpenPackage) -> list[str]:
    """Boxes ticked on the check page."""
    return [f"{each.id} {seen(session, each)}" for each in made]


def test_the_check_page_hints_at_the_printed_length(
    admin_client: TestClient,
    registered_client: TestClient,
    spools: Spools,
) -> None:
    made = spools.opened()
    registered_client.post(URL, json=report(spools, length(made, "250")))
    page = text(admin_client.get(LENGTHS))
    assert pages.PRINTED_LENGTH in page
    assert f'aria-describedby="hint-{made.id}"' in page


def test_a_volunteer_reports_what_is_on_it_and_nothing_moves(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    made = spools.opened()
    batch = report(spools, length(made, "250.5"))
    assert registered_client.post(URL, json=batch).status_code == 201
    # Sent again, as sent: the same transaction.
    assert registered_client.post(URL, json=batch).status_code == 200
    session.refresh(made)
    assert made.length_reported == Decimal("250.5")
    assert made.length_reported_by_volunteer_id == spools.place.volunteer.id
    assert made.length_reported_in_id is not None
    # A report, not an adjustment: it still shows all 305 m.
    assert left_on(session, made) == 305
    # Sent again with another figure: refused as the key reused.
    other = batch | {"lengths": [length(made, "200")]}
    assert registered_client.post(URL, json=other).status_code == 409


def test_a_report_rides_with_a_cut(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    made = spools.opened()
    batch = spools.place.batch(lines=[cut(made, "310")], lengths=[length(made, "12")])
    assert registered_client.post(URL, json=batch).status_code == 201
    session.refresh(made)
    assert (made.length_reported, made.below_zero_at is not None) == (12, True)


def test_a_report_that_cannot_be_is_refused(
    registered_client: TestClient, spools: Spools
) -> None:
    made = spools.opened()

    def refused(batch: dict[str, object]) -> list[tuple[list[object], str]]:
        response = registered_client.post(URL, json=batch)
        assert response.status_code == 422
        return [(p["loc"][1:], p["type"]) for p in response.json()["detail"]]

    nobody = {"open_package_key": str(uuid.uuid4()), "left": "1"}
    assert refused(report(spools, nobody)) == [
        (["lengths", 0, "open_package_key"], "not_found")
    ]
    assert refused(report(spools, length(made, "1"), length(made, "2"))) == [
        (["lengths", 0, "open_package_key"], "repeated"),
        (["lengths", 1, "open_package_key"], "repeated"),
    ]
    elsewhere = report(spools, length(made, "1")) | {"from_location_id": spools.van.id}
    assert refused(elsewhere) == [
        (["lengths", 0, "open_package_key"], "open_package_elsewhere")
    ]
    # Only from where it is: never a receipt; never less than nothing.
    receipt = spools.place.batch(
        kind="receipt", from_location_id=None, lengths=[length(made, "1")]
    )
    negative = report(spools, length(made, "-1"))
    empty = spools.place.batch(kind="report", to_location_id=None, lines=[])
    for batch in (receipt, negative, empty):
        assert registered_client.post(URL, json=batch).status_code == 422


def test_a_cut_below_zero_raises_a_check_once(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    made = spools.opened()
    assert (
        registered_client.post(
            URL, json=spools.place.batch(lines=[cut(made, "305")])
        ).status_code
        == 201
    )
    session.refresh(made)
    # Nothing left is not less than nothing.
    assert made.below_zero_at is None
    below = spools.place.batch(lines=[cut(made, "5")])
    assert registered_client.post(URL, json=below).status_code == 201
    session.refresh(made)
    raised = made.below_zero_at
    assert raised is not None
    again = spools.place.batch(lines=[cut(made, "5")])
    assert registered_client.post(URL, json=again).status_code == 201
    session.refresh(made)
    assert made.below_zero_at == raised


def test_needs_your_action_lists_lengths_to_check(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    spools: Spools,
) -> None:
    reported, below = spools.opened(), spools.opened()
    registered_client.post(URL, json=report(spools, length(reported, "250")))
    registered_client.post(URL, json=spools.place.batch(lines=[cut(below, "310")]))
    home = text(admin_client.get("/admin"))
    assert "Needs your action (2)" in home
    assert "Shows the wrong length" in home
    assert "Went below zero" in home
    assert (
        f"{spools.cable.name}: 1 \N{MULTIPLICATION SIGN} Spool</a>, 5 m below zero"
        in home
    )
    page = text(admin_client.get(f"{LENGTHS}?item={spools.cable.id}"))
    assert f"Only {spools.cable.name}." in page
    assert "said 250 m is on it" in page
    # Set against what it showed.
    assert 'name="showed" value="305.000"' in page
    assert "310 m" in page  # The recent cut.
    # No such item: all of them.
    assert "Only" not in text(admin_client.get(f"{LENGTHS}?item=999999"))


def test_set_counts_it_at_what_it_really_has(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    spools: Spools,
) -> None:
    made = spools.opened()
    registered_client.post(URL, json=report(spools, length(made, "250")))
    form = set_form(session, made, "260")
    done = admin_client.post(f"{LENGTHS}/{made.id}/set", data=form)
    assert done.headers["location"] == f"{LENGTHS}?notice=length_set"
    session.refresh(made)
    assert made.length_reported is None
    assert left_on(session, made) == 260
    counted = session.scalars(
        select(LedgerTransaction).where(
            LedgerTransaction.kind == "count",
            LedgerTransaction.from_location_id == spools.store.id,
        )
    ).one()
    assert counted.admin_id is not None
    # Asked again, nothing more: it was settled.
    again = admin_client.post(f"{LENGTHS}/{made.id}/set", data=form | {"left": "200"})
    assert again.headers["location"] == f"{LENGTHS}?notice=length_unchanged"
    assert left_on(session, made) == 260


def test_set_finds_what_was_more_than_it_showed(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    spools: Spools,
) -> None:
    made = spools.opened()
    registered_client.post(URL, json=spools.place.batch(lines=[cut(made, "310")]))
    form = set_form(session, made, "3")
    admin_client.post(f"{LENGTHS}/{made.id}/set", data=form)
    assert left_on(session, made) == 3


def test_set_keeps_a_cut_since_the_report(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    spools: Spools,
) -> None:
    """The reported figure is set against what it showed at the report, so
    a cut since stays (inventory-tng-b1z)."""
    made, other = spools.opened(), spools.opened()
    registered_client.post(URL, json=report(spools, length(made, "250")))
    registered_client.post(URL, json=spools.place.batch(lines=[cut(made, "10")]))
    registered_client.post(URL, json=report(spools, length(other, "100")))
    done = admin_client.post(
        LENGTHS,
        data={"open_package": ticked(session, made, other), "do": "set"},
    )
    assert done.headers["location"] == f"{LENGTHS}?notice=length_set"
    session.refresh(made)
    session.refresh(other)
    assert made.length_reported is None
    assert other.length_reported is None
    assert left_on(session, other) == 100
    # Showed 305 at the report, really 250; cut 10 since.
    assert left_on(session, made) == 240


def test_leave_it_and_what_is_refused(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    spools: Spools,
) -> None:
    made = spools.opened()
    registered_client.post(URL, json=report(spools, length(made, "250")))
    only = f"?item={spools.cable.id}"
    for bad in ({"left": "x"}, {"left": ""}):
        refused = admin_client.post(f"{LENGTHS}/{made.id}/set{only}", data=bad)
        assert refused.headers["location"] == (f"{LENGTHS}{only}&notice=length_bad")
    none = admin_client.post(f"{LENGTHS}{only}", data={"do": "leave"})
    assert none.headers["location"] == f"{LENGTHS}{only}&notice=length_none"
    assert admin_client.post(LENGTHS, data={"do": "maybe"}).status_code == 404
    assert admin_client.post(f"{LENGTHS}/999999/leave").status_code == 404
    assert admin_client.post(f"{LENGTHS}/999999/set").status_code == 404
    left = admin_client.post(
        LENGTHS, data={"open_package": ticked(session, made), "do": "leave"}
    )
    assert left.headers["location"] == f"{LENGTHS}?notice=length_left"
    session.refresh(made)
    assert made.length_reported is None
    assert left_on(session, made) == 305
    assert "Shows the wrong length" not in text(admin_client.get("/admin"))


def test_bulk_set_passes_over_one_nobody_said_a_length_of(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    spools: Spools,
) -> None:
    made = spools.opened()
    registered_client.post(URL, json=spools.place.batch(lines=[cut(made, "310")]))
    done = admin_client.post(
        LENGTHS, data={"open_package": ticked(session, made), "do": "set"}
    )
    assert done.headers["location"] == f"{LENGTHS}?notice=length_none_said"
    session.refresh(made)
    assert made.below_zero_at is not None


def test_counting_an_open_package_twice_under_its_key_writes_once(
    session: Session, spools: Spools
) -> None:
    made = spools.opened()
    counter = add(session, Admin(username=f"counter-{tag()}"))
    key = uuid.uuid4()
    count = partial(ledger.count_open_package, session, key, counter.id, made)
    # What it shows already: nothing differs, nothing is written.
    assert count(Decimal(305), Decimal(305)) == 305
    assert left_on(session, made) == 305
    assert count(Decimal(300), Decimal(305)) == 300
    # Sent again under its key: written once.
    assert count(Decimal(290), Decimal(305)) is None
    assert left_on(session, made) == 300
    # Nothing to settle: said so.
    assert open_packages.settle_check(session, made.id, "/") is False


def test_a_report_since_the_page_was_shown_is_not_settled_unseen(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    spools: Spools,
) -> None:
    made = spools.opened()
    registered_client.post(URL, json=report(spools, length(made, "250")))
    shown, shown_at = seen(session, made), str(left_on(session, made))
    registered_client.post(URL, json=report(spools, length(made, "5")))
    for path, form in (
        ("leave", {"seen": shown}),
        ("set", {"left": "250", "seen": shown, "showed": shown_at}),
    ):
        done = admin_client.post(f"{LENGTHS}/{made.id}/{path}", data=form)
        assert done.headers["location"] == f"{LENGTHS}?notice=length_unchanged"
    session.refresh(made)
    assert made.length_reported == 5
    assert left_on(session, made) == 305


def test_each_kind_has_its_page(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    spools: Spools,
) -> None:
    reported, below = spools.opened(), spools.opened()
    registered_client.post(URL, json=report(spools, length(reported, "250")))
    registered_client.post(URL, json=spools.place.batch(lines=[cut(below, "310")]))
    only = f"?item={spools.cable.id}"
    said = text(admin_client.get(f"{LENGTHS}/reported{only}"))
    assert "said 250 m is on it" in said
    assert "Went below zero" not in said.split("<main", 1)[1]
    gone = text(admin_client.get(f"{LENGTHS}/below-zero{only}"))
    assert "said 250 m" not in gone
    assert f'href="{LENGTHS}/below-zero">Show all' in gone
    assert admin_client.get(f"{LENGTHS}/nothing").status_code == 404


def test_a_count_leaves_a_check_to_be_answered_but_a_close_ends_it(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    spools: Spools,
) -> None:
    """The count form shows what the records say, not the report: one left
    as it was says nothing of it. Closed, nobody checks it."""
    made = spools.opened()
    registered_client.post(URL, json=report(spools, length(made, "250")))
    store = spools.store.id
    count = {
        "key": str(uuid.uuid4()),
        "as_of": str(ledger.moment(session, store)),
        f"opened_{made.id}": "305",
    }
    assert admin_client.post(f"/admin/counts/{store}", data=count).status_code == 303
    session.refresh(made)
    assert made.length_reported == 250
    closer = add(session, Admin(username=f"closer-{tag()}"))
    open_packages.close(session, made.id, closer.id, made.opened_at)
    session.flush()
    session.refresh(made)
    assert (made.length_reported, made.length_reported_in_id) == (None, None)


def test_one_reported_used_up_is_not_also_below_zero(
    admin_client: TestClient,
    registered_client: TestClient,
    spools: Spools,
) -> None:
    """Its shortfall shows on the confirm page's line."""
    made = spools.opened()
    over = spools.place.batch(lines=[cut(made, "310")], used_up=[str(made.key)])
    assert registered_client.post(URL, json=over).status_code == 201
    home = text(admin_client.get("/admin"))
    assert "Needs your action (1)" in home
    assert "Went below zero" not in home


def test_answers_go_back_to_the_page_they_came_from(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    spools: Spools,
) -> None:
    made = spools.opened()
    registered_client.post(URL, json=spools.place.batch(lines=[cut(made, "310")]))
    below = f"{LENGTHS}/below-zero"
    done = admin_client.post(
        f"{LENGTHS}/{made.id}/leave",
        data={"seen": seen(session, made), "page": below},
    )
    assert done.headers["location"] == f"{below}?notice=length_left"
    elsewhere = admin_client.post(
        LENGTHS, data={"do": "leave", "page": "https://elsewhere.example"}
    )
    assert elsewhere.headers["location"] == f"{LENGTHS}?notice=length_none"


def test_a_length_of_an_item_counted_each_is_whole(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    spools: Spools,
) -> None:
    made = spools.opened()
    registered_client.post(URL, json=report(spools, length(made, "250")))
    spools.cable.unit = "each"
    session.flush()
    form = set_form(session, made, "2.5")
    done = admin_client.post(f"{LENGTHS}/{made.id}/set", data=form)
    assert done.headers["location"] == f"{LENGTHS}?notice=length_bad"


def test_a_report_of_one_closed_is_not_kept(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    made = spools.opened()
    closer = add(session, Admin(username=f"closer-{tag()}"))
    open_packages.close(session, made.id, closer.id, made.opened_at)
    session.flush()
    transaction = session.scalars(select(LedgerTransaction)).first()
    assert transaction is not None
    assert not open_packages.report_length(
        made, Decimal(1), spools.place.volunteer.id, transaction
    )
    session.refresh(made)
    assert made.length_reported is None


# The hint to read the printed length (inventory-tng-pg7).


def test_the_count_page_hints_at_the_printed_length_of_items_in_length(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    store = spools.store.id
    spools.opened()
    made = spools.opened()
    page = text(admin_client.get(f"/admin/counts/{store}"))
    # Each open package's, and under one nobody recorded (inventory-tng-lby).
    assert page.count(pages.PRINTED_LENGTH) == 3
    assert f'aria-describedby="hint-opened_{made.id}"' in page
    assert 'aria-describedby="hint-found_left"' in page
    # Beside a box of plugs, counted each: only the spool's option is one
    # the hint fits, as the page follows what is picked.
    plugs = add(
        session,
        Item(
            name=f"Plugs {tag()}",
            category_id=spools.cable.category_id,
            track_opened=True,
        ),
    )
    box = add(session, ItemPackage(item_id=plugs.id, name="Box", size=Decimal(100)))
    page = admin_client.get(f"/admin/counts/{store}").text
    assert f'<option value="{spools.spool.id}"  data-hinted>' in page
    assert f'<option value="{box.id}" >' in page
    # Counted each, as the plugs are: none.
    spools.cable.unit = "each"
    session.flush()
    page = text(admin_client.get(f"/admin/counts/{store}"))
    assert pages.PRINTED_LENGTH not in page
    assert "hint-found_left" not in page


@pytest.mark.clone
def test_the_scan_page_hints_in_the_same_words() -> None:
    card = (CLONE / "frontend" / "src" / "OpenPackageCard.tsx").read_text()
    assert f'"{pages.PRINTED_LENGTH}"' in card


def test_set_moves_it_by_what_it_was_off_so_cuts_since_stay(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    spools: Spools,
) -> None:
    """What is typed is what it had when the page showed it: a cut since,
    or one under way as the page was read, stays as saved
    (inventory-tng-b1z)."""
    made = spools.opened()
    registered_client.post(URL, json=spools.place.batch(lines=[cut(made, "310")]))
    form = set_form(session, made, "3")
    registered_client.post(URL, json=spools.place.batch(lines=[cut(made, "1")]))
    # A form that does not say what it showed: nothing to set it against.
    for stale in ("", "x", "NaN"):
        refused = admin_client.post(
            f"{LENGTHS}/{made.id}/set", data=form | {"showed": stale}
        )
        assert refused.headers["location"] == f"{LENGTHS}?notice=length_reload"
    done = admin_client.post(f"{LENGTHS}/{made.id}/set", data=form)
    assert done.headers["location"] == f"{LENGTHS}?notice=length_set"
    # Showed -5, really had 3; the cut of 1 since stays.
    assert left_on(session, made) == 2


def test_a_place_with_no_saves_is_at_nothing(session: Session, spools: Spools) -> None:
    assert ledger.moment(session, spools.van.id) == 0


def test_the_page_locks_nothing(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    spools: Spools,
) -> None:
    """Each place's latest transaction read, not locked (inventory-tng-a34);
    that it reads alike for one package or eleven is test_query_counts'."""
    for whole in (False, True):
        made = spools.opened()
        registered_client.post(URL, json=spools.place.batch(lines=[cut(made, "1")]))
        registered_client.post(URL, json=report(spools, length(made, "250")))
        if whole:
            # Moved whole to the van: a place more to read.
            moved = cut(made, "304") | {"whole": True}
            registered_client.post(URL, json=spools.place.batch(lines=[moved]))
    at_van = select(OpenPackage).where(OpenPackage.location_id == spools.van.id)
    assert session.scalars(at_van).first() is not None
    with counting(session) as sent:
        assert admin_client.get(LENGTHS).status_code == 200
    assert sent
    assert not [each for each in sent if " FOR " in each]


def test_with_nothing_to_check_the_page_says_so(admin_client: TestClient) -> None:
    page = text(admin_client.get(f"{LENGTHS}/below-zero?item=999999"))
    assert "Nothing to check." in page


def test_a_figure_other_than_the_volunteers_is_set_against_the_page(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    spools: Spools,
) -> None:
    """The volunteer's figure is set against what it showed at the report;
    one the administrator measured, against what the page showed."""
    made = spools.opened()
    registered_client.post(URL, json=report(spools, length(made, "250")))
    registered_client.post(URL, json=spools.place.batch(lines=[cut(made, "10")]))
    form = set_form(session, made, "240")
    done = admin_client.post(f"{LENGTHS}/{made.id}/set", data=form)
    assert done.headers["location"] == f"{LENGTHS}?notice=length_set"
    assert left_on(session, made) == 240


def test_a_set_that_leaves_it_below_zero_asks_again(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    spools: Spools,
) -> None:
    """Cuts since took more than it really had: still wrong, asked about."""
    made = spools.opened()
    registered_client.post(URL, json=report(spools, length(made, "250")))
    registered_client.post(URL, json=spools.place.batch(lines=[cut(made, "300")]))
    done = admin_client.post(
        LENGTHS, data={"open_package": ticked(session, made), "do": "set"}
    )
    assert done.headers["location"] == f"{LENGTHS}?notice=length_set"
    session.refresh(made)
    assert left_on(session, made) == -50
    assert (made.length_reported, made.below_zero_at is not None) == (None, True)


def test_a_count_since_the_report_is_not_taken_off_twice(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    spools: Spools,
) -> None:
    """A count since the report put it right already: the report's figure
    is set against what it showed after that count, not before it."""
    made = spools.opened()
    registered_client.post(URL, json=report(spools, length(made, "250")))
    counter = add(session, Admin(username=f"counter-{tag()}"))
    ledger.count_open_package(
        session, uuid.uuid4(), counter.id, made, Decimal(250), Decimal(305)
    )
    assert left_on(session, made) == 250
    done = admin_client.post(
        LENGTHS, data={"open_package": ticked(session, made), "do": "set"}
    )
    assert done.headers["location"] == f"{LENGTHS}?notice=length_set"
    assert left_on(session, made) == 250
