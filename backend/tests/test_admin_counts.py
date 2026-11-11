"""Counts and adjustments (admin/counts.py), and their reasons
(admin/settings.py)."""

import re
import uuid
from collections.abc import Callable
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng import items, locations
from inventory_tng.admin.common import RULES
from inventory_tng.admin.counts import ADJUSTMENTS, COUNTS, lines, named_twice
from inventory_tng.admin.settings import SETTINGS
from inventory_tng.admin.stock import STOCK
from inventory_tng.db import RuleBroken
from inventory_tng.ledger import (
    expected,
    lock_place,
    moment,
    record_adjustment,
    record_count,
)
from inventory_tng.local_time import DEFAULT_ZONE, show
from inventory_tng.models import (
    INTEGER_MAX,
    AdjustmentReason,
    Admin,
    Item,
    LedgerTransaction,
    Location,
    Movement,
)

from helpers.concurrency import Committed
from helpers.rows import item_fields, shown, violates
from helpers.sign_in import code, new_admin, sign_in_with, text
from helpers.stock import (
    Place,
    adjustment_form,
    at_retired_van,
    badge_html,
    committed_place,
    received,
    retire,
    stocked,
    write,
)


def key_on(response: Response) -> str:
    match = re.search(r'name="key" value="([0-9a-f-]{36})"', response.text)
    assert match is not None
    return match.group(1)


def count_form(client: TestClient, path: str) -> dict[str, str]:
    """The count page's hidden fields, as it was shown: its key, and the
    place's latest ledger entry then."""
    return count_form_of(client.get(path))


def count_form_of(page: Response) -> dict[str, str]:
    found = re.findall(
        r'type="hidden" name="(key|as_of|items)" value="([^"]*)"', page.text
    )
    return dict(found)


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
        # Closing an open package's (migration 0024).
        "Used up",
    ]


def test_a_count_puts_the_records_right(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    path = f"{COUNTS}/{place.store.id}"
    assert 'value="5"' in admin_client.get(path).text
    done = admin_client.post(
        path,
        data=count_form(admin_client, path)
        | {
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


def test_a_count_that_matches_is_still_recorded(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    path = f"{COUNTS}/{place.store.id}"
    done = admin_client.post(
        path, data=count_form(admin_client, path) | {f"counted_{place.item.id}": "5"}
    )
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
    form = count_form(admin_client, path) | {f"counted_{place.item.id}": "2"}
    admin_client.post(path, data=form)
    admin_client.post(path, data=form)
    assert len(counts_written(session)) == 1
    assert expected(session, place.store.id) == {place.item.id: Decimal(2)}


def test_a_count_refuses_what_is_not_a_quantity(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    path = f"{COUNTS}/{place.store.id}"
    shown = count_form(admin_client, path)
    for typed, problem in (("lots", "Type a number"), ("1.5", "whole units")):
        refused = admin_client.post(
            path, data=shown | {f"counted_{place.item.id}": typed}
        )
        assert refused.status_code == 400
        assert problem in text(refused)
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


@pytest.mark.parametrize(
    "fields",
    [
        lambda place: {f"counted_0{place.item.id}": "2"},
        lambda place: {"extra_item_id": str(place.item.id), "extra_quantity": "2"},
    ],
    ids=["two spellings", "chosen at the foot too"],
)
def test_a_count_naming_an_item_twice_is_refused(
    admin_client: TestClient,
    session: Session,
    fields: Callable[[Place], dict[str, str]],
) -> None:
    """The page cannot send this; a hand-made form can. Nothing is written,
    and the page says why, and the same key saves the count put right."""
    place = stocked(session)
    path = f"{COUNTS}/{place.store.id}"
    shown = count_form(admin_client, path) | {f"counted_{place.item.id}": "3"}
    with capture_logs() as logs:
        refused = admin_client.post(path, data=shown | fields(place))
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
    assert "This item is on the form more than once." in page
    assert counts_written(session) == []
    assert expected(session, place.store.id) == {place.item.id: Decimal(5)}
    done = admin_client.post(path, data=shown)
    assert done.headers["location"] == f"{COUNTS}?notice=counted&section=count#count"
    assert expected(session, place.store.id) == {place.item.id: Decimal(3)}


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
    assert "Damaged: -2 each" in " ".join(text(admin_client.get(COUNTS)).split())


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"location_id": ""}, "Pick a place."),
        ({"item_id": "999999"}, "Pick an item."),
        # A digit int() cannot read.
        ({"item_id": "²"}, "Pick an item."),
        ({"amount": "0"}, "Use a number above 0."),
        ({"amount": "1.5"}, "whole units"),
        ({"direction": "sideways"}, "Found or written off?"),
        ({"reason_id": ""}, "Pick a reason."),
    ],
)
def test_an_adjustment_says_what_is_missing(
    admin_client: TestClient, session: Session, change: dict[str, str], problem: str
) -> None:
    place = stocked(session)
    form = adjustment_form(session, place.store, place.item) | change
    refused = admin_client.post(ADJUSTMENTS, data=form)
    assert refused.status_code == 400
    assert problem in text(refused)


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
    assert (
        "Donated"
        not in text(admin_client.get(COUNTS)).split("Why")[1].split("</select>")[0]
    )
    admin_client.post(path, data=form | {"action": "restore"})
    assert given.retired_at is None
    taken = admin_client.post(f"{SETTINGS}/reasons", data={"name": "damaged"})
    assert "already a reason by that name" in text(taken)
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
    shown = count_form(admin_client, path)
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
    shown = count_form(admin_client, path)
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
    again = count_form_of(asked)
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
    shown = count_form(admin_client, path)
    write(session, place)  # Three radios to the van.
    asked = admin_client.post(
        path,
        data=shown
        | {f"counted_{place.item.id}": "5", f"counted_{place.cable.id}": "18"},
    )
    again = count_form_of(asked)
    refused = admin_client.post(
        path, data=again | {f"counted_{place.item.id}": "x", "counted_bad": "1"}
    )
    assert refused.status_code == 400
    page = refused.text
    assert f'name="counted_{place.item.id}"' in page
    assert f'name="counted_{place.cable.id}"' not in page
    assert "Count them again now." in text(refused)


def test_a_recount_sent_back_leaves_out_an_item_there_is_not(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    path = f"{COUNTS}/{place.store.id}"
    shown = count_form(admin_client, path)
    write(session, place)  # Three radios to the van.
    asked = admin_client.post(path, data=shown | {f"counted_{place.item.id}": "5"})
    again = count_form_of(asked)
    again["items"] += f" {INTEGER_MAX}"
    refused = admin_client.post(path, data=again | {f"counted_{place.item.id}": "x"})
    assert refused.status_code == 400
    assert f'name="counted_{place.item.id}"' in refused.text
    assert f'name="counted_{INTEGER_MAX}"' not in refused.text


def test_a_count_whose_every_line_moved_writes_nothing(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    path = f"{COUNTS}/{place.store.id}"
    shown = count_form(admin_client, path)
    write(session, place)  # Three radios to the van.
    asked = admin_client.post(path, data=shown | {f"counted_{place.item.id}": "5"})
    assert asked.status_code == 409
    assert "Saved" not in text(asked)
    assert counts_written(session) == []


def test_a_count_needs_the_moment_it_was_shown(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    path = f"{COUNTS}/{place.store.id}"
    shown = count_form(admin_client, path)
    for as_of in ("", "²", "-1", str(2**31)):
        refused = admin_client.post(
            path, data=shown | {"as_of": as_of, f"counted_{place.item.id}": "5"}
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
    shown = count_form(admin_client, path)
    write(session, place, lines=[{"item_id": place.item.id, "quantity": "5"}])
    refused = admin_client.post(
        path,
        data=shown
        | {f"counted_{place.item.id}": "5", f"counted_{place.cable.id}": "x"},
    )
    assert refused.status_code == 400
    assert count_form_of(refused)["as_of"] == shown["as_of"]
    # Every radio has gone since, but the radios' line is still there.
    assert f'name="counted_{place.item.id}" inputmode="decimal" value="5"' in (
        refused.text
    )


def test_a_count_sent_again_after_a_later_move_is_not_asked_again(
    admin_client: TestClient, session: Session
) -> None:
    """The first send saved every line: a move after it does not make the
    second send ask for the radios again."""
    place = stocked(session)
    path = f"{COUNTS}/{place.store.id}"
    form = count_form(admin_client, path) | {f"counted_{place.item.id}": "4"}
    assert admin_client.post(path, data=form).status_code == 303
    write(session, place)  # Three radios to the van.
    again = admin_client.post(path, data=form)
    assert again.headers["location"] == f"{COUNTS}?notice=counted&section=count#count"
    assert expected(session, place.store.id) == {place.item.id: Decimal(1)}


def receive(session: Session, place: Place) -> None:
    """Three radios received into the store. Committed tests may not write
    the ledger (APPEND_ONLY), so the caller rolls it back."""
    received(session, place)


@pytest.mark.concurrency
def test_a_count_form_waits_for_a_save_under_way_at_the_place(
    committed: Committed,
) -> None:
    """Its moment must not leave out a save that commits later with a lower
    ledger id."""
    place = committed_place(committed)
    saving = committed.session()
    receive(saving, place)
    counting = committed.session()
    worker = committed.blocked(counting, lambda: moment(counting, place.store.id))
    saving.rollback()
    assert worker.finished().error is None


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
    assert sign_in_with(client, code=code(totp)).status_code == 303
    if kind == "count":
        path = f"{COUNTS}/{place.store.id}"
        form = count_form(client, path) | {
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
    assert RULES["movement_whole_units"] in text(answer)
    assert counts_written(own_committed.session()) == []


# Stock at a retired place (docs/decisions/0003)


def signals(client: TestClient, place: Place) -> dict[str, bool]:
    """Where the van shows as retired and holding stock: each is shown by
    its page's own tests, so these only check that each clears."""
    stock = text(client.get(STOCK))
    return {
        "home": f'href="{COUNTS}/{place.van.id}"' in text(client.get("/admin")),
        "badge": badge_html(1) in stock,
        "stock": "1 retired place holds stock" in stock,
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
    form = count_form_of(shown) | {f"counted_{place.item.id}": "0"}
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
            f"counted_{place.item.id}": "2",
        },
    )
    assert counted.status_code == 404
    form = adjustment_form(session, place.van, place.item, direction="found")
    adjusted = admin_client.post(ADJUSTMENTS, data=form)
    assert adjusted.status_code == 400
    assert RULES["location_countable"] in text(adjusted)
    assert counts_written(session) == []


def test_a_key_written_at_another_place_lets_no_other_place_through(
    admin_client: TestClient, session: Session
) -> None:
    """A form sent again gets its first answer, but only at the place it
    was written for: its key opens no other place that cannot be counted."""
    place = stocked(session)
    retire(session, place.van)
    path = f"{COUNTS}/{place.store.id}"
    form = count_form(admin_client, path) | {f"counted_{place.item.id}": "5"}
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
                0,
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
    path = f"{COUNTS}/{place.store.id}"
    admin_client.post(
        path, data=count_form(admin_client, path) | {f"counted_{place.item.id}": "5"}
    )
    (counted,) = counts_written(session)
    assert counted.time_zone == "America/Chicago"
    assert show(counted.created_at, "America/Chicago") in text(admin_client.get(COUNTS))


def test_the_counts_page_says_its_links_start_a_task(admin_client: TestClient) -> None:
    """Only the button that changes the records promises a change
    (docs/decisions/0019)."""
    page = text(admin_client.get(COUNTS))
    assert "<button>Start a count</button>" in page
    assert '<button class="primary">Record it</button>' in page
    assert "Count it" not in page
