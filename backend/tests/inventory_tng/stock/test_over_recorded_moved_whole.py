"""One moved whole from an open package: the page asks for its count,
and a count confirmed writes nothing while one corrected adjusts it where
it arrived."""

import uuid
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng.models.catalogue import ItemPackage
from inventory_tng.models.stock import LedgerTransaction, Movement
from inventory_tng.stock import (
    adjustment_reasons,
    counts,
    over_recorded,
    over_recorded_routes,
)
from inventory_tng.stock.ledger import Form

from helpers.markup import box_values, read, text, words_on
from helpers.over_recorded import (
    PAGE,
    adjustments,
    checks,
    counted,
    moved_whole,
    one_bag_waits,
)
from helpers.rows import tag
from helpers.sign_in import new_admin
from helpers.stock import Place, Spools


def test_one_moved_whole_from_an_open_package_asks_for_its_count(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """The open package is now where it arrived, so it cannot be found again
    where it left: its count there is asked for, filled with what it held
    as it moved, under one button (Ali, 2026-11-15)."""
    spools = Spools(session)
    moved_whole(registered_client, spools)
    page = admin_client.get(f"{PAGE}?item={spools.cable.id}").text
    (check,) = checks(session)
    assert (
        f"An open package moved to {spools.van.name}. Confirm or correct count."
        in text(admin_client.get(PAGE))
    )
    assert "Count in the package" in page
    assert box_values(page, "count") == ["305"]
    was = read(page).one("p", id=f"count-{check.id}-was")
    assert (was.text, was.classes, "hidden" in was.attrs) == (
        "Was 305 m",
        ["hint"],
        True,
    )
    assert "Confirm count" in page
    assert "/checked" not in page
    assert "/correct" not in page


def test_its_count_box_shows_the_keypad_its_items_unit_takes(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Metres take a decimal point; an item counted each, whose count is
    whole (forms.confirmed_count), the number keypad (inventory-tng-qt6z).
    The cable is made each once it waits, to show the same row both ways."""
    spools = Spools(session)
    moved_whole(registered_client, spools)
    keyboards = []
    for unit in ("m", "each"):
        spools.cable.unit = unit
        session.flush()
        page = admin_client.get(PAGE).text
        keyboards.append(read(page).one("input", name="count").attrs["inputmode"])
    assert keyboards == ["decimal", "numeric"]


def test_its_count_confirmed_as_it_was_writes_nothing(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    spools = Spools(session)
    moved_whole(registered_client, spools)
    with capture_logs() as logs:
        _, response = counted(admin_client, session, "305")
    assert "over_recorded_confirmed" in response.headers["location"]
    (check,) = checks(session)
    # The movement holds the count: no reason (inventory-tng-zgy).
    assert (check.settled_how, check.checked_reason) == ("checked", None)
    assert adjustments(session) == []
    assert any(line["event"] == "over recorded confirmed" for line in logs)
    assert "Confirmed." in text(admin_client.get(response.headers["location"]))


def test_its_count_corrected_adjusts_it_where_it_arrived(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Written off at the van, from the open package, for the difference."""
    spools = Spools(session)
    made = moved_whole(registered_client, spools)
    with capture_logs() as logs:
        _, response = counted(admin_client, session, "180")
    assert "over_recorded_count_corrected" in response.headers["location"]
    assert checks(session)[0].settled_how == "corrected"
    assert adjustments(session) == [
        (spools.van.id, None, Decimal(125), made.id, None, over_recorded.REASON)
    ]
    assert any(line["event"] == "over recorded count corrected" for line in logs)
    assert str(over_recorded_routes.COUNT_CORRECTED) in text(
        admin_client.get(response.headers["location"])
    )
    # Sent again, as a second tap would: nothing more.
    _, again = counted(admin_client, session, "180", seen="305")
    assert "over_recorded_unchanged" in again.headers["location"]
    assert len(adjustments(session)) == 1


def test_its_count_corrected_under_a_key_written_already_says_so(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """The notice says how it was settled, not whether anything new was
    written: its adjustment's key written already still corrects it."""
    spools = Spools(session)
    made = moved_whole(registered_client, spools)
    (check,) = checks(session)
    movement = session.get_one(Movement, check.movement_id)
    transaction = session.get_one(LedgerTransaction, movement.transaction_id)
    key = uuid.uuid5(transaction.idempotency_key, f"over recorded {check.id} count")
    admin, _ = new_admin(session, f"ada-{tag()}", totp=False)
    reason = adjustment_reasons.seeded(session, over_recorded.REASON)
    written = counts.record_adjustment(
        session,
        key,
        admin.id,
        spools.van.id,
        spools.cable.id,
        Decimal(125),
        found=False,
        reason_id=reason.id,
        form=Form(open_package_id=made.id),
    )
    assert written
    session.commit()
    _, response = counted(admin_client, session, "180")
    assert "over_recorded_count_corrected" in response.headers["location"]
    assert checks(session)[0].settled_how == "corrected"


def test_its_count_raised_is_found_in_it(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    spools = Spools(session)
    made = moved_whole(registered_client, spools)
    counted(admin_client, session, "310")
    assert adjustments(session) == [
        (None, spools.van.id, Decimal(5), None, made.id, over_recorded.REASON)
    ]


def test_a_count_not_a_number_is_asked_again(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    spools = Spools(session)
    moved_whole(registered_client, spools)
    for typed, problem in (("lots", "Type a number"), ("-1", "Use 0 or more.")):
        check, response = counted(admin_client, session, typed)
        assert response.status_code == 400
        page = response.text
        assert problem in page
        assert box_values(page, "count") == [typed]
        # Edited, what it held shows under it.
        was = read(page).one("p", id=f"count-{check.id}-was")
        assert (was.text, was.classes, "hidden" in was.attrs) == (
            "Was 305 m",
            ["hint"],
            False,
        )
    assert checks(session)[0].settled_at is None
    assert adjustments(session) == []


def test_a_count_is_taken_only_for_an_open_package_moved_whole(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
    bags: ItemPackage,
) -> None:
    one_bag_waits(registered_client, place, bags)
    with capture_logs() as logs:
        _, response = counted(admin_client, session, "50")
    assert "over_recorded_unchanged" in response.headers["location"]
    assert checks(session)[0].settled_at is None
    assert adjustments(session) == []
    refused = [line for line in logs if line["event"] == "over recorded not settled"]
    assert refused[0]["rule"] == "over_recorded_not_whole"


def test_the_page_says_what_it_is_for(admin_client: TestClient) -> None:
    page = words_on(admin_client.get(PAGE).text)
    assert (
        "Another tab recorded these before a volunteer changed them, so the "
        "inventory shows more moved than was. Correct moves the extra back with "
        "an adjustment. Mark checked if you already fixed it." in page
    )
