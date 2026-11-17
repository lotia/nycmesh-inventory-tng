"""Counting by form (8i4.133.8): a count finds and loses stock form by
form, and the count page counts sealed and open packages, finding and
closing them."""

import uuid
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import ledger
from inventory_tng.admin import counts
from inventory_tng.ledger import LOOSE, Entry, Form
from inventory_tng.models import AdjustmentReason, Admin, LedgerTransaction, OpenPackage

from helpers.package_forms import CABLE, OPEN, SEALED, forms, opened
from helpers.rows import add, breaks, tag
from helpers.sign_in import text
from helpers.stock import Spools

# Counting by form (8i4.133.8).


def test_a_count_by_form_finds_and_loses_form_by_form() -> None:
    then = {(CABLE, SEALED): Decimal(610), (CABLE, opened(OPEN)): Decimal(120)}
    counted = [
        (CABLE, SEALED, Decimal(305)),
        (CABLE, opened(OPEN), Decimal(120)),
        (CABLE, LOOSE, Decimal(40)),
    ]
    found, lost = ledger.count_corrections(counted, then)
    assert found == [Entry(CABLE, Decimal(40), LOOSE, LOOSE)]
    assert lost == [Entry(CABLE, Decimal(305), SEALED, LOOSE)]


def counter(session: Session) -> int:
    return add(session, Admin(username=f"counter-{tag()}")).id


def test_record_count_by_form(session: Session, spools: Spools) -> None:
    made = spools.opened()
    store, cable = spools.store.id, spools.cable.id
    as_of = ledger.moment(session, store)
    lines = [
        (cable, spools.sealed, Decimal(0)),
        (cable, Form(open_package_id=made.id), Decimal(250)),
        (cable, LOOSE, Decimal(20)),
    ]
    done = ledger.record_count(
        session, uuid.uuid4(), counter(session), store, [], as_of, by_form=lines
    )
    assert (done.differed, done.moved) == (True, {})
    assert forms(session, spools.cable) == {
        (store, None, made.id): 250,
        (store, None, None): 20,
    }
    with breaks(session, "movement_item_once"):
        ledger.record_count(
            session,
            uuid.uuid4(),
            counter(session),
            store,
            [(cable, Decimal(1))],
            ledger.moment(session, store),
            by_form=lines[:1],
        )
    with breaks(session, "movement_item_once"):
        ledger.record_count(
            session,
            uuid.uuid4(),
            counter(session),
            store,
            [],
            ledger.moment(session, store),
            by_form=[lines[2], lines[2]],
        )


def test_an_opening_while_counting_by_form_asks_again(
    session: Session, spools: Spools
) -> None:
    store, cable = spools.store.id, spools.cable.id
    as_of = ledger.moment(session, store)
    spools.opened()
    done = ledger.record_count(
        session,
        uuid.uuid4(),
        counter(session),
        store,
        [],
        as_of,
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
    page = text(admin_client.get(f"{COUNTS}/{store}"))
    assert f'name="sealed_{cable}_{spools.spool.id}"' not in page  # None sealed now.
    assert f'name="opened_{one.id}"' in page
    assert f'name="state_{two.id}"' in page
    form = {
        "key": str(uuid.uuid4()),
        "as_of": str(ledger.moment(session, store)),
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
    held = forms(session, spools.cable)
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
    assert f'name="{field}"' in text(admin_client.get(f"{COUNTS}/{store}"))
    as_of = str(ledger.moment(session, store))
    base = {"as_of": as_of}
    done = admin_client.post(
        f"{COUNTS}/{store}", data=base | {"key": str(uuid.uuid4()), field: "1"}
    )
    assert done.status_code == 303
    # Its loose line, left as the records had it, is counted as it was.
    assert forms(session, spools.cable) == {
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
    assert str(counts.WHOLE_PACKAGES) in text(response)
    left = base | {
        "key": str(uuid.uuid4()),
        "found_package_id": str(spool),
        "found_left": "",
    }
    assert str(counts.HOW_MUCH_LEFT) in text(
        admin_client.post(f"{COUNTS}/{store}", data=left)
    )


def test_counting_by_form_asks_again_for_what_moved(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    store, cable, spool = spools.store.id, spools.cable.id, spools.spool.id
    form = {
        "key": str(uuid.uuid4()),
        "as_of": str(ledger.moment(session, store)),
        f"sealed_{cable}_{spool}": "2",
    }
    spools.opened()  # While it was counted.
    again = admin_client.post(f"{COUNTS}/{store}", data=form)
    assert again.status_code == 409
    page = text(again)
    assert "These moved while you counted" in page
    # Asked again by form.
    assert f'name="sealed_{cable}_{spool}"' in page


def test_an_open_package_found_where_its_item_was_counted_by_total(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    van, cable = spools.van.id, spools.cable.id
    spools.move(Entry(cable, Decimal(10)))  # Loose, alone, in the van.
    form = {
        "key": str(uuid.uuid4()),
        "as_of": str(ledger.moment(session, van)),
        f"counted_{cable}": "10",
        "found_package_id": str(spools.spool.id),
        "found_left": "40",
    }
    assert admin_client.post(f"{COUNTS}/{van}", data=form).status_code == 303
    held = forms(session, spools.cable)
    assert held[(van, None, None)] == 10
    assert sorted(
        amount for (place, _, opened), amount in held.items() if place == van and opened
    ) == [40]


def test_open_packages_closed_by_a_count_and_refusals_said(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    store, cable, spool = spools.store.id, spools.cable.id, spools.spool.id
    one, two = spools.opened(), spools.opened()
    as_of = str(ledger.moment(session, store))
    # Not here, its left box cleared: closed all the same.
    gone = {
        "key": str(uuid.uuid4()),
        "as_of": as_of,
        f"opened_{one.id}": "",
        f"state_{one.id}": "used_up",
    }
    assert admin_client.post(f"{COUNTS}/{store}", data=gone).status_code == 303
    session.refresh(one)
    assert one.closed_at is not None
    closed = {"key": str(uuid.uuid4()), "as_of": as_of, f"opened_{one.id}": "5"}
    assert "was closed meanwhile" in text(
        admin_client.post(f"{COUNTS}/{store}", data=closed)
    )
    huge = {
        "key": str(uuid.uuid4()),
        "as_of": as_of,
        f"sealed_{cable}_{spool}": "9999999",
    }
    # None sealed is here now, so no line shows it: refused whole.
    assert admin_client.post(f"{COUNTS}/{store}", data=huge).status_code == 400
    spools.cable.track_opened = False
    session.flush()
    untracked = {
        "key": str(uuid.uuid4()),
        "as_of": str(ledger.moment(session, store)),
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
    key = uuid.uuid4()
    form = {
        "key": str(key),
        "as_of": str(ledger.moment(session, van)),
        f"counted_{cable}": "10",
        "found_package_id": str(spools.spool.id),
        "found_left": "40",
    }
    spools.move(Entry(cable, Decimal(5)))  # While it was counted.
    assert admin_client.post(f"{COUNTS}/{van}", data=form).status_code == 409
    made = select(OpenPackage).where(OpenPackage.key == uuid.uuid5(key, "found"))
    assert session.scalars(made).first() is None
