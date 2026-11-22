"""Counts and adjustments (stock/counts_routes.py), and their reasons
(site_settings/site_settings_routes.py)."""

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

import pytest
from babel.messages.catalog import Catalog
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng.catalogue import items
from inventory_tng.core.db_errors import RuleBroken
from inventory_tng.core.local_time import DEFAULT_ZONE, show
from inventory_tng.core.phrases import compiled, speaking
from inventory_tng.models.admins import Admin
from inventory_tng.models.base import INTEGER_MAX
from inventory_tng.models.catalogue import Item
from inventory_tng.models.places import Location
from inventory_tng.models.stock import AdjustmentReason, LedgerTransaction, Movement
from inventory_tng.places import locations
from inventory_tng.site_settings.forms import REASON_TAKEN
from inventory_tng.site_settings.paths import SETTINGS
from inventory_tng.stock import counts_routes
from inventory_tng.stock.counts import Counted, record_adjustment, record_count
from inventory_tng.stock.counts_routes import COUNT_KIND, SAVED_AS_SHOWN
from inventory_tng.stock.forms import (
    FOUND_OR_OFF,
    ON_THE_FORM_TWICE,
    OUT_OF_DATE,
    PICK_A_PLACE,
    PICK_A_REASON,
    PICK_AN_ITEM,
    TYPE_IT_HERE,
    lines,
    named_twice,
)
from inventory_tng.stock.ledger import LOOSE, Entry, lock_place
from inventory_tng.stock.moments import Moment, expected, moment
from inventory_tng.stock.paths import ADJUSTMENTS, COUNTS, STOCK
from inventory_tng.web.forms import ABOVE_ZERO, RULES, TYPE_A_NUMBER, WHOLE_UNITS
from inventory_tng.web.navigation import retired_holding

from helpers.concurrency import Committed, isolation_seen
from helpers.locales import ENGLISH, GERMAN
from helpers.markup import (
    addresses,
    box_values,
    button,
    forms,
    hidden_fields,
    links,
    options,
    read,
    text,
    words_on,
)
from helpers.over_recorded import opening_raised
from helpers.query_counts import counting
from helpers.rows import item_fields, shown, violates
from helpers.sign_in import new_admin, sign_in_with
from helpers.stock import (
    Place,
    Spools,
    adjustment_form,
    at_retired_van,
    badge,
    badge_of,
    committed_place,
    count_at,
    count_form,
    received,
    retire,
    stocked,
    write,
)


def key_on(response: Response) -> str:
    return dict(hidden_fields(response.text))["key"]


def counted_box(page: str, item_id: int) -> str | None:
    """What the one box counting the item holds."""
    box = read(page).one("input", name=f"counted_{item_id}")
    return box.attrs.get("value")


def counts_written(session: Session) -> list[LedgerTransaction]:
    return list(
        session.scalars(
            select(LedgerTransaction)
            .where(LedgerTransaction.kind.in_(("count", "adjustment")))
            .order_by(LedgerTransaction.id)
        )
    )


def test_the_reasons_come_seeded(session: Session) -> None:
    names = session.scalars(select(AdjustmentReason.name).order_by(AdjustmentReason.id))
    assert list(names) == [
        "Damaged",
        "Lost or stolen",
        "Found",
        "Used for testing",
        "Records were wrong",
        # The app's own: closing an open package's, and correcting one
        # recorded more than moved.
        "Used up",
        "Recorded more than moved",
    ]


def test_the_count_page_reads_an_amount_grouped_but_its_box_holds_it_plainly(
    admin_client: TestClient, session: Session
) -> None:
    """In English and in German: a box holds what the form reads back
    (inventory-tng-31q3)."""
    place = Place(session)
    received(session, place, [{"item_id": place.cable.id, "quantity": "1234.5"}])
    field = f"counted_{place.cable.id}"
    for catalogue, written in ((ENGLISH, "1,234.5 m"), (GERMAN, "1.234,5 m")):
        with speaking(catalogue):
            shown = admin_client.get(f"{COUNTS}/{place.store.id}")
        assert box_values(shown.text, field) == ["1234.5"]
        assert written in text(shown)
    # Metres, perhaps with a decimal point: the keypad with one.
    assert read(shown.text).one("input", name=field, inputmode="decimal")


def test_a_count_puts_the_records_right(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    page = admin_client.get(f"{COUNTS}/{place.store.id}").text
    assert counted_box(page, place.item.id) == "5"
    # Counted each, so whole: the number keypad.
    assert read(page).one("input", name=f"counted_{place.item.id}", inputmode="numeric")
    done = count_at(
        admin_client,
        place.store.id,
        {
            f"counted_{place.item.id}": "3",
            "extra_item_id": str(place.cable.id),
            "extra_quantity": "10.5",
        },
    )
    assert done.headers["location"] == f"{COUNTS}?notice=counted&section=count#count"
    assert expected(session, place.store.id) == {
        place.item.id: Decimal(3),
        place.cable.id: Decimal("10.5"),
    }
    found, lost = counts_written(session)
    ada = session.scalars(select(Admin)).one()
    assert (found.to_location_id, lost.from_location_id) == (place.store.id,) * 2
    assert {found.admin_id, lost.admin_id} == {ada.id}
    assert "Count" in text(admin_client.get(COUNTS))


def test_count_a_place_goes_to_the_place_picked(admin_client: TestClient) -> None:
    """The "Count a place" form, sent as a GET, goes to that place's count;
    anything but a number goes back to the list."""
    picked = admin_client.get(f"{COUNTS}/place", params={"location_id": "7"})
    assert picked.headers["location"] == f"{COUNTS}/7"
    odd = admin_client.get(f"{COUNTS}/place", params={"location_id": "x"})
    assert odd.headers["location"] == COUNTS


def test_a_count_that_matches_is_still_recorded(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    done = count_at(admin_client, place.store.id, {f"counted_{place.item.id}": "5"})
    assert (
        done.headers["location"] == f"{COUNTS}?notice=counted_same&section=count#count"
    )
    (clean,) = counts_written(session)
    assert clean.to_location_id == place.store.id
    assert (
        session.scalars(
            select(Movement).where(Movement.transaction_id == clean.id)
        ).all()
        == []
    )
    assert "as the records said" in text(admin_client.get(COUNTS))


def test_a_count_sent_twice_is_written_once(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    path = f"{COUNTS}/{place.store.id}"
    form = count_form(admin_client.get(path)) | {f"counted_{place.item.id}": "2"}
    admin_client.post(path, data=form)
    admin_client.post(path, data=form)
    assert len(counts_written(session)) == 1
    assert expected(session, place.store.id) == {place.item.id: Decimal(2)}


def test_a_count_refuses_what_is_not_a_quantity(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    path = f"{COUNTS}/{place.store.id}"
    shown = count_form(admin_client.get(path))
    for typed, problem in (("lots", TYPE_A_NUMBER), ("1.5", WHOLE_UNITS)):
        refused = admin_client.post(
            path, data=shown | {f"counted_{place.item.id}": typed}
        )
        assert refused.status_code == 400
        assert str(problem) in text(refused)
    assert counts_written(session) == []


@pytest.mark.parametrize(
    ("form", "twice"),
    [
        ({"counted_5": "1"}, set()),
        ({"counted_5": "1", "counted_6": "2"}, set()),
        ({"counted_5": "1", "counted_05": "2"}, {"counted_5", "counted_05"}),
        ({"counted_5": "1", "counted_005": ""}, {"counted_5", "counted_005"}),
        (
            {"counted_5": "1", "extra_item_id": "5", "extra_quantity": "2"},
            {"counted_5", "extra_quantity"},
        ),
        (
            {"counted_5": "1", "extra_item_id": "05", "extra_quantity": "2"},
            {"counted_5", "extra_quantity"},
        ),
        # An item chosen at the foot with no amount is not counted.
        ({"counted_5": "1", "extra_item_id": "5", "extra_quantity": " "}, set()),
        ({"counted_5": "1", "extra_item_id": "6", "extra_quantity": "2"}, set()),
        # What names no item is no item's repeat.
        ({"counted_x": "1", "counted_y": "2", "counted_0": "3"}, set()),
        ({"counted_²": "1", "counted_2": "2"}, set()),
    ],
)
def test_a_count_form_naming_an_item_twice_names_each_field(
    form: dict[str, str], twice: set[str]
) -> None:
    assert named_twice(lines(form)).keys() == twice


def test_a_count_form_reads_the_item_each_field_names() -> None:
    form = {
        "key": "k",
        "counted_07": "1",
        "counted_x": "2",
        f"counted_{INTEGER_MAX + 1}": "3",
        "extra_item_id": "8",
        "extra_quantity": "4",
    }
    assert lines(form) == [
        ("counted_07", 7, "1"),
        ("counted_x", None, "2"),
        (f"counted_{INTEGER_MAX + 1}", None, "3"),
        ("extra_quantity", 8, "4"),
    ]
    assert named_twice([("counted_5", 5, "1"), ("counted_05", 5, "2")]) == {
        "counted_5": 5,
        "counted_05": 5,
    }


def test_a_count_naming_an_item_twice_is_refused(
    admin_client: TestClient, session: Session
) -> None:
    """The page cannot send this; a hand-made form can. Nothing is written,
    and the page says why, and the same key saves the count put right."""
    place = stocked(session)
    path = f"{COUNTS}/{place.store.id}"
    shown = count_form(admin_client.get(path)) | {f"counted_{place.item.id}": "3"}
    with capture_logs() as logs:
        refused = admin_client.post(
            path, data=shown | {f"counted_0{place.item.id}": "2"}
        )
    assert refused.status_code == 400
    assert {
        "event": "count refused: an item named twice",
        "location_id": place.store.id,
        "item_ids": [place.item.id],
        "log_level": "info",
    } in logs
    page = text(refused)
    assert "An item is on this form more than once." in page
    assert "Each item is counted once" in page
    assert str(ON_THE_FORM_TWICE) in page
    assert counts_written(session) == []
    assert expected(session, place.store.id) == {place.item.id: Decimal(5)}
    done = admin_client.post(path, data=shown)
    assert done.headers["location"] == f"{COUNTS}?notice=counted&section=count#count"
    assert expected(session, place.store.id) == {place.item.id: Decimal(3)}


def test_something_else_here_listed_on_a_count_of_one_place_is_refused(
    admin_client: TestClient, session: Session
) -> None:
    """The page offers no item listed here; a hand-made form may name one,
    counted by form: it is said as on a store's count, with the place's
    name, its loose line marked (inventory-tng-fh0e). Nothing is written."""
    spools = Spools(session)
    cable = spools.cable.id
    path = f"{COUNTS}/{spools.store.id}"
    form = count_form(admin_client.get(path)) | {
        f"sealed_{cable}_{spools.spool.id}": "2",
        f"loose_{cable}": "0",
        "extra_item_id": str(cable),
        "extra_quantity": "3",
    }
    with capture_logs() as logs:
        refused = admin_client.post(path, data=form)
    assert refused.status_code == 400
    assert {
        "event": "count refused: something else here listed there",
        "location_id": spools.store.id,
        "found_location_id": spools.store.id,
        "item_id": cable,
        "log_level": "info",
    } in logs
    page = read(refused.text)
    assert page.one("span", id=f"loose_{cable}-problem").text == str(TYPE_IT_HERE)
    assert (
        page.one("span", id="extra_item_id-problem").text
        == f"Listed under {spools.store.name} already."
    )
    assert (
        f"{spools.cable.name_shown} is already listed under {spools.store.name}."
        in text(refused)
    )
    assert "An item is on this form more than once." not in text(refused)
    assert counts_written(session) == []


def test_found_and_written_off(admin_client: TestClient, session: Session) -> None:
    place = stocked(session)
    form = adjustment_form(
        session,
        place.store,
        place.item,
        key=key_on(admin_client.get(COUNTS)),
        amount="2",
    )
    done = admin_client.post(ADJUSTMENTS, data=form)
    assert done.headers["location"] == f"{COUNTS}?notice=adjusted&section=adjust#adjust"
    admin_client.post(ADJUSTMENTS, data=form)  # Twice: written once.
    (written,) = counts_written(session)
    assert (written.kind, written.reason_id) == ("adjustment", int(form["reason_id"]))
    assert expected(session, place.store.id) == {place.item.id: Decimal(3)}
    assert "Damaged: -2 each" in words_on(admin_client.get(COUNTS).text)


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"location_id": ""}, PICK_A_PLACE),
        ({"item_id": "999999"}, PICK_AN_ITEM),
        # A digit int() cannot read.
        ({"item_id": "²"}, PICK_AN_ITEM),
        ({"amount": "0"}, ABOVE_ZERO),
        ({"amount": "1.5"}, WHOLE_UNITS),
        ({"direction": "sideways"}, FOUND_OR_OFF),
        ({"reason_id": ""}, PICK_A_REASON),
        ({"key": "not a key"}, OUT_OF_DATE),
    ],
)
def test_an_adjustment_says_what_is_missing(
    admin_client: TestClient, session: Session, change: dict[str, str], problem: str
) -> None:
    place = stocked(session)
    form = adjustment_form(session, place.store, place.item) | change
    refused = admin_client.post(ADJUSTMENTS, data=form)
    assert refused.status_code == 400
    assert str(problem) in text(refused)


def test_the_database_holds_counts_to_their_rules(session: Session) -> None:
    """A count needs its admin; an adjustment, its reason too."""
    place = Place(session)
    with violates(session, "ck_ledger_transaction_admin_counts"):
        session.add(
            LedgerTransaction(
                time_zone=DEFAULT_ZONE,
                idempotency_key=uuid.uuid4(),
                kind="count",
                to_location_id=place.store.id,
            )
        )
    admin = session.scalars(select(Admin)).first() or Admin(username="ada")
    session.add(admin)
    session.flush()
    with violates(session, "ck_ledger_transaction_adjustment_reason"):
        session.add(
            LedgerTransaction(
                time_zone=DEFAULT_ZONE,
                idempotency_key=uuid.uuid4(),
                kind="adjustment",
                to_location_id=place.store.id,
                admin_id=admin.id,
            )
        )


def test_reasons_can_be_added_renamed_retired_and_brought_back(
    admin_client: TestClient, session: Session
) -> None:
    admin_client.post(f"{SETTINGS}/reasons", data={"name": "Given away"})
    given = session.scalars(
        select(AdjustmentReason).where(AdjustmentReason.name == "Given away")
    ).one()
    path = f"{SETTINGS}/reasons/{given.id}"
    form = {"name": "Donated", "version": shown(given)}
    admin_client.post(path, data=form | {"action": "rename"})
    assert given.name == "Donated"
    admin_client.post(path, data=form | {"action": "retire"})
    assert given.retired_at is not None
    why = read(admin_client.get(COUNTS).text).one("select", name="reason_id")
    assert "Donated" not in [words for _, words, _ in options(why)]
    admin_client.post(path, data=form | {"action": "restore"})
    assert given.retired_at is None
    taken = admin_client.post(f"{SETTINGS}/reasons", data={"name": "damaged"})
    assert str(REASON_TAKEN) in text(taken)
    too_long = form | {"name": "x" * 1000, "version": shown(given), "action": "rename"}
    assert admin_client.post(path, data=too_long).status_code == 400
    assert given.name == "Donated"
    unnamed = admin_client.post(f"{SETTINGS}/reasons", data={"name": "  "})
    assert unnamed.status_code == 400
    assert "Give it a name" in text(unnamed)
    assert admin_client.post(f"{SETTINGS}/reasons/999999").status_code == 404


def with_cable(session: Session, place: Place) -> None:
    """Twenty metres of cable received into the store, beside the radios."""
    received(session, place, [{"item_id": place.cable.id, "quantity": "20"}])


def test_a_save_during_a_count_still_applies(
    admin_client: TestClient, session: Session
) -> None:
    """The count is true for when its form was shown: three radios moved to
    the van meanwhile, and only the cable was counted, so it saves in one go
    and the move stands."""
    place = stocked(session)
    with_cable(session, place)
    path = f"{COUNTS}/{place.store.id}"
    shown = count_form(admin_client.get(path))
    write(session, place)  # Three radios to the van.
    done = admin_client.post(
        path,
        data=shown
        | {f"counted_{place.item.id}": "", f"counted_{place.cable.id}": "18"},
    )
    assert done.headers["location"] == f"{COUNTS}?notice=counted&section=count#count"
    assert expected(session, place.store.id) == {
        place.item.id: Decimal(2),
        place.cable.id: Decimal(18),
    }


def test_an_item_that_moved_while_counted_is_counted_again(
    admin_client: TestClient, session: Session
) -> None:
    """Three radios went to the van after the page was shown: the count of
    radios may be from before or after, so only the cable is saved, and the
    page asks for the radios again, as they are now."""
    place = stocked(session)
    with_cable(session, place)
    path = f"{COUNTS}/{place.store.id}"
    shown = count_form(admin_client.get(path))
    write(session, place)  # Three radios to the van.
    asked = admin_client.post(
        path,
        data=shown
        | {f"counted_{place.item.id}": "5", f"counted_{place.cable.id}": "18"},
    )
    assert asked.status_code == 409
    page = text(asked)
    assert "Saved 1 line. These moved while you counted" in page
    assert "0 in, 3 out; you typed 5" in page
    assert place.cable.name not in page.split("Something else here")[0]
    assert expected(session, place.store.id) == {
        place.item.id: Decimal(2),
        place.cable.id: Decimal(18),
    }
    again = count_form(asked)
    assert again["key"] != shown["key"]
    assert again["as_of"] != shown["as_of"]
    done = admin_client.post(path, data=again | {f"counted_{place.item.id}": "1"})
    assert done.headers["location"] == f"{COUNTS}?notice=counted&section=count#count"
    assert expected(session, place.store.id)[place.item.id] == Decimal(1)


def test_a_recount_sent_back_with_a_problem_asks_for_its_items_only(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    with_cable(session, place)
    path = f"{COUNTS}/{place.store.id}"
    shown = count_form(admin_client.get(path))
    write(session, place)  # Three radios to the van.
    asked = admin_client.post(
        path,
        data=shown
        | {f"counted_{place.item.id}": "5", f"counted_{place.cable.id}": "18"},
    )
    again = count_form(asked)
    refused = admin_client.post(
        path, data=again | {f"counted_{place.item.id}": "x", "counted_bad": "1"}
    )
    assert refused.status_code == 400
    page = refused.text
    assert box_values(page, f"counted_{place.item.id}")
    assert not box_values(page, f"counted_{place.cable.id}")
    assert "Count them again now." in text(refused)


def test_a_recount_refused_for_a_bad_number_keeps_its_figures(
    admin_client: TestClient, session: Session
) -> None:
    """Refused for a problem, the recount is shown again with what was
    typed, came in and went out while it was counted, as first shown
    (inventory-tng-up4h)."""
    place = stocked(session)
    path = f"{COUNTS}/{place.store.id}"
    shown = count_form(admin_client.get(path))
    write(session, place)  # Three radios to the van.
    asked = admin_client.post(path, data=shown | {f"counted_{place.item.id}": "5"})
    again = count_form(asked)
    refused = admin_client.post(path, data=again | {f"counted_{place.item.id}": "x"})
    assert refused.status_code == 400
    page = text(refused)
    assert "While you counted" in page
    assert "0 in, 3 out; you typed 5" in page
    assert count_form(refused) == again
    done = admin_client.post(path, data=again | {f"counted_{place.item.id}": "1"})
    assert done.status_code == 303
    assert expected(session, place.store.id)[place.item.id] == Decimal(1)


def test_a_recount_figures_sent_changed_change_only_what_is_shown(
    admin_client: TestClient, session: Session
) -> None:
    """A hand-made form's figures are only shown, and only of the items the
    recount asks for; one that is no number is left blank, and the count
    is written as typed."""
    place = stocked(session)
    with_cable(session, place)
    path = f"{COUNTS}/{place.store.id}"
    shown = count_form(admin_client.get(path))
    write(session, place)  # Three radios to the van.
    asked = admin_client.post(
        path,
        data=shown
        | {f"counted_{place.item.id}": "5", f"counted_{place.cable.id}": "18"},
    )
    again = count_form(asked)
    item = f"counted_{place.item.id}"
    made = again | {
        f"while_{place.item.id}": "7 8 9",
        f"while_{place.cable.id}": "1 2 3",
        "while_x": "4 5 6",
    }
    refused = admin_client.post(path, data=made | {item: "x"})
    assert "8 in, 9 out; you typed 7" in text(refused)
    assert "2 in, 3 out" not in text(refused)
    assert "5 in, 6 out" not in text(refused)
    assert not box_values(refused.text, f"counted_{place.cable.id}")
    for wrong in ("7 8", "x 8 9", "-1 8 9"):
        page = admin_client.post(
            path, data=again | {f"while_{place.item.id}": wrong, item: "x"}
        )
        assert " in, " not in text(page), wrong
    done = admin_client.post(path, data=made | {item: "1"})
    assert done.status_code == 303
    assert expected(session, place.store.id) == {
        place.item.id: Decimal(1),
        place.cable.id: Decimal(18),
    }


def test_a_recount_sent_back_leaves_out_an_item_there_is_not(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    path = f"{COUNTS}/{place.store.id}"
    shown = count_form(admin_client.get(path))
    write(session, place)  # Three radios to the van.
    asked = admin_client.post(path, data=shown | {f"counted_{place.item.id}": "5"})
    again = count_form(asked)
    again["items"] += f" {INTEGER_MAX}"
    refused = admin_client.post(path, data=again | {f"counted_{place.item.id}": "x"})
    assert refused.status_code == 400
    assert box_values(refused.text, f"counted_{place.item.id}")
    assert not box_values(refused.text, f"counted_{INTEGER_MAX}")


def test_something_else_here_held_but_not_recounted_is_refused_with_its_line(
    admin_client: TestClient, session: Session
) -> None:
    """The recount asks for the radios only; something else here names the
    cable the store holds: refused as on a full count, the cable's line
    added under the store to type it on, nothing written, everything typed
    kept (Ali, 2026-11-22, inventory-tng-oazl)."""
    place = stocked(session)
    with_cable(session, place)
    radio, cable = place.item.id, place.cable.id
    path = f"{COUNTS}/{place.store.id}"
    shown = count_form(admin_client.get(path))
    write(session, place)  # Three radios to the van.
    asked = admin_client.post(
        path, data=shown | {f"counted_{radio}": "5", f"counted_{cable}": "20"}
    )
    assert asked.status_code == 409
    again = count_form(asked)
    assert again["items"] == str(radio)
    form = again | {
        f"counted_{radio}": "1",
        "extra_item_id": str(cable),
        "extra_quantity": "2",
    }
    with capture_logs() as logs:
        refused = admin_client.post(path, data=form)
    assert refused.status_code == 400
    assert {
        "event": "count refused: something else here listed there",
        "location_id": place.store.id,
        "found_location_id": place.store.id,
        "item_id": cable,
        "log_level": "info",
    } in logs
    assert (
        f"{place.cable.name_shown} is already listed under {place.store.name}. "
        "Type what you found on its line there. Nothing was saved, and what "
        "you typed is kept."
    ) in text(refused)
    page = read(refused.text)
    assert page.one("span", id=f"counted_{cable}-problem").text == str(TYPE_IT_HERE)
    assert (
        page.one("span", id="extra_item_id-problem").text
        == f"Listed under {place.store.name} already."
    )
    assert f"{place.cable.name_shown} 20 m" in words_on(refused.text)
    assert counted_box(refused.text, radio) == "1"
    assert page.one("input", name="extra_quantity").attrs["value"] == "2"
    picked = options(page.one("select", name="extra_item_id"))
    assert [value for value, _, chosen in picked if chosen] == [str(cable)]
    assert sorted(count_form(refused)["items"].split()) == sorted(
        [str(radio), str(cable)]
    )
    assert expected(session, place.store.id) == {
        radio: Decimal(2),
        cable: Decimal(20),
    }
    # Typed on its line, the recount saves both.
    done = admin_client.post(
        path,
        data=count_form(refused)
        | {
            f"counted_{radio}": "1",
            f"counted_{cable}": "22",
            "extra_item_id": "",
            "extra_quantity": "",
        },
    )
    assert done.headers["location"] == f"{COUNTS}?notice=counted&section=count#count"
    assert expected(session, place.store.id) == {
        radio: Decimal(1),
        cable: Decimal(22),
    }


def test_something_else_here_held_by_form_but_not_recounted_marks_its_loose_line(
    admin_client: TestClient, session: Session
) -> None:
    """The cable, held in sealed spools, is counted by form: the line added
    for it is marked on its loose units (inventory-tng-oazl)."""
    spools = Spools(session)
    radio, cable = spools.place.item.id, spools.cable.id
    received(session, spools.place, [{"item_id": radio, "quantity": "5"}])
    path = f"{COUNTS}/{spools.store.id}"
    form = count_form(admin_client.get(path)) | {
        "items": str(radio),
        f"counted_{radio}": "5",
        "extra_item_id": str(cable),
        "extra_quantity": "3",
    }
    refused = admin_client.post(path, data=form)
    assert refused.status_code == 400
    page = read(refused.text)
    assert page.one("span", id=f"loose_{cable}-problem").text == str(TYPE_IT_HERE)
    assert page.all("input", name=f"sealed_{cable}_{spools.spool.id}")
    assert counts_written(session) == []


def test_something_else_here_with_an_opening_waiting_but_not_recounted_is_refused(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """An opening waits in the store, all its cable moved out since: the
    records hold none there, but a full count asks for it by form, so the
    recount refuses it too, its loose line marked (inventory-tng-oazl)."""
    spools = Spools(session, tracked=False)
    opening_raised(registered_client, spools)
    spools.move(
        Entry(spools.cable.id, Decimal(305), spools.sealed, spools.sealed),
        Entry(spools.cable.id, Decimal(305), LOOSE, LOOSE),
    )
    radio, cable = spools.place.item.id, spools.cable.id
    path = f"{COUNTS}/{spools.store.id}"
    shown = count_form(admin_client.get(path))
    assert cable not in expected(session, spools.store.id)
    form = shown | {
        "items": str(radio),
        f"counted_{radio}": "5",
        "extra_item_id": str(cable),
        "extra_quantity": "3",
    }
    refused = admin_client.post(path, data=form)
    assert refused.status_code == 400
    page = read(refused.text)
    assert page.one("span", id=f"loose_{cable}-problem").text == str(TYPE_IT_HERE)
    assert counts_written(session) == []


def test_a_count_whose_every_line_moved_writes_nothing(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    path = f"{COUNTS}/{place.store.id}"
    shown = count_form(admin_client.get(path))
    write(session, place)  # Three radios to the van.
    asked = admin_client.post(path, data=shown | {f"counted_{place.item.id}": "5"})
    assert asked.status_code == 409
    assert "Saved" not in text(asked)
    assert counts_written(session) == []


@pytest.mark.parametrize("field", ["as_of", "seen"])
def test_a_count_needs_the_moment_it_was_shown(
    admin_client: TestClient, session: Session, field: str
) -> None:
    place = stocked(session)
    path = f"{COUNTS}/{place.store.id}"
    shown = count_form(admin_client.get(path))
    for sent in ("", "²", "-1", str(2**31)):
        refused = admin_client.post(
            path, data=shown | {field: sent, f"counted_{place.item.id}": "5"}
        )
        assert refused.status_code == 404
    assert counts_written(session) == []


def test_a_count_shown_again_for_a_problem_keeps_its_moment(
    admin_client: TestClient, session: Session
) -> None:
    """What was typed is as of when the form was first shown, so the form
    shown again to fix a line keeps that moment, not a later one."""
    place = stocked(session)
    with_cable(session, place)
    path = f"{COUNTS}/{place.store.id}"
    shown = count_form(admin_client.get(path))
    write(session, place, lines=[{"item_id": place.item.id, "quantity": "5"}])
    refused = admin_client.post(
        path,
        data=shown
        | {f"counted_{place.item.id}": "5", f"counted_{place.cable.id}": "x"},
    )
    assert refused.status_code == 400
    assert count_form(refused)["as_of"] == shown["as_of"]
    # No recount: no While you counted column (inventory-tng-up4h).
    assert "While you counted" not in refused.text
    # Every radio has gone since, but the radios' line is still there.
    assert counted_box(refused.text, place.item.id) == "5"


def test_a_count_sent_again_after_a_later_move_is_not_asked_again(
    admin_client: TestClient, session: Session
) -> None:
    """The first send saved every line: a move after it does not make the
    second send ask for the radios again."""
    place = stocked(session)
    path = f"{COUNTS}/{place.store.id}"
    form = count_form(admin_client.get(path)) | {f"counted_{place.item.id}": "4"}
    assert admin_client.post(path, data=form).status_code == 303
    write(session, place)  # Three radios to the van.
    again = admin_client.post(path, data=form)
    assert again.headers["location"] == f"{COUNTS}?notice=counted&section=count#count"
    assert expected(session, place.store.id) == {place.item.id: Decimal(1)}


def test_showing_a_count_form_locks_nothing(
    admin_client: TestClient, session: Session
) -> None:
    """Its moment is read, not held (inventory-tng-0um): volunteers' saves
    at the place go on while it is shown."""
    place = stocked(session)
    with counting(session) as sent:
        assert admin_client.get(f"{COUNTS}/{place.store.id}").status_code == 200
    assert not [each for each in sent if " FOR " in each]


def test_a_count_of_a_place_changed_before_its_moment_is_shown_again(
    admin_client: TestClient, session: Session
) -> None:
    """A save there older than the form's moment that it did not see (one
    under way as it was shown, committed after, as
    test_a_save_under_way_as_the_form_was_shown_refuses_the_count makes):
    nothing is written, and the form is shown again as it is now, keeping
    what was typed, to check and send again."""
    place = stocked(session)
    path = f"{COUNTS}/{place.store.id}"
    shown = count_form(admin_client.get(path))
    unseen = shown | {"seen": str(int(shown["seen"]) - 1)}
    with capture_logs() as logs:
        refused = admin_client.post(
            path, data=unseen | {f"counted_{place.item.id}": "4"}
        )
    assert refused.status_code == 409
    assert str(SAVED_AS_SHOWN) in text(refused)
    assert counts_written(session) == []
    again = count_form(refused)
    assert again["key"] != shown["key"]
    assert (again["as_of"], again["seen"]) == (shown["as_of"], shown["seen"])
    assert counted_box(refused.text, place.item.id) == "4"
    assert {
        "event": "count refused by a rule",
        "location_id": place.store.id,
        "rule": "changed_since_shown",
        "log_level": "info",
    } in logs
    done = admin_client.post(path, data=again | {f"counted_{place.item.id}": "4"})
    assert done.headers["location"] == f"{COUNTS}?notice=counted&section=count#count"


def receive(session: Session, place: Place) -> None:
    """Three radios received into the store. Committed tests may not write
    the ledger (APPEND_ONLY), so the caller rolls it back."""
    received(session, place)


@pytest.mark.concurrency
def test_a_count_form_does_not_wait_for_a_save_under_way_at_the_place(
    committed: Committed,
) -> None:
    """Its moment is read without the place's lock (inventory-tng-0um): a
    save under way there is left out of it, not waited for."""
    place = committed_place(committed)
    saving = committed.session()
    receive(saving, place)
    counting = committed.session()
    counting.execute(sql_text("SET LOCAL lock_timeout = '100ms'"))
    assert moment(counting, place.store.id) == Moment(as_of=0, seen=0)
    counting.rollback()
    saving.rollback()


def _into_store(place: Place) -> dict[str, object]:
    return {
        "kind": "receipt",
        "from_location_id": None,
        "to_location_id": place.store.id,
    }


def _into_van(place: Place) -> dict[str, object]:
    return _into_store(place) | {"to_location_id": place.van.id}


# A save under way as a count form of the store is shown, drawing its id
# before a later save there that commits first: its fields (Place.batch's
# default is a move out of the store), whether it commits, and whether the
# count is refused.
UNDER_WAY: dict[str, tuple[Callable[[Place], dict[str, object]], bool, bool]] = {
    "into the place": (_into_store, True, True),
    "out of the place": (lambda _: {}, True, True),
    "at another place": (_into_van, True, False),
    "rolled back": (_into_store, False, False),
}


@pytest.mark.concurrency
@pytest.mark.parametrize("case", UNDER_WAY)
def test_a_save_under_way_as_the_form_was_shown_refuses_the_count(
    own_committed: Committed, case: str
) -> None:
    """A save at the place that drew its id before a later one there, and
    commits after the form was shown, is older than the form's moment but
    was not in it: the count, holding the place, finds it and is refused,
    writing nothing. One at another place, or one rolled back, leaving a
    gap in the ids, refuses nothing."""
    fields, commits, refuses = UNDER_WAY[case]
    setup = own_committed.session()
    place = Place(setup)
    admin, _ = new_admin(setup)
    setup.commit()
    saving = own_committed.session()
    early = write(saving, place, **fields(place))
    later = own_committed.session()
    late = received(later, place, [{"item_id": place.cable.id, "quantity": "20"}])
    later.commit()
    assert early.id < late.id
    counting = own_committed.session()
    shown = moment(counting, place.store.id)
    assert shown == Moment(as_of=late.id, seen=1)
    counting.rollback()
    if commits:
        saving.commit()
    else:
        saving.rollback()

    def count() -> Counted:
        return record_count(
            counting,
            uuid.uuid4(),
            admin.id,
            place.store.id,
            [(place.cable.id, Decimal(18))],
            shown,
        )

    if refuses:
        with pytest.raises(RuleBroken) as refused:
            count()
        assert refused.value.rule == "changed_since_shown"
        counting.rollback()
        assert counts_written(counting) == []
    else:
        count()
        assert len(counts_written(counting)) == 1
        counting.rollback()


@pytest.mark.concurrency
def test_a_count_form_does_not_hold_up_saves(committed: Committed) -> None:
    """The form keeps its moment, not the lock: a save at the place goes
    ahead while the form is still being made."""
    place = committed_place(committed)
    counting = committed.session()
    moment(counting, place.store.id)
    saving = committed.session()
    receive(saving, place)
    saving.rollback()
    counting.rollback()


@pytest.mark.concurrency
def test_a_save_at_the_place_waits_for_a_count(
    committed: Committed, owner_engine: Engine
) -> None:
    """Before it has an id: one drawn first could be committed after the
    count's moment, though lower (inventory-tng-099)."""
    place = committed_place(committed)
    counting = committed.session()
    lock_place(counting, place.store.id)
    before = ids_drawn(owner_engine)
    saving = committed.session()
    worker = committed.blocked(saving, lambda: receive(saving, place))
    assert ids_drawn(owner_engine) == before
    counting.rollback()
    assert worker.finished().error is None
    saving.rollback()


def ids_drawn(owner_engine: Engine) -> tuple[int, bool]:
    """How far the ledger's ids have been handed out: read as the owner, as
    the app has no privileges on the sequence, nor needs any."""
    with owner_engine.connect() as connection:
        last, called = connection.execute(
            sql_text("SELECT last_value, is_called FROM ledger_transaction_id_seq")
        ).one()
    return last, called


def test_leftovers_of_a_retired_item_can_be_written_off(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    retire(session, place.item)
    page = text(admin_client.get(COUNTS))
    assert place.item.name in page
    form = adjustment_form(
        session, place.store, place.item, reason="Lost or stolen", amount="5"
    )
    assert admin_client.post(ADJUSTMENTS, data=form).status_code == 303
    assert expected(session, place.store.id) == {}


@pytest.mark.concurrency
@pytest.mark.end_to_end
def test_a_count_form_reads_its_moment_and_figures_on_one_snapshot(
    committed: Committed,
    make_client: Callable[..., TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a session of its own, as in production: REPEATABLE READ, so a
    save committing between its moment and its figures is in neither, and
    a count of what it shows is not refused for one it did."""
    place = committed_place(committed)
    setup = committed.session()
    _, totp = new_admin(setup)
    setup.commit()
    seen = isolation_seen(monkeypatch, counts_routes, "moment")
    client = make_client(own_sessions=True)
    assert sign_in_with(client, key=totp).status_code == 303
    assert client.get(f"{COUNTS}/{place.store.id}").status_code == 200
    assert seen == [("repeatable read", "on")]


@pytest.mark.end_to_end
@pytest.mark.parametrize("kind", ["count", "adjustment"])
def test_a_unit_changed_to_each_as_the_page_writes_is_said_and_nothing_is_written(
    own_committed: Committed, make_client: Callable[..., TestClient], kind: str
) -> None:
    """The page finds 1.5 m of cable fine, then waits for the cable, held by
    a change of its unit to each; once that commits, the ledger refuses the
    line, and the page says why, as for any other problem, and writes
    nothing (inventory-tng-8i4.104). On a database of its own, as it would
    write the ledger if it were wrong."""
    place = committed_place(own_committed)
    setup = own_committed.session()
    _, totp = new_admin(setup)
    setup.commit()
    page = own_committed.session()
    client = make_client(on=page)
    assert sign_in_with(client, key=totp).status_code == 303
    if kind == "count":
        path = f"{COUNTS}/{place.store.id}"
        form = count_form(client.get(path)) | {
            "extra_item_id": str(place.cable.id),
            "extra_quantity": "1.5",
        }
    else:
        path = ADJUSTMENTS
        form = adjustment_form(
            setup,
            place.store,
            place.cable,
            key=key_on(client.get(COUNTS)),
            amount="1.5",
            direction="found",
        )
    changing = own_committed.session()
    cable = changing.get_one(Item, place.cable.id)
    items.save(changing, cable, item_fields(cable, unit="each"), seen=None)
    changing.flush()
    worker = own_committed.blocked(page, lambda: client.post(path, data=form))
    changing.commit()
    answer = worker.finished().result
    assert isinstance(answer, Response)
    assert answer.status_code == 400
    assert str(RULES["movement_whole_units"]) in text(answer)
    assert counts_written(own_committed.session()) == []


# Stock at a retired place (docs/decisions/0003)


def signals(client: TestClient, place: Place) -> dict[str, bool]:
    """Where the van shows as retired and holding stock: each is shown by
    its page's own tests, so these only check that each clears."""
    stock = client.get(STOCK).text
    return {
        "home": f"{COUNTS}/{place.van.id}" in addresses(client.get("/admin").text),
        "badge": badge(stock) == badge_of(1),
        "stock": retired_holding(1) in stock,
        "counts": f"{place.van.name} (retired)" in text(client.get(COUNTS)),
        "form": client.get(f"{COUNTS}/{place.van.id}").status_code == 200,
    }


def test_a_retired_place_counted_to_nothing_clears_every_signal(
    admin_client: TestClient, session: Session
) -> None:
    place = at_retired_van(session)
    every = dict.fromkeys(("home", "badge", "stock", "counts", "form"), True)
    assert signals(admin_client, place) == every
    path = f"{COUNTS}/{place.van.id}"
    shown = admin_client.get(path)
    assert "is retired, but the records say something" in text(shown)
    form = count_form(shown) | {f"counted_{place.item.id}": "0"}
    done = admin_client.post(path, data=form)
    assert done.headers["location"] == f"{COUNTS}?notice=counted&section=count#count"
    assert expected(session, place.van.id) == {}
    assert signals(admin_client, place) == dict.fromkeys(every, False)
    # Sent again, it is answered as the first send was, writing nothing.
    again = admin_client.post(path, data=form)
    assert again.headers["location"] == done.headers["location"]
    assert len(counts_written(session)) == 1


def test_stock_at_a_retired_place_can_be_written_off(
    admin_client: TestClient, session: Session
) -> None:
    place = at_retired_van(session)
    form = adjustment_form(
        session, place.van, place.item, reason="Lost or stolen", amount="3"
    )
    assert admin_client.post(ADJUSTMENTS, data=form).status_code == 303
    assert expected(session, place.van.id) == {}
    assert not any(signals(admin_client, place).values())
    # Sent again, it is answered as the first send was, writing nothing.
    assert admin_client.post(ADJUSTMENTS, data=form).status_code == 303
    assert len(counts_written(session)) == 1


def test_a_retired_place_holding_nothing_is_not_offered(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    retire(session, place.van)
    assert place.van.name not in text(admin_client.get(COUNTS))
    assert admin_client.get(f"{COUNTS}/{place.van.id}").status_code == 404


def test_a_form_for_a_retired_place_holding_nothing_writes_nothing(
    admin_client: TestClient, session: Session
) -> None:
    """A stale or hand-made form, with a key not yet written: the ledger
    refuses it (location_countable), a count as for a place there is not."""
    place = stocked(session)
    retire(session, place.van)
    counted = admin_client.post(
        f"{COUNTS}/{place.van.id}",
        data={
            "key": str(uuid.uuid4()),
            "as_of": "0",
            "seen": "0",
            f"counted_{place.item.id}": "2",
        },
    )
    assert counted.status_code == 404
    form = adjustment_form(session, place.van, place.item, direction="found")
    adjusted = admin_client.post(ADJUSTMENTS, data=form)
    assert adjusted.status_code == 400
    assert str(RULES["location_countable"]) in text(adjusted)
    assert counts_written(session) == []


def test_a_key_written_at_another_place_lets_no_other_place_through(
    admin_client: TestClient, session: Session
) -> None:
    """A form sent again gets its first answer, but only at the place it
    was written for: its key opens no other place that cannot be counted."""
    place = stocked(session)
    retire(session, place.van)
    path = f"{COUNTS}/{place.store.id}"
    form = count_form(admin_client.get(path)) | {f"counted_{place.item.id}": "5"}
    assert admin_client.post(path, data=form).status_code == 303
    elsewhere = admin_client.post(f"{COUNTS}/{place.van.id}", data=form)
    assert elsewhere.status_code == 404
    assert len(counts_written(session)) == 1


@pytest.mark.concurrency
@pytest.mark.parametrize("kind", ["count", "adjustment"])
def test_a_place_retired_as_it_is_counted_is_refused(
    committed: Committed, kind: str
) -> None:
    """The page found the store current; a retire of it, holding nothing,
    commits while the count or adjustment waits for the place (lock_place),
    which then finds it may not be counted, and writes nothing."""
    place = committed_place(committed)
    setup = committed.session()
    admin, _ = new_admin(setup)
    reason_id = setup.scalars(select(AdjustmentReason.id)).first()
    setup.commit()
    retiring = committed.session()
    store = retiring.get_one(Location, place.store.id)
    locations.retire(retiring, store, store.created_at)
    retiring.flush()
    counting = committed.session()
    key = uuid.uuid4()
    if kind == "count":
        worker = committed.blocked(
            counting,
            lambda: record_count(
                counting,
                key,
                admin.id,
                place.store.id,
                [(place.item.id, Decimal(1))],
                Moment(0, 0),
            ),
        )
    else:
        assert reason_id is not None
        worker = committed.blocked(
            counting,
            lambda: record_adjustment(
                counting,
                key,
                admin.id,
                place.store.id,
                place.item.id,
                Decimal(1),
                found=True,
                reason_id=reason_id,
            ),
        )
    retiring.commit()
    error = worker.finished().error
    assert isinstance(error, RuleBroken)
    assert error.rule == "location_countable"
    counting.rollback()


def test_a_count_shows_in_the_zone_of_its_place(
    admin_client: TestClient, session: Session
) -> None:
    """Recorded in its place's zone, shown in it: CST beside the site's EST."""
    place = stocked(session)
    place.store.time_zone = "America/Chicago"
    session.flush()
    count_at(admin_client, place.store.id, {f"counted_{place.item.id}": "5"})
    (counted,) = counts_written(session)
    assert counted.time_zone == "America/Chicago"
    assert show(counted.created_at, "America/Chicago") in text(admin_client.get(COUNTS))


def test_the_counts_page_says_its_links_start_a_task(admin_client: TestClient) -> None:
    """Only the button that changes the records promises a change
    (docs/decisions/0019)."""
    page = admin_client.get(COUNTS).text
    assert button(page, "Start a count").classes == []
    assert button(page, "Record it").classes == ["primary"]
    assert "Count it" not in page


# A count of some items only (Ali, 2026-11-15, inventory-tng-hnv).


def test_a_count_can_show_one_item(admin_client: TestClient, session: Session) -> None:
    place = stocked(session)
    with_cable(session, place)
    path = f"{COUNTS}/{place.store.id}"
    page = admin_client.get(f"{path}?item={place.cable.id}")
    assert box_values(page.text, f"counted_{place.cable.id}")
    assert not box_values(page.text, f"counted_{place.item.id}")
    assert ("post", f"{path}?item={place.cable.id}") in forms(page.text)
    words = text(page)
    assert f"Only {place.cable.name}. Only these are counted" in words
    assert ("Show all", path) in links(page.text)
    # Nothing else of it to add at the foot.
    assert "Something else here" not in words


def test_an_item_asked_for_is_shown_though_none_is_there(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    page = admin_client.get(f"{COUNTS}/{place.store.id}?item={place.cable.id}").text
    assert counted_box(page, place.cable.id) == "0"
    assert not box_values(page, f"counted_{place.item.id}")


def test_a_count_can_show_the_items_a_search_finds(
    admin_client: TestClient, session: Session
) -> None:
    """As the items list searches: typos forgiven."""
    place = stocked(session)
    with_cable(session, place)
    path = f"{COUNTS}/{place.store.id}"
    page = admin_client.get(f"{path}?q=litebeem")
    assert box_values(page.text, f"counted_{place.item.id}")
    assert not box_values(page.text, f"counted_{place.cable.id}")
    assert ("post", f"{path}?q=litebeem") in forms(page.text)
    assert "Only items matching “litebeem”." in text(page)
    nothing = admin_client.get(f"{path}?q=zzzzzzzz")
    assert "Nothing here matches." in text(nothing)
    # Saved, it would record the place counted, though nothing was.
    assert "Save the count" not in text(nothing)
    # A search of nothing once folded, or an item there is not: all shown.
    for odd in ("?q=%CC%81", "?item=x", f"?item={INTEGER_MAX}"):
        every = admin_client.get(f"{path}{odd}").text
        assert box_values(every, f"counted_{place.cable.id}"), odd
        assert box_values(every, f"counted_{place.item.id}"), odd
        assert "Show all" not in every, odd


def test_a_count_of_one_item_leaves_the_others_as_they_are(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    with_cable(session, place)
    path = f"{COUNTS}/{place.store.id}?item={place.cable.id}"
    done = admin_client.post(
        path,
        data=count_form(admin_client.get(path)) | {f"counted_{place.cable.id}": "18"},
    )
    assert done.headers["location"] == f"{COUNTS}?notice=counted&section=count#count"
    assert expected(session, place.store.id) == {
        place.item.id: Decimal(5),
        place.cable.id: Decimal(18),
    }


def test_a_count_sent_back_keeps_its_filter(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    with_cable(session, place)
    path = f"{COUNTS}/{place.store.id}?item={place.cable.id}&q=cable"
    refused = admin_client.post(
        path,
        data=count_form(admin_client.get(path)) | {f"counted_{place.cable.id}": "x"},
    )
    assert refused.status_code == 400
    assert counted_box(refused.text, place.cable.id) == "x"
    assert not box_values(refused.text, f"counted_{place.item.id}")
    assert ("post", path) in forms(refused.text)
    assert f"Only {place.cable.name} matching “cable”." in text(refused)


def test_a_recount_keeps_its_filter(admin_client: TestClient, session: Session) -> None:
    place = stocked(session)
    with_cable(session, place)
    path = f"{COUNTS}/{place.store.id}?q=litebeam"
    shown = count_form(admin_client.get(path))
    write(session, place)  # Three radios to the van.
    asked = admin_client.post(path, data=shown | {f"counted_{place.item.id}": "5"})
    assert asked.status_code == 409
    assert ("post", path) in forms(asked.text)
    assert "Only items matching “litebeam”." in text(asked)


def test_a_recount_shows_each_item_it_asks_for_though_the_search_no_longer_finds_it(
    admin_client: TestClient, session: Session
) -> None:
    """Renamed meanwhile, a moved item is still asked for again, not left
    out of sight with its line unwritten."""
    place = stocked(session)
    path = f"{COUNTS}/{place.store.id}?q=litebeam"
    shown = count_form(admin_client.get(path))
    write(session, place)  # Three radios to the van.
    place.item.search_text = "renamed"
    session.commit()
    asked = admin_client.post(path, data=shown | {f"counted_{place.item.id}": "5"})
    assert asked.status_code == 409
    assert box_values(asked.text, f"counted_{place.item.id}")
    assert "Nothing here matches." not in text(asked)


# The counts list (Ali, 2026-11-15, inventory-tng-qwb): newest first unless
# a heading sorts them; filtered by place, kind and administrator; in pages
# of 25, 50, 100 or all, not only the newest 30.


@dataclass
class Listed:
    """A store's Damaged by Ada, a shelf's Found by Bob, then the store's
    Count by Bob: three rows the list sorts, filters and pages."""

    session: Session
    client: TestClient
    place: Place
    shelf: Location
    ada: Admin
    bob: Admin
    found_reason_id: int

    def __call__(self, **query: str) -> list[str]:
        """Each row's kind, as the list asked for by `query` shows it."""
        return kinds(self.client.get(COUNTS, params=query).text)


def kinds(page: str, words: str = "Count|Damaged|Found") -> list[str]:
    """Each row's kind, from its first words, in the page's order."""
    return [
        found[1]
        for cell in read(page).all("td")
        if (found := re.match(rf"({words}):", cell.text))
    ]


@pytest.fixture
def listed(admin_client: TestClient, session: Session) -> Listed:
    place = stocked(session)
    shelf = Location(name="Aa shelf", kind="store")
    session.add(shelf)
    ada = session.scalars(select(Admin)).one()
    bob = Admin(username="bob")
    session.add(bob)
    session.flush()
    reasons = {
        name: id_
        for name, id_ in session.execute(
            select(AdjustmentReason.name, AdjustmentReason.id)
        )
    }
    for who, where, found, reason in (
        (ada, place.store, False, "Damaged"),
        (bob, shelf, True, "Found"),
    ):
        record_adjustment(
            session,
            uuid.uuid4(),
            who.id,
            where.id,
            place.item.id,
            Decimal(1),
            found=found,
            reason_id=reasons[reason],
        )
    session.commit()
    as_of = moment(session, place.store.id)
    record_count(
        session,
        uuid.uuid4(),
        bob.id,
        place.store.id,
        [(place.item.id, Decimal(4))],
        as_of,
    )
    session.commit()
    return Listed(session, admin_client, place, shelf, ada, bob, reasons["Found"])


def test_counts_are_newest_first_and_sort_by_each_heading(listed: Listed) -> None:
    assert listed() == ["Count", "Found", "Damaged"]
    assert listed(sort="when", order="asc") == ["Damaged", "Found", "Count"]
    assert listed(sort="what") == ["Count", "Damaged", "Found"]
    assert listed(sort="what", order="desc") == ["Found", "Damaged", "Count"]
    assert listed(sort="where") == ["Found", "Count", "Damaged"]
    assert listed(sort="by") == ["Damaged", "Count", "Found"]


def test_counts_sort_by_what_in_the_language_spoken(listed: Listed) -> None:
    """By its words in the language spoken, not English."""
    catalogue = Catalog()
    catalogue.add(str(COUNT_KIND), "Inventur")
    with speaking(compiled(catalogue)):
        page = listed.client.get(COUNTS, params={"sort": "what"}).text
    assert kinds(page, "Inventur|Damaged|Found") == ["Damaged", "Found", "Inventur"]


def test_counts_filter_by_place_kind_and_administrator(listed: Listed) -> None:
    assert listed(place=str(listed.shelf.id)) == ["Found"]
    assert listed(kind="count") == ["Count"]
    assert listed(kind="adjustment") == ["Found", "Damaged"]
    assert listed(admin=str(listed.bob.id)) == ["Count", "Found"]
    assert listed(admin=str(listed.bob.id), kind="adjustment") == ["Found"]


def test_a_counts_filter_that_names_nothing_is_left_out(listed: Listed) -> None:
    assert listed(place="x", kind="audit", admin="") == listed()


def test_the_counts_filter_shows_its_choices_and_a_way_to_clear_it(
    listed: Listed,
) -> None:
    response = listed.client.get(COUNTS, params={"admin": str(listed.bob.id)})
    page = read(response.text)
    assert (str(listed.bob.id), "bob", True) in options(page)
    assert (str(listed.shelf.id), "Aa shelf", False) in options(page)
    assert ("Clear", COUNTS) in links(page)


def test_a_counts_filter_matching_nothing_says_so(listed: Listed) -> None:
    assert "None is in the filter." in text(
        listed.client.get(COUNTS, params={"place": str(INTEGER_MAX)})
    )


def test_counts_come_in_pages(listed: Listed) -> None:
    for _ in range(28):
        record_adjustment(
            listed.session,
            uuid.uuid4(),
            listed.ada.id,
            listed.shelf.id,
            listed.place.item.id,
            Decimal(1),
            found=True,
            reason_id=listed.found_reason_id,
        )
    listed.session.commit()
    assert len(listed()) == 31
    assert len(listed(per="25")) == 25
    assert len(listed(per="25", page="2")) == 6
    assert "Page 2 of 2" in text(
        listed.client.get(COUNTS, params={"per": "25", "page": "2"})
    )
