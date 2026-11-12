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

from inventory_tng import ledger, open_packages
from inventory_tng.models import Admin, LedgerTransaction, OpenPackage

from helpers.rows import add, tag
from helpers.sign_in import text
from helpers.stock import Spools

URL = "/api/transactions"
LENGTHS = "/admin/stock/length-checks"


@pytest.fixture
def spools(session: Session) -> Spools:
    return Spools(session)


def length(made: OpenPackage, left: str) -> dict[str, str]:
    return {"open_package_key": str(made.key), "left": left}


def report(spools: Spools, *lengths: dict[str, str]) -> dict[str, object]:
    return spools.place.batch(
        kind="report", to_location_id=None, lines=[], lengths=list(lengths)
    )


def cut(made: OpenPackage, quantity: str) -> dict[str, str]:
    return {
        "item_id": str(made.item_id),
        "quantity": quantity,
        "open_package_key": str(made.key),
    }


def left_on(session: Session, made: OpenPackage) -> Decimal:
    return ledger.left_on(session, [made])[made.id]


def seen(session: Session, made: OpenPackage) -> str:
    """What the check page shows of its check."""
    session.refresh(made)
    return open_packages.check_seen(made)


def ticked(session: Session, *made: OpenPackage) -> list[str]:
    """Boxes ticked on the check page."""
    return [f"{each.id} {seen(session, each)}" for each in made]


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
    assert f"{spools.cable.name}: 1 spool</a>, 5 m below zero" in home
    page = text(admin_client.get(f"{LENGTHS}?item={spools.cable.id}"))
    assert f"Only {spools.cable.name}." in page
    assert "said 250 m is on it" in page
    # Set to what was said is as of the report.
    session.refresh(reported)
    assert f'name="as_of" value="{reported.length_reported_in_id}"' in page
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
    as_of = str(ledger.moment(session, spools.store.id))
    form = {"left": "260", "as_of": as_of, "seen": seen(session, made)}
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
    as_of = str(ledger.moment(session, spools.store.id))
    form = {"left": "3", "as_of": as_of, "seen": seen(session, made)}
    admin_client.post(f"{LENGTHS}/{made.id}/set", data=form)
    assert left_on(session, made) == 3


def test_set_after_a_cut_since_changes_nothing(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    spools: Spools,
) -> None:
    """The reported figure is as of the report: a cut since is never lost."""
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
    assert made.length_reported == 250
    assert other.length_reported is None
    assert left_on(session, other) == 100
    assert left_on(session, made) == 295


def test_leave_it_and_what_is_refused(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    spools: Spools,
) -> None:
    made = spools.opened()
    registered_client.post(URL, json=report(spools, length(made, "250")))
    only = f"?item={spools.cable.id}"
    for bad in ({"left": "x", "as_of": "1"}, {"left": "1", "as_of": ""}):
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
    as_of = ledger.moment(session, spools.store.id)
    key = uuid.uuid4()
    count = partial(ledger.count_open_package, session, key, counter.id, made)
    # What it shows already: nothing differs, nothing is written.
    assert count(Decimal(305), as_of)
    assert left_on(session, made) == 305
    assert count(Decimal(300), as_of)
    # Sent again under its key: written once.
    assert count(Decimal(290), as_of)
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
    shown = seen(session, made)
    registered_client.post(URL, json=report(spools, length(made, "5")))
    as_of = str(ledger.moment(session, spools.store.id))
    for path, form in (
        ("leave", {"seen": shown}),
        ("set", {"left": "250", "as_of": as_of, "seen": shown}),
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
    form = {"left": "2.5", "as_of": "1", "seen": seen(session, made)}
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
