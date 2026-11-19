"""Counting by form (8i4.133.8): a count finds and loses stock form by
form, and the count page counts sealed and open packages, finding and
closing them."""

import uuid
from collections.abc import Collection
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.models.catalogue import Category, ItemPackage
from inventory_tng.models.stock import AdjustmentReason, LedgerTransaction, OpenPackage
from inventory_tng.stock import counts, counts_routes, moments, queries
from inventory_tng.stock.forms import HOW_MUCH_LEFT, WHOLE_PACKAGES
from inventory_tng.stock.ledger import LOOSE, Entry, Form

from helpers.items import new_item, same_size_twins
from helpers.markup import box_values, read, text
from helpers.package_forms import CABLE, OPEN, SEALED, open_form, quantities_by_form
from helpers.rows import add, breaks, tag
from helpers.sign_in import new_admin_id
from helpers.stock import Spools, count_form, count_form_at

# Counting by form (8i4.133.8).


def test_a_count_by_form_finds_and_loses_form_by_form() -> None:
    then = {(CABLE, SEALED): Decimal(610), (CABLE, open_form(OPEN)): Decimal(120)}
    counted = [
        (CABLE, SEALED, Decimal(305)),
        (CABLE, open_form(OPEN), Decimal(120)),
        (CABLE, LOOSE, Decimal(40)),
    ]
    found, lost = counts.count_corrections(counted, then)
    assert found == [Entry(CABLE, Decimal(40), LOOSE, LOOSE)]
    assert lost == [Entry(CABLE, Decimal(305), SEALED, LOOSE)]


def test_record_count_by_form(session: Session, spools: Spools) -> None:
    made = spools.opened()
    store, cable = spools.store.id, spools.cable.id
    shown = moments.moment(session, store)
    lines = [
        (cable, spools.sealed, Decimal(0)),
        (cable, Form(open_package_id=made.id), Decimal(250)),
        (cable, LOOSE, Decimal(20)),
    ]
    done = counts.record_count(
        session, uuid.uuid4(), new_admin_id(session), store, [], shown, by_form=lines
    )
    assert (done.differed, done.moved) == (True, {})
    assert quantities_by_form(session, spools.cable) == {
        (store, None, made.id): 250,
        (store, None, None): 20,
    }
    with breaks(session, "movement_item_once"):
        counts.record_count(
            session,
            uuid.uuid4(),
            new_admin_id(session),
            store,
            [(cable, Decimal(1))],
            moments.moment(session, store),
            by_form=lines[:1],
        )
    with breaks(session, "movement_item_once"):
        counts.record_count(
            session,
            uuid.uuid4(),
            new_admin_id(session),
            store,
            [],
            moments.moment(session, store),
            by_form=[lines[2], lines[2]],
        )


def test_an_opening_while_counting_by_form_asks_again(
    session: Session, spools: Spools
) -> None:
    store, cable = spools.store.id, spools.cable.id
    shown = moments.moment(session, store)
    spools.opened()
    done = counts.record_count(
        session,
        uuid.uuid4(),
        new_admin_id(session),
        store,
        [],
        shown,
        by_form=[(cable, spools.sealed, Decimal(610))],
    )
    assert cable in done.moved
    assert done.written == [] or all(each.lines == 0 for each in done.written)


COUNTS = "/admin/counts"


def test_the_count_page_counts_forms_finds_and_closes(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    store, cable = spools.store.id, spools.cable.id
    one, two = spools.opened(), spools.opened()
    shown = admin_client.get(f"{COUNTS}/{store}")
    page = shown.text
    assert not box_values(page, f"sealed_{cable}_{spools.spool.id}")  # None sealed.
    assert box_values(page, f"opened_{one.id}")
    assert read(page).all("*", name=f"state_{two.id}")
    form = count_form(shown) | {
        f"opened_{one.id}": "300",
        f"opened_{two.id}": "305",
        f"state_{two.id}": "missing",
        f"loose_{cable}": "7",
        "found_package_id": str(spools.spool.id),
        "found_left": "40",
    }
    done = admin_client.post(f"{COUNTS}/{store}", data=form)
    assert done.status_code == 303, text(done)
    session.refresh(two)
    assert two.closed_at is not None
    held = quantities_by_form(session, spools.cable)
    assert held[(store, None, one.id)] == 300
    assert held[(store, None, None)] == 7
    found = session.scalars(
        select(OpenPackage).where(
            OpenPackage.key == uuid.uuid5(uuid.UUID(form["key"]), "found")
        )
    ).one()
    assert held[(store, None, found.id)] == 40
    lost = session.scalars(
        select(LedgerTransaction).where(LedgerTransaction.kind == "adjustment")
    ).one()
    assert session.get_one(AdjustmentReason, lost.reason_id).name == "Lost or stolen"
    bad = form | {"key": str(uuid.uuid4()), f"opened_{one.id}": "lots"}
    assert admin_client.post(f"{COUNTS}/{store}", data=bad).status_code == 400


def test_the_count_page_counts_sealed_and_says_whats_wrong(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    store, cable, spool = spools.store.id, spools.cable.id, spools.spool.id
    spools.write({"kind": "receipt", "to_location_id": store}, Entry(cable, Decimal(5)))
    field = f"sealed_{cable}_{spool}"
    shown = admin_client.get(f"{COUNTS}/{store}")
    assert box_values(shown.text, field)
    base = count_form(shown)
    done = admin_client.post(f"{COUNTS}/{store}", data=base | {field: "1"})
    assert done.status_code == 303
    # Its loose line, left as the records had it, is counted as it was.
    assert quantities_by_form(session, spools.cable) == {
        (store, spool, None): 305,
        (store, None, None): 5,
    }
    elsewhere = spools.opened()
    whole = Form(open_package_id=elsewhere.id)
    spools.move(Entry(cable, Decimal(305), whole, whole))
    bad = base | {
        "key": str(uuid.uuid4()),
        f"sealed_{cable}_999999": "1",
        field: "half",
        "loose_999999": "1",
        f"loose_{cable}": "lots",
        f"opened_{elsewhere.id}": "1",
        "found_package_id": "999999",
    }
    response = admin_client.post(f"{COUNTS}/{store}", data=bad)
    # Refused whole; what the page shows a field for is said by it.
    assert response.status_code == 400
    assert str(WHOLE_PACKAGES) in text(response)
    left = base | {
        "key": str(uuid.uuid4()),
        "found_package_id": str(spool),
        "found_left": "",
    }
    assert str(HOW_MUCH_LEFT) in text(admin_client.post(f"{COUNTS}/{store}", data=left))


def test_counting_by_form_asks_again_for_what_moved(
    admin_client: TestClient, spools: Spools
) -> None:
    store, cable, spool = spools.store.id, spools.cable.id, spools.spool.id
    form = count_form_at(admin_client, store) | {f"sealed_{cable}_{spool}": "2"}
    spools.opened()  # While it was counted.
    again = admin_client.post(f"{COUNTS}/{store}", data=form)
    assert again.status_code == 409
    assert "These moved while you counted" in text(again)
    # Asked again by form.
    assert box_values(again.text, f"sealed_{cable}_{spool}")


def test_an_open_package_found_where_its_item_was_counted_by_total(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    van, cable = spools.van.id, spools.cable.id
    spools.move(Entry(cable, Decimal(10)))  # Loose, alone, in the van.
    form = count_form_at(admin_client, van) | {
        f"counted_{cable}": "10",
        "found_package_id": str(spools.spool.id),
        "found_left": "40",
    }
    assert admin_client.post(f"{COUNTS}/{van}", data=form).status_code == 303
    held = quantities_by_form(session, spools.cable)
    assert held[(van, None, None)] == 10
    assert sorted(
        amount for (place, _, opened), amount in held.items() if place == van and opened
    ) == [40]


def test_open_packages_closed_by_a_count_and_refusals_said(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    store, cable, spool = spools.store.id, spools.cable.id, spools.spool.id
    one, two = spools.opened(), spools.opened()
    at = count_form_at(admin_client, store)
    # Not here, its left box cleared: closed all the same.
    gone = at | {f"opened_{one.id}": "", f"state_{one.id}": "used_up"}
    assert admin_client.post(f"{COUNTS}/{store}", data=gone).status_code == 303
    session.refresh(one)
    assert one.closed_at is not None
    closed = at | {"key": str(uuid.uuid4()), f"opened_{one.id}": "5"}
    assert "was closed meanwhile" in text(
        admin_client.post(f"{COUNTS}/{store}", data=closed)
    )
    huge = at | {"key": str(uuid.uuid4()), f"sealed_{cable}_{spool}": "9999999"}
    # None sealed is here now, so no line shows it: refused whole.
    assert admin_client.post(f"{COUNTS}/{store}", data=huge).status_code == 400
    spools.cable.track_opened = False
    session.flush()
    untracked = count_form_at(admin_client, store) | {
        "found_package_id": str(spool),
        "found_left": "40",
    }
    refused = admin_client.post(f"{COUNTS}/{store}", data=untracked)
    assert refused.status_code == 400
    assert "isn't followed once opened" in text(refused)
    assert two.closed_at is None


def test_an_open_package_found_while_its_item_moved_is_asked_again(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    """Nothing of it is kept, the open package neither: found again then."""
    van, cable = spools.van.id, spools.cable.id
    spools.move(Entry(cable, Decimal(10)))
    form = count_form_at(admin_client, van) | {
        f"counted_{cable}": "10",
        "found_package_id": str(spools.spool.id),
        "found_left": "40",
    }
    spools.move(Entry(cable, Decimal(5)))  # While it was counted.
    assert admin_client.post(f"{COUNTS}/{van}", data=form).status_code == 409
    key = uuid.UUID(form["key"])
    made = select(OpenPackage).where(OpenPackage.key == uuid.uuid5(key, "found"))
    assert session.scalars(made).first() is None


def test_current_packages_come_by_item_then_smallest_ties_by_id(
    session: Session, radios: Category
) -> None:
    item = new_item(session, radios, f"Cable {tag()}")
    first, second = same_size_twins(session, item)
    small = add(session, ItemPackage(item_id=item.id, name="Small", size=Decimal(2)))
    listed = [
        package
        for package, *_ in queries.current_packages(session)
        if package.item_id == item.id
    ]
    assert listed == [small, first, second]


def test_the_count_form_lists_sealed_smallest_first_ties_by_id(
    admin_client: TestClient,
    session: Session,
    spools: Spools,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, cable = spools.store.id, spools.cable.id
    first, second = same_size_twins(session, spools.cable, Decimal(610))
    small = add(session, ItemPackage(item_id=cable, name="Small", size=Decimal(100)))
    for package in (first, second, small):
        spools.write(
            {"kind": "receipt", "to_location_id": store},
            Entry(cable, package.size, LOOSE, Form(package_id=package.id)),
        )
    made = spools.opened()

    # The records' forms come in no set order; handed over by package id
    # descending, so only the form's own tie-break puts the twins in order.
    def descending_forms(
        session: Session, place_id: int, as_of: int, items: Collection[int]
    ) -> dict[tuple[int, Form], Decimal]:
        found = moments.expected_forms(session, place_id, as_of, items)
        return dict(
            sorted(found.items(), key=lambda pair: -(pair[0][1].package_id or 0))
        )

    monkeypatch.setattr(counts_routes, "expected_forms", descending_forms)
    page = read(admin_client.get(f"{COUNTS}/{store}").text)
    expected = [
        f"sealed_{cable}_{small.id}",
        f"sealed_{cable}_{spools.spool.id}",
        f"sealed_{cable}_{first.id}",
        f"sealed_{cable}_{second.id}",
        f"opened_{made.id}",
        f"loose_{cable}",
    ]
    names = (field.attrs.get("name") for field in page.all("input"))
    asked = [name for name in names if name in expected]
    # Sealed smallest first, ties by id; then open ones; then loose.
    assert asked == expected
