"""Open packages reported used up (8i4.133.5, 8i4.133.6): volunteers
report one, a batch sent again is compared with the reports it sent, and
administrators confirm or clear reports, or close one directly."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import adjustment_reasons, ledger, open_packages
from inventory_tng.admin import needs_action, used_up
from inventory_tng.ledger import (
    Entry,
    Form,
)
from inventory_tng.models import (
    AdjustmentReason,
    Admin,
    ItemPackage,
    LedgerTransaction,
    OpenPackage,
)

from helpers.markup import button, links, read
from helpers.package_forms import forms, taken
from helpers.rows import add, tag
from helpers.sign_in import text
from helpers.stock import URL, Spools

# Volunteers report an open package used up (8i4.133.5).


def test_a_cut_reports_its_open_package_used_up_and_it_is_offered_no_more(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    made = spools.opened()
    cut = spools.place.batch(lines=[taken(made, "305")], used_up=[str(made.key)])
    assert registered_client.post(URL, json=cut).status_code == 201
    assert registered_client.post(URL, json=cut).status_code == 200
    session.refresh(made)
    assert made.reported_by_volunteer_id == spools.place.volunteer.id
    assert made.reported_at is not None
    listed = registered_client.get("/api/catalogue").json()["open_packages"]
    assert made.id not in [entry["id"] for entry in listed]
    # Its metres stay in stock: no adjustment is written.
    assert not session.scalars(
        select(LedgerTransaction).where(LedgerTransaction.kind == "adjustment")
    ).first()


# A batch sent again is compared with the reports it sent (inventory-tng-ckl).


def report_of(spools: Spools, *keys: object) -> dict[str, object]:
    return spools.place.batch(
        kind="report",
        to_location_id=None,
        lines=[],
        used_up=[str(key) for key in keys],
    )


# The reports sent first and again, of two open packages, a key nobody
# made, and (cut) whether the batch is a cut from the first, else a report.


REPORTS_CHANGED = {
    "added": ("12", "12x", False),
    "removed": ("12", "1", False),
    "swapped": ("12", "1x", False),
    "none_before": ("", "1", True),
    "none_after": ("1", "", True),
}


@pytest.mark.parametrize(
    ("first", "again", "cut"), REPORTS_CHANGED.values(), ids=list(REPORTS_CHANGED)
)
def test_a_batch_sent_again_with_other_reports_is_refused(
    registered_client: TestClient, spools: Spools, first: str, again: str, cut: bool
) -> None:
    one = spools.opened()
    keys = {"1": one.key, "2": spools.opened().key, "x": uuid.uuid4()}
    batch = spools.place.batch(lines=[taken(one, "5")]) if cut else report_of(spools)

    def reporting(marks: str) -> dict[str, object]:
        return batch | {"used_up": [str(keys[mark]) for mark in marks]}

    assert registered_client.post(URL, json=reporting(first)).status_code == 201
    response = registered_client.post(URL, json=reporting(again))
    assert (response.status_code, response.json()["reason"]) == (409, "key_reused")


def test_a_batch_sent_again_with_its_reports_in_another_order_is_replayed(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    one, two = spools.opened(), spools.opened()
    first = report_of(spools, one.key, two.key)
    saved = registered_client.post(URL, json=first)
    assert saved.status_code == 201
    again = first | {"used_up": [str(two.key), str(one.key)]}
    replayed = registered_client.post(URL, json=again)
    assert (replayed.status_code, replayed.json()) == (200, saved.json())
    # Kept as sent.
    written = session.get_one(LedgerTransaction, saved.json()["transaction_id"])
    assert written.used_up == [one.key, two.key]


def test_a_report_sent_again_after_it_was_cleared_is_replayed(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    spools: Spools,
) -> None:
    """What the batch said, not what became of its package since."""
    made = spools.opened()
    report = report_of(spools, made.key)
    saved = registered_client.post(URL, json=report)
    assert saved.status_code == 201
    cleared = admin_client.post(f"{USED_UP}/{made.id}/clear")
    assert cleared.status_code == 303
    replayed = registered_client.post(URL, json=report)
    assert (replayed.status_code, replayed.json()) == (200, saved.json())
    # Replayed, not reported again.
    session.refresh(made)
    assert made.reported_at is None


def test_a_report_of_one_reported_already_sent_again_is_replayed(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    """Reported by someone else first, so this batch reported nothing."""
    made = reported(session, spools)
    report = report_of(spools, made.key)
    saved = registered_client.post(URL, json=report)
    assert saved.status_code == 201
    replayed = registered_client.post(URL, json=report)
    assert (replayed.status_code, replayed.json()) == (200, saved.json())


def test_the_button_reports_one_alone(
    registered_client: TestClient, session: Session, spools: Spools
) -> None:
    made = spools.opened()
    report = spools.place.batch(
        kind="report", to_location_id=None, lines=[], used_up=[str(made.key)]
    )
    response = registered_client.post(URL, json=report)
    assert response.status_code == 201
    written = session.get_one(LedgerTransaction, response.json()["transaction_id"])
    assert (written.kind, written.from_location_id) == ("report", spools.store.id)
    session.refresh(made)
    reported_at = made.reported_at
    assert reported_at is not None
    # Reported again, by another batch: as it was.
    again = report | {"key": str(uuid.uuid4())}
    assert registered_client.post(URL, json=again).status_code == 201
    session.refresh(made)
    assert made.reported_at == reported_at


def test_a_report_that_cannot_be_is_refused(
    registered_client: TestClient, spools: Spools
) -> None:
    unknown = spools.place.batch(
        kind="report", to_location_id=None, lines=[], used_up=[str(uuid.uuid4())]
    )
    response = registered_client.post(URL, json=unknown)
    assert response.status_code == 422
    assert [(p["loc"][1:], p["type"]) for p in response.json()["detail"]] == [
        (["used_up", 0], "not_found")
    ]
    made = spools.opened()
    twice = report_of(spools, made.key, made.key)
    response = registered_client.post(URL, json=twice)
    assert [(p["loc"][1:], p["type"]) for p in response.json()["detail"]] == [
        (["used_up", 0], "repeated"),
        (["used_up", 1], "repeated"),
    ]
    for batch in (
        spools.place.batch(kind="report", to_location_id=None, lines=[]),
        spools.place.batch(lines=[]),
        spools.place.batch(
            kind="report", to_location_id=None, used_up=[str(uuid.uuid4())]
        ),
    ):
        assert registered_client.post(URL, json=batch).status_code == 422


def test_a_report_names_what_is_where_it_is_said_and_not_moving(
    registered_client: TestClient, spools: Spools
) -> None:
    made = spools.opened()
    key = str(made.key)

    def refused(batch: dict[str, object]) -> list[object]:
        response = registered_client.post(URL, json=batch)
        assert response.status_code == 422
        return [(p["loc"][1:], p["type"]) for p in response.json()["detail"]]

    elsewhere = spools.place.batch(
        kind="report",
        from_location_id=spools.van.id,
        to_location_id=None,
        lines=[],
        used_up=[key],
    )
    assert refused(elsewhere) == [(["used_up", 0], "open_package_elsewhere")]
    moving = spools.place.batch(lines=[taken(made, "305", whole=True)], used_up=[key])
    assert refused(moving) == [(["used_up", 0], "moved_whole")]
    receipt = spools.place.batch(
        kind="receipt",
        from_location_id=None,
        to_location_id=spools.store.id,
        used_up=[key],
    )
    assert registered_client.post(URL, json=receipt).status_code == 422


# Administrators confirm or clear reports, or close directly (8i4.133.6).


USED_UP = "/admin/stock/used-up"


def reported(session: Session, spools: Spools) -> OpenPackage:
    made = spools.opened()
    spools.move(Entry(spools.cable.id, Decimal(300), Form(open_package_id=made.id)))
    open_packages.report(
        session, [made.id], spools.place.volunteer.id, datetime.now(UTC)
    )
    session.flush()
    return made


def test_needs_your_action_until_each_report_is_answered(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    made = reported(session, spools)
    home = admin_client.get("/admin").text
    assert "Needs your action (1)" in home
    spool = f"{spools.cable.name}: 1 \N{MULTIPLICATION SIGN} Spool"
    assert (spool, f"{USED_UP}?item={spools.cable.id}") in links(home)
    assert ("All 1", USED_UP) in links(home)
    page = admin_client.get(USED_UP).text
    assert read(page).all("input", name="open_package", value=str(made.id))
    done = admin_client.post(f"{USED_UP}/{made.id}/confirm")
    assert done.headers["location"] == f"{USED_UP}?notice=used_up_confirmed"
    session.refresh(made)
    assert made.closed_at is not None
    # What it still showed, 5 m, is written off as used up: it shows nothing.
    assert (spools.store.id, None, made.id) not in forms(session, spools.cable)
    adjustment = session.scalars(
        select(LedgerTransaction).where(
            LedgerTransaction.kind == "adjustment",
            LedgerTransaction.from_location_id == spools.store.id,
        )
    ).one()
    reason = session.get_one(AdjustmentReason, adjustment.reason_id)
    assert reason.name == "Used up"
    assert "Needs your action" not in text(admin_client.get("/admin"))
    # Asked again, nothing more is written.
    admin_client.post(f"{USED_UP}/{made.id}/confirm")
    assert (
        len(
            session.scalars(
                select(LedgerTransaction).where(
                    LedgerTransaction.reason_id == reason.id
                )
            ).all()
        )
        == 1
    )


def test_needs_your_action_lists_each_item_and_what_is_below_zero(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    """One line per item, its packages in their word, a shortfall said; the
    confirm page shows one item's alone (inventory-tng-pll)."""
    reported(session, spools)
    under = spools.opened()
    spools.move(Entry(spools.cable.id, Decimal(312), Form(open_package_id=under.id)))
    open_packages.report(
        session, [under.id], spools.place.volunteer.id, datetime.now(UTC)
    )
    other = Spools(session)
    theirs = reported(session, other)
    session.flush()
    home = read(admin_client.get("/admin").text)
    assert "Needs your action (3)" in home.text
    spool = f"{spools.cable.name}: 2 \N{MULTIPLICATION SIGN} Spool"
    assert spool in [words for words, _ in links(home)]
    assert f"{spool}, 7 m below zero" in home.text
    # The other's line is its link alone.
    other_spool = f"{other.cable.name}: 1 \N{MULTIPLICATION SIGN} Spool"
    lines = home.all("li")
    assert any(
        [words for words, _ in links(line)] == [line.text] == [other_spool]
        for line in lines
    )
    only = f"{USED_UP}?item={other.cable.id}"
    page = text(admin_client.get(only))
    assert f"Only {other.cable.name}." in page
    assert spools.cable.name not in page.split("Only", 1)[1]
    # Answered there, it shows that item again.
    done = admin_client.post(f"{USED_UP}/{theirs.id}/clear?item={other.cable.id}")
    assert done.headers["location"] == f"{only}&notice=used_up_cleared"
    none = admin_client.post(f"{USED_UP}?item={other.cable.id}", data={"do": "clear"})
    assert none.headers["location"] == f"{only}&notice=used_up_none"
    # Any other item, or none, or no id: all of them.
    for item in ("", "999999", "abc", "99999999999"):
        shown = text(admin_client.get(f"{USED_UP}?item={item}"))
        assert spools.cable.name in shown
        assert "Only" not in shown


def test_an_items_packages_are_counted_by_name(
    session: Session, spools: Spools
) -> None:
    reported(session, spools)
    shorter = add(
        session,
        ItemPackage(item_id=spools.cable.id, name="Spool 150 m", size=Decimal(150)),
    )
    made = open_packages.make(session, uuid.uuid4(), shorter.id, spools.store.id)
    open_packages.report(
        session, [made.id], spools.place.volunteer.id, datetime.now(UTC)
    )
    session.flush()

    def line() -> needs_action.ItemWaiting:
        [found] = [
            each
            for each in needs_action.by_item(session, *used_up.REPORTED)
            if each.item_id == spools.cable.id
        ]
        return found

    # By name, never a word made from it (inventory-tng-b1z).
    assert (
        line().packages
        == "1 \N{MULTIPLICATION SIGN} Spool, 1 \N{MULTIPLICATION SIGN} Spool 150\u00a0m"
    )
    reel = add(
        session, ItemPackage(item_id=spools.cable.id, name="Reel", size=Decimal(100))
    )
    made = open_packages.make(session, uuid.uuid4(), reel.id, spools.store.id)
    open_packages.report(
        session, [made.id], spools.place.volunteer.id, datetime.now(UTC)
    )
    session.flush()
    every = (
        "1 \N{MULTIPLICATION SIGN} Reel, 1 \N{MULTIPLICATION SIGN} Spool,"
        " 1 \N{MULTIPLICATION SIGN} Spool 150\u00a0m"
    )
    assert (line().packages, line().below) == (every, 0)


def test_a_report_cleared_is_offered_again(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    spools: Spools,
) -> None:
    made = reported(session, spools)
    admin_client.post(f"{USED_UP}/{made.id}/clear")
    session.refresh(made)
    assert (made.reported_at, made.closed_at) == (None, None)
    listed = registered_client.get("/api/catalogue").json()["open_packages"]
    assert made.id in [entry["id"] for entry in listed]


def test_reports_ticked_are_answered_together(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    one, two = reported(session, spools), reported(session, spools)
    done = admin_client.post(
        USED_UP, data={"open_package": [str(one.id), str(two.id)], "do": "confirm"}
    )
    assert done.headers["location"] == f"{USED_UP}?notice=used_up_confirmed"
    for row in (one, two):
        session.refresh(row)
        assert row.closed_at is not None
    none = admin_client.post(USED_UP, data={"do": "clear"})
    assert none.headers["location"] == f"{USED_UP}?notice=used_up_none"
    assert admin_client.post(USED_UP, data={"do": "maybe"}).status_code == 404
    assert admin_client.post(f"{USED_UP}/{one.id}/maybe").status_code == 404
    assert admin_client.post(f"{USED_UP}/999999/confirm").status_code == 404


def test_an_open_package_is_closed_from_its_items_page(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    """Reported or not: nothing waits. Less than nothing is found."""
    made = spools.opened()
    spools.move(Entry(spools.cable.id, Decimal(310), Form(open_package_id=made.id)))
    page = f"/admin/items/{spools.cable.id}"
    assert "Close as used up" in text(admin_client.get(page))
    path = f"{page}/open-packages/{made.id}/close"
    # Asked first: it writes off what it shows.
    asked = admin_client.get(path).text
    assert "it shows\n-5 m" in asked or "it shows -5 m" in " ".join(asked.split())
    assert "danger" in button(asked, "Close as used up").classes
    done = admin_client.post(path)
    assert done.headers["location"] == (
        f"{page}?notice=open_package_closed&section=open-packages#open-packages"
    )
    # The last one closed: its section is there to say so.
    shown = text(admin_client.get(done.headers["location"]))
    assert 'id="open-packages"' in shown
    assert admin_client.get(path).status_code == 404
    session.refresh(made)
    assert made.closed_at is not None
    assert (spools.store.id, None, made.id) not in forms(session, spools.cable)
    other = f"/admin/items/{spools.place.item.id}/open-packages/{made.id}/close"
    assert admin_client.post(other).status_code == 404


def test_the_used_up_reason_is_there_whatever_administrators_did(
    session: Session,
) -> None:
    """Seeded by the initial migration; brought back if retired; made again
    if renamed away."""
    reason = adjustment_reasons.seeded(session, adjustment_reasons.USED_UP)
    assert reason.name == "Used up"
    reason.retired_at = datetime.now(UTC)
    session.flush()
    assert adjustment_reasons.seeded(session, adjustment_reasons.USED_UP) is reason
    assert reason.retired_at is None
    reason.name = "Spent"
    session.flush()
    again = adjustment_reasons.seeded(session, adjustment_reasons.USED_UP)
    assert (again.name, again.id != reason.id) == ("Used up", True)


def test_closing_or_clearing_twice_changes_nothing_more(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    made = reported(session, spools)
    closer = add(session, Admin(username=f"closer-{tag()}"))
    assert open_packages.close(session, 999999, closer.id, datetime.now(UTC)) is None
    assert open_packages.clear(session, made.id) is True
    assert open_packages.clear(session, made.id) is False
    first = open_packages.close(session, made.id, closer.id, datetime.now(UTC))
    again = open_packages.close(session, made.id, closer.id, datetime.now(UTC))
    assert (first, again) == (open_packages.Closed(False, 0), None)
    # Ticked when no longer reported: left as it is.
    done = admin_client.post(
        USED_UP, data={"open_package": [str(made.id)], "do": "clear"}
    )
    assert done.headers["location"] == f"{USED_UP}?notice=used_up_unchanged"


def test_an_open_package_showing_nothing_closes_with_nothing_written_off(
    session: Session, spools: Spools
) -> None:
    made = spools.opened()
    spools.move(Entry(spools.cable.id, Decimal(305), Form(open_package_id=made.id)))
    closer = add(session, Admin(username=f"closer-{tag()}"))
    assert open_packages.close(session, made.id, closer.id, datetime.now(UTC))
    assert not session.scalars(
        select(LedgerTransaction).where(LedgerTransaction.admin_id == closer.id)
    ).first()


def test_a_confirm_after_a_clear_closes_nothing(
    session: Session, spools: Spools
) -> None:
    """Confirming checks the report still stands, once the package is locked."""
    made = spools.opened()
    closer = add(session, Admin(username=f"closer-{tag()}"))
    when = datetime.now(UTC)
    assert open_packages.close(session, made.id, closer.id, when, reported=True) is None
    assert made.closed_at is None
    assert ledger.left_on(session, []) == {}


def test_whats_left_is_where_it_is_now(session: Session, spools: Spools) -> None:
    """Moved whole, its old place keeps a row, at nothing."""
    made = spools.opened()
    whole = Form(open_package_id=made.id)
    spools.move(Entry(spools.cable.id, Decimal(305), whole, whole))
    assert ledger.left_on(session, [made]) == {made.id: 305}


def test_those_reported_used_up_sort_and_come_in_pages(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    """The first reported first unless a heading sorts them; in pages of
    25, 50, 100 or all (Ali, 2026-11-15, inventory-tng-qwb)."""
    later = reported(session, spools)  # 5 m left.
    earlier = spools.opened()
    spools.move(Entry(spools.cable.id, Decimal(100), Form(open_package_id=earlier.id)))
    open_packages.report(
        session,
        [earlier.id],
        spools.place.volunteer.id,
        datetime(2026, 1, 1, tzinfo=UTC),
    )
    session.flush()

    def listed(**query: str) -> list[int]:
        page = admin_client.get(USED_UP, params=query).text
        ticks = read(page).all("input", name="open_package")
        return [int(tick.attrs["value"]) for tick in ticks]

    assert listed() == [earlier.id, later.id]
    assert listed(sort="reported", order="desc") == [later.id, earlier.id]
    assert listed(sort="shows") == [later.id, earlier.id]
    assert listed(sort="shows", order="desc") == [earlier.id, later.id]
    # One item, package and place: as reported, either way.
    assert listed(sort="package") == [earlier.id, later.id]
    assert listed(sort="package", order="desc") == [earlier.id, later.id]
    assert listed(per="all") == [earlier.id, later.id]
    page = read(admin_client.get(USED_UP, params={"item": str(spools.cable.id)}).text)
    assert ("Shows", f"{USED_UP}?item={spools.cable.id}&sort=shows") in links(page)
    assert ("25", f"{USED_UP}?item={spools.cable.id}&per=25") in links(page)


def test_an_answer_comes_back_to_the_used_up_list_as_it_was(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    """Its sort, page size, page and item go with each answer and come
    back; a page past the end, as after the last one on it is answered,
    shows the last there is (inventory-tng-qwb)."""
    one, two = reported(session, spools), reported(session, spools)
    view = f"item={spools.cable.id}&sort=shows&order=desc&per=25&page=2"
    page = admin_client.get(f"{USED_UP}?{view}").text
    # Drawn, the page is 1 of 1: a page past the end shows the last.
    shown = view.removesuffix("&page=2").replace("&", "&amp;")
    assert f'action="{USED_UP}?{shown}"' in page
    assert f'action="{USED_UP}/{one.id}/confirm?{shown}"' in page
    assert f'formaction="{USED_UP}/{one.id}/clear?{shown}"' in page
    done = admin_client.post(f"{USED_UP}/{one.id}/clear?{view}")
    back = done.headers["location"]
    assert back == f"{USED_UP}?{view}&notice=used_up_cleared"
    # Page 2 of 25 a page is past the end: the last page, the first.
    page = admin_client.get(back).text
    ticks = read(page).all("input", name="open_package")
    assert [t for t in ticks if t.attrs["value"].isdigit()]
    ticked = admin_client.post(
        f"{USED_UP}?{view}", data={"open_package": [str(two.id)], "do": "confirm"}
    )
    assert ticked.headers["location"] == f"{USED_UP}?{view}&notice=used_up_confirmed"
    none = admin_client.post(f"{USED_UP}?{view}", data={"do": "clear"})
    assert none.headers["location"] == f"{USED_UP}?{view}&notice=used_up_none"
