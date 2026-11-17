"""A batch changed after another tab recorded it under the same key
(inventory-tng-0gg): the 409 says what was recorded, and what it took off
a line already recorded waits for an administrator to correct it, or mark
it checked, under "Needs your action"."""

import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng import (
    adjustment_reasons,
    ledger,
    locations,
    open_packages,
    over_recorded,
)
from inventory_tng.admin import length_checks
from inventory_tng.admin import over_recorded as over_recorded_page
from inventory_tng.admin.common import RULES, up_to
from inventory_tng.admin.counts import COUNTS
from inventory_tng.db import RuleBroken, attempt
from inventory_tng.ledger import Entry, Form, _more_written, expected
from inventory_tng.local_time import show_day, site_zone
from inventory_tng.models import (
    OVER_RECORDED_REASONS,
    AdjustmentReason,
    Admin,
    CatalogueHistory,
    ItemPackage,
    LedgerTransaction,
    Location,
    Movement,
    OpenPackage,
    OverRecorded,
    Volunteer,
)

from helpers.concurrency import Committed
from helpers.rows import add, tag
from helpers.sign_in import new_admin, text
from helpers.stock import (
    Place,
    Spools,
    ada_id,
    count_at,
    count_form_at,
    cut,
    length,
    received,
    report,
    retire,
    write,
)

URL = "/api/transactions"
PAGE = "/admin/stock/recorded-more"


@pytest.fixture
def place(session: Session) -> Place:
    return Place(session)


@pytest.fixture
def bags(session: Session, place: Place) -> ItemPackage:
    """Bags of 50 of the place's cable."""
    return add(session, ItemPackage(item_id=place.cable.id, name="Bag of 50", size=50))


def bagged(bags: ItemPackage, n: int) -> dict[str, object]:
    return {
        "item_id": bags.item_id,
        "quantity": str(50 * n),
        "package_id": bags.id,
        "package_size": "50",
    }


def checks(session: Session) -> list[OverRecorded]:
    session.expire_all()
    return list(session.scalars(select(OverRecorded).order_by(OverRecorded.id)))


def recorded_then_changed(
    client: TestClient, first: dict[str, Any], **changed: object
) -> dict[str, Any]:
    """`first` recorded, then sent again changed under its key, as a page
    sends a cart put back with `first`'s lines (changed_from, unless
    `changed` says otherwise): the 409."""
    assert client.post(URL, json=first).status_code == 201
    response = client.post(URL, json=first | {"changed_from": first["lines"]} | changed)
    assert response.status_code == 409
    return response.json()


def test_a_reused_key_is_told_what_was_recorded(
    registered_client: TestClient, place: Place, bags: ItemPackage
) -> None:
    first = place.batch(
        lines=[{"item_id": place.item.id, "quantity": "3"}, bagged(bags, 2)]
    )
    reply = recorded_then_changed(
        registered_client, first, lines=[{"item_id": place.item.id, "quantity": "4"}]
    )
    recorded = reply["recorded"]
    assert reply["reason"] == "key_reused"
    assert {
        key: recorded[key] for key in ("kind", "from_location_id", "to_location_id")
    } == {
        "kind": "move",
        "from_location_id": place.store.id,
        "to_location_id": place.van.id,
    }
    assert recorded["volunteer_id"] == place.volunteer.id
    lines = sorted(recorded["lines"], key=lambda line: line["item_id"])
    assert [
        (
            line["item_id"],
            Decimal(line["quantity"]),
            line["package_id"],
            line["package_size"],
        )
        for line in lines
    ] == sorted(
        [
            (place.item.id, Decimal(3), None, None),
            (place.cable.id, Decimal(100), bags.id, "50.000"),
        ]
    )
    assert (recorded["used_up"], recorded["lengths"]) == ([], [])


def test_what_a_batch_took_off_waits_for_an_administrator(
    registered_client: TestClient, session: Session, place: Place, bags: ItemPackage
) -> None:
    first = place.batch(
        lines=[{"item_id": place.item.id, "quantity": "3"}, bagged(bags, 2)]
    )
    with capture_logs() as logs:
        recorded_then_changed(
            registered_client,
            first,
            # One more radio, which is no check, and one bag fewer.
            lines=[{"item_id": place.item.id, "quantity": "4"}, bagged(bags, 1)],
        )
    (check,) = checks(session)
    movement = session.get_one(Movement, check.movement_id)
    assert (movement.item_id, check.quantity, check.settled_at) == (
        place.cable.id,
        Decimal(50),
        None,
    )
    assert any(
        line["event"] == "over recorded raised" and line["over_recorded_id"] == check.id
        for line in logs
    )
    # Nothing of the changed batch was written.
    assert (
        session.scalar(
            select(LedgerTransaction.id).where(
                LedgerTransaction.idempotency_key == uuid.UUID(first["key"])
            )
        )
        == movement.transaction_id
    )


def test_a_line_taken_off_whole_waits_whole(
    registered_client: TestClient, session: Session, place: Place, bags: ItemPackage
) -> None:
    first = place.batch(
        lines=[{"item_id": place.item.id, "quantity": "3"}, bagged(bags, 2)]
    )
    recorded_then_changed(
        registered_client, first, lines=[{"item_id": place.item.id, "quantity": "3"}]
    )
    assert [check.quantity for check in checks(session)] == [100]


def test_sent_to_another_place_every_line_recorded_waits(
    registered_client: TestClient, session: Session, place: Place
) -> None:
    other = add(session, Location(name=f"Van {tag()}", kind="vehicle"))
    recorded_then_changed(registered_client, place.batch(), to_location_id=other.id)
    assert [check.quantity for check in checks(session)] == [3]


def test_another_volunteer_alone_takes_nothing_off(
    registered_client: TestClient, session: Session, place: Place
) -> None:
    other = add(session, Volunteer(name=f"Volunteer {tag()}"))
    reply = recorded_then_changed(
        registered_client, place.batch(), volunteer_id=other.id
    )
    assert reply["recorded"]["volunteer_id"] == place.volunteer.id
    assert checks(session) == []


def test_said_again_while_waiting_it_says_the_newest_and_settled_stays(
    registered_client: TestClient, session: Session, place: Place
) -> None:
    first = place.batch(lines=[{"item_id": place.item.id, "quantity": "5"}])

    def less(n: str) -> None:
        lines = [{"item_id": place.item.id, "quantity": n}]
        response = registered_client.post(
            URL, json=first | {"lines": lines, "changed_from": first["lines"]}
        )
        assert response.status_code == 409

    assert registered_client.post(URL, json=first).status_code == 201
    less("4")
    less("2")
    (check,) = checks(session)
    assert check.quantity == 3
    over_recorded.mark_checked(
        session, check.id, over_recorded.seen(check), admin_id(session), "both"
    )
    less("1")
    (check,) = checks(session)
    assert (check.quantity, check.settled_how) == (3, "checked")


def admin_id(session: Session) -> int:
    admin, _ = new_admin(session, f"ada-{tag()}", totp=False)
    return admin.id


def test_an_opening_and_a_cut_are_said_by_their_open_packages(
    registered_client: TestClient, session: Session
) -> None:
    spools = Spools(session)
    made = spools.opened()
    first = spools.place.batch(lines=[cut(made, "20")])
    reply = recorded_then_changed(registered_client, first, lines=[cut(made, "15")])
    (line,) = reply["recorded"]["lines"]
    assert (line["open_package_key"], line["package_id"], line["whole"]) == (
        str(made.key),
        None,
        None,
    )
    assert [check.quantity for check in checks(session)] == [5]
    opening = {
        "kind": "open",
        "from_location_id": spools.store.id,
        "to_location_id": spools.store.id,
    }
    key = str(uuid.uuid4())
    opened = spools.place.batch(
        key=key,
        lines=[
            {
                "item_id": spools.cable.id,
                "quantity": "305",
                "package_id": spools.spool.id,
                "package_size": "305",
                "open_package_key": key,
            }
        ],
        **opening,
    )
    refused_key = uuid.uuid4()
    reply = recorded_then_changed(
        registered_client,
        opened,
        install_number=None,
        lines=[opened["lines"][0] | {"open_package_key": str(refused_key)}],
    )
    (line,) = reply["recorded"]["lines"]
    assert (line["package_id"], line["open_package_key"]) == (spools.spool.id, key)
    # Nothing of the refused opening is kept: no open package under its key.
    session.expire_all()
    assert (
        session.scalar(select(OpenPackage.id).where(OpenPackage.key == refused_key))
        is None
    )


def test_a_line_moved_whole_is_said_whole(
    registered_client: TestClient, session: Session
) -> None:
    spools = Spools(session)
    made = spools.opened()
    first = spools.place.batch(lines=[cut(made, "305") | {"whole": True}])
    reply = recorded_then_changed(registered_client, first, lines=[cut(made, "5")])
    (line,) = reply["recorded"]["lines"]
    assert line["whole"] is True


def test_only_the_lines_less_was_sent_of_are_over() -> None:
    sealed, loose = Form(package_id=7), Form()
    written = {
        1: Entry(1, Decimal(100), sealed, sealed),
        2: Entry(2, Decimal(3)),
        3: Entry(3, Decimal(2)),
    }
    sent = [
        Entry(1, Decimal(50), sealed, sealed),
        # The same item loose is another line.
        Entry(1, Decimal(50), loose, loose),
        Entry(2, Decimal(4)),
    ]
    assert _more_written(written, sent, list(written.values())) == {
        1: Decimal(50),
        3: Decimal(2),
    }
    # Only what it took off what it started from: another tab's addition,
    # or the line another tab added, is no check.
    started = [Entry(1, Decimal(100), sealed, sealed), Entry(2, Decimal(1))]
    assert _more_written(written, sent, started) == {1: Decimal(50)}
    assert _more_written(written, sent, None) == {}


def test_lines_of_one_item_in_the_same_forms_are_over_once_together() -> None:
    """Two lines of the same item and forms (a batch may send them) are
    taken off together: 5 and 5 recorded, started from 5, sent none, is 5
    over, not 10."""
    written = {1: Entry(1, Decimal(5)), 2: Entry(1, Decimal(5))}
    assert _more_written(written, [], [Entry(1, Decimal(5))]) == {1: Decimal(5)}
    # Started from both, sent 6: 4 over, from the first then the second.
    started = list(written.values())
    assert _more_written(written, [Entry(1, Decimal(6))], started) == {1: Decimal(4)}
    assert _more_written(written, [Entry(1, Decimal(2))], started) == {
        1: Decimal(5),
        2: Decimal(3),
    }


def test_a_batch_older_than_the_one_recorded_takes_nothing_off(
    registered_client: TestClient, session: Session, place: Place, bags: ItemPackage
) -> None:
    """Another tab put the cart back, changed it and recorded it first: this
    batch, from before that change, sent after it (a tab of a build before,
    which sends no changed_from), took nothing off. Its difference is that
    tab's change, which moved (inventory-tng-0gg review)."""
    original = [{"item_id": place.item.id, "quantity": "3"}, bagged(bags, 1)]
    newer = place.batch(
        lines=[{"item_id": place.item.id, "quantity": "5"}, bagged(bags, 2)]
    )
    reply = recorded_then_changed(
        registered_client, newer, lines=original, changed_from=None
    )
    assert reply["recorded"] is not None
    assert checks(session) == []


def test_only_what_this_batch_took_off_since_it_was_put_back_waits(
    registered_client: TestClient, session: Session, place: Place, bags: ItemPackage
) -> None:
    """Put back with 3 radios and 1 bag; another tab raised the bags to 2
    and recorded that; this one took the radios down to 1: only the 2 radios
    wait, not the bag the other tab added."""
    put_back = [{"item_id": place.item.id, "quantity": "3"}, bagged(bags, 1)]
    other = place.batch(lines=[put_back[0], bagged(bags, 2)])
    recorded_then_changed(
        registered_client,
        other,
        lines=[{"item_id": place.item.id, "quantity": "1"}, bagged(bags, 1)],
        changed_from=put_back,
    )
    ((check, item_id),) = [
        (each, session.get_one(Movement, each.movement_id).item_id)
        for each in checks(session)
    ]
    assert (item_id, check.quantity) == (place.item.id, 2)


def test_the_reply_to_another_client_leaves_out_nothing_it_holds(
    registered_client: TestClient, session: Session, place: Place
) -> None:
    """A count's key reused by a batch: nothing a volunteer recorded."""
    key = uuid.uuid4()
    reason = add(session, AdjustmentReason(name=f"Reason {tag()}"))
    ledger.record_adjustment(
        session,
        key,
        admin_id(session),
        place.store.id,
        place.item.id,
        Decimal(1),
        found=True,
        reason_id=reason.id,
    )
    response = registered_client.post(URL, json=place.batch(key=str(key)))
    assert (response.status_code, response.json()) == (
        409,
        {"reason": "key_reused", "recorded": None},
    )


# The administrators' page.


def raised(client: TestClient, place: Place, bags: ItemPackage) -> None:
    """Two bags recorded, then one: one bag waits."""
    first = place.batch(lines=[bagged(bags, 2)])
    recorded_then_changed(client, first, lines=[bagged(bags, 1)])


def test_needs_your_action_lists_it(
    admin_client: TestClient,
    registered_client: TestClient,
    place: Place,
    bags: ItemPackage,
) -> None:
    raised(registered_client, place, bags)
    home = text(admin_client.get("/admin"))
    assert str(over_recorded_page.TITLE) in home
    assert f"{place.cable.name}: 1 \N{MULTIPLICATION SIGN} Bag of 50" in home
    page = text(admin_client.get(f"{PAGE}?item={place.cable.id}"))
    assert f"from {place.store.name} to {place.van.name}" in page
    assert "1 \N{MULTIPLICATION SIGN} Bag of 50" in page


def test_correcting_one_undoes_it_on_each_side(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
    bags: ItemPackage,
) -> None:
    raised(registered_client, place, bags)
    (check,) = checks(session)
    form = {"seen": over_recorded.seen(check)}
    with capture_logs() as logs:
        response = admin_client.post(f"{PAGE}/{check.id}/correct", data=form)
    assert response.status_code == 303
    assert "over_recorded_corrected" in response.headers["location"]
    assert "Corrected." in text(admin_client.get(response.headers["location"]))
    (check,) = checks(session)
    assert check.settled_how == "corrected"
    # The bag found again in the store, written off from the van, sealed.
    adjusted = session.execute(
        select(
            LedgerTransaction.from_location_id,
            LedgerTransaction.to_location_id,
            Movement.quantity,
            Movement.from_package_id,
            Movement.to_package_id,
        )
        .join(Movement, Movement.transaction_id == LedgerTransaction.id)
        .where(LedgerTransaction.kind == "adjustment")
    ).all()
    assert sorted(adjusted, key=str) == sorted(
        [
            (None, place.store.id, Decimal(50), None, bags.id),
            (place.van.id, None, Decimal(50), bags.id, None),
        ],
        key=str,
    )
    assert any(line["event"] == "over recorded corrected" for line in logs)
    # Sent again, as a second tap would: nothing more.
    again = admin_client.post(f"{PAGE}/{check.id}/correct", data=form)
    assert "over_recorded_unchanged" in again.headers["location"]
    assert (
        "Not changed: someone answered or changed it meanwhile. Reload to see it "
        "as it is." in text(admin_client.get(again.headers["location"]))
    )
    assert str(over_recorded_page.TITLE) not in text(admin_client.get("/admin"))


def test_marking_one_checked_writes_nothing(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
    bags: ItemPackage,
) -> None:
    raised(registered_client, place, bags)
    response = mark_checked(admin_client, session, reason="arrived")
    assert "over_recorded_checked" in response.headers["location"]
    assert str(over_recorded_page.MARKED_CHECKED) in text(
        admin_client.get(response.headers["location"])
    )
    (check,) = checks(session)
    assert (check.settled_how, check.checked_reason, check.checked_words) == (
        "checked",
        "arrived",
        None,
    )
    assert check.settled_by_admin_id is not None
    assert (
        session.scalar(
            select(LedgerTransaction.id).where(LedgerTransaction.kind == "adjustment")
        )
        is None
    )


def mark_checked(client: TestClient, session: Session, **form: str) -> Response:
    """The one waiting marked checked, as seen, with `form`'s reason."""
    (check,) = checks(session)
    data = {"seen": over_recorded.seen(check)} | form
    return client.post(f"{PAGE}/{check.id}/checked", data=data)


def test_marking_one_checked_offers_each_reason(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
    bags: ItemPackage,
) -> None:
    """Ali, 2026-11-15 (inventory-tng-zgy)."""
    raised(registered_client, place, bags)
    page = admin_client.get(PAGE).text
    assert "<legend>Why it is right as it is</legend>" in page
    for value, words in over_recorded.CHECKED_REASONS.items():
        assert f'value="{value}" required> {words}</label>' in page


@pytest.mark.parametrize(
    "form",
    [{}, {"reason": "guessed"}, {"reason": "other"}, {"reason": "other", "words": " "}],
)
def test_marking_one_checked_asks_why(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
    bags: ItemPackage,
    form: dict[str, str],
) -> None:
    """No reason picked, or put right another way with nothing said: asked
    again, beside the reasons, what was typed kept."""
    raised(registered_client, place, bags)
    with capture_logs() as logs:
        response = mark_checked(admin_client, session, **form)
    assert response.status_code == 400
    (check,) = checks(session)
    assert (
        f'<span class="problem" id="reason-{check.id}-problem">'
        f"{over_recorded_page.SAY_WHY}</span>" in response.text
    )
    # Each reason, and the words, described by it, as the count is.
    assert response.text.count(f'aria-describedby="reason-{check.id}-problem"') == 5
    if form.get("reason") == "other":
        assert 'value="other" checked' in response.text
        assert f'name="words" value="{form.get("words", "")}"' in response.text
    assert check.settled_at is None
    assert "over recorded checked refused" in [line["event"] for line in logs]
    assert not any("words" in line for line in logs)


def test_put_right_another_way_keeps_the_words_as_typed(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
    bags: ItemPackage,
) -> None:
    """In canonical spelling (models.canonical), never logged."""
    raised(registered_client, place, bags)
    words = "Moved back by hand, 10µF"
    with capture_logs() as logs:
        response = mark_checked(
            admin_client, session, reason="other", words=f" {words} "
        )
    assert "over_recorded_checked" in response.headers["location"]
    (check,) = checks(session)
    assert (check.checked_reason, check.checked_words) == (
        "other",
        "Moved back by hand, 10\u03bcF",
    )
    assert "Moved back" not in repr(logs)
    assert any(line.get("reason") == "other" for line in logs)


def waiting_as(
    form: str, client: TestClient, session: Session, place: Place, bags: ItemPackage
) -> None:
    """One waiting, as Mark checked is offered: in its row, in a refusal's
    alert (here, Correct refused at a retired place), or, for an open
    package moved whole, only in a refusal's alert."""
    if form == "row":
        raised(client, place, bags)
    elif form == "refusal":
        from_emptied_store(client, session)
    else:
        moved_whole(client, Spools(session))


@pytest.mark.parametrize("form", ["row", "refusal", "moved whole"])
@pytest.mark.parametrize("reason", ["left", "arrived", "both"])
def test_words_for_another_reason_are_asked_about(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
    bags: ItemPackage,
    form: str,
    reason: str,
) -> None:
    """Never dropped (Ali, 2026-11-15): refused, nothing settled, what was
    typed kept, the problem beside the reasons."""
    waiting_as(form, registered_client, session, place, bags)
    if form == "refusal":
        assert correct(admin_client, session).status_code == 409
    with capture_logs() as logs:
        response = mark_checked(
            admin_client, session, reason=reason, words=" typed, then not "
        )
    assert response.status_code == 400
    (check,) = checks(session)
    assert (
        f'<span class="problem" id="reason-{check.id}-problem">'
        f"{over_recorded_page.WORDS_FOR_ANOTHER}</span>" in response.text
    )
    assert response.text.count(f'aria-describedby="reason-{check.id}-problem"') == 5
    assert f'value="{reason}" checked' in response.text
    assert 'name="words" value=" typed, then not "' in response.text
    assert check.settled_at is None
    assert check.checked_reason is None
    assert "typed, then not" not in repr(logs)


@pytest.mark.parametrize("words", ["", "  \t "])
def test_words_of_only_spaces_with_another_reason_are_none(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
    bags: ItemPackage,
    words: str,
) -> None:
    raised(registered_client, place, bags)
    response = mark_checked(admin_client, session, reason="both", words=words)
    assert "over_recorded_checked" in response.headers["location"]
    (check,) = checks(session)
    assert (check.checked_reason, check.checked_words) == ("both", None)


def test_words_too_long_are_asked_again(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
    bags: ItemPackage,
) -> None:
    raised(registered_client, place, bags)
    response = mark_checked(admin_client, session, reason="other", words="x" * 201)
    assert response.status_code == 400
    assert up_to(200) in response.text
    assert checks(session)[0].settled_at is None


@pytest.mark.parametrize(
    ("how", "reason", "words", "broken"),
    [
        ("checked", "guessed", None, "reason"),
        ("corrected", "both", None, "reason"),
        ("checked", "other", None, "words"),
        ("checked", "other", " ", "words"),
        ("checked", "other", "x" * 201, "words"),
        ("checked", "both", "said anyway", "words"),
        ("checked", None, "said anyway", "words"),
    ],
)
def test_the_database_keeps_reasons_only_as_mark_checked_gives_them(
    registered_client: TestClient,
    session: Session,
    place: Place,
    bags: ItemPackage,
    how: str,
    reason: str | None,
    words: str | None,
    broken: str,
) -> None:
    """A reason from the fixed list, only on one checked; words for another
    way, and only for it."""
    raised(registered_client, place, bags)
    (check,) = checks(session)
    admin = admin_id(session)

    def settle() -> None:
        check.settled_at = func.now()
        check.settled_by_admin_id = admin
        check.settled_how = how
        check.checked_reason = reason
        check.checked_words = words
        session.flush()

    assert attempt(session, settle) == (None, f"ck_over_recorded_checked_{broken}")


def test_the_page_offers_the_reasons_the_database_keeps() -> None:
    assert tuple(over_recorded.CHECKED_REASONS) == OVER_RECORDED_REASONS


def checked_forms(page: str) -> list[str]:
    """Each Mark checked form on `page`."""
    return re.findall(
        r'<form [^>]*action="[^"]*/checked[^>]*>.*?</form>', page, re.DOTALL
    )


def test_enter_in_the_words_marks_checked(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
    bags: ItemPackage,
) -> None:
    """Enter submits a form by its first button: Mark checked's form has
    no other, so Enter in the words never corrects it."""
    raised(registered_client, place, bags)
    (form,) = checked_forms(admin_client.get(PAGE).text)
    assert "Correct" not in form
    assert "formaction" not in form


def test_enter_in_the_words_of_a_refusal_marks_checked(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Nor brings a place back."""
    from_emptied_store(registered_client, session)
    response = correct(admin_client, session)
    assert response.status_code == 409
    forms = checked_forms(response.text)
    assert len(forms) == 2
    assert not any("formaction" in form or "Bring back" in form for form in forms)


@pytest.mark.parametrize("only", [False, True])
def test_one_settled_meanwhile_marked_checked_without_a_reason_says_so(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
    bags: ItemPackage,
    only: bool,
) -> None:
    """Not asked why for a row no longer there: told it changed, as with a
    reason."""
    raised(registered_client, place, bags)
    (check,) = checks(session)
    seen = over_recorded.seen(check)
    mark_checked(admin_client, session, reason="both")
    item = session.get_one(Movement, check.movement_id).item_id
    response = admin_client.post(
        f"{PAGE}/{check.id}/checked" + (f"?item={item}" if only else ""),
        data={"seen": seen, "reason": "other"},
    )
    assert response.status_code == 303
    assert "over_recorded_unchanged" in response.headers["location"]


def test_one_moved_whole_marked_checked_without_a_reason_is_asked_again_above(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Its row offers only Confirm count, so Mark checked, as a refusal
    offers it, is asked again above the list."""
    spools = Spools(session)
    moved_whole(registered_client, spools)
    response = mark_checked(admin_client, session)
    assert response.status_code == 400
    (check,) = checks(session)
    assert response.text.count(f'action="{PAGE}/{check.id}/checked"') == 1
    assert str(over_recorded_page.SAY_WHY) in response.text
    assert 'role="alert"' not in response.text


def test_one_said_again_since_it_was_shown_is_not_settled(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
    bags: ItemPackage,
) -> None:
    raised(registered_client, place, bags)
    (check,) = checks(session)
    response = admin_client.post(
        f"{PAGE}/{check.id}/checked", data={"seen": "2", "reason": "both"}
    )
    assert "over_recorded_unchanged" in response.headers["location"]
    assert checks(session)[0].settled_at is None


def test_only_its_answers_are_taken(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
    bags: ItemPackage,
) -> None:
    raised(registered_client, place, bags)
    (check,) = checks(session)
    assert admin_client.post(f"{PAGE}/{check.id}/delete").status_code == 404
    assert admin_client.post(f"{PAGE}/{check.id + 1000}/checked").status_code == 404


@pytest.mark.concurrency
def test_one_answered_meanwhile_waits_then_is_refused(own_committed: Committed) -> None:
    """Two administrators answering it at once: the second waits for the
    first's row lock, then finds it settled. On a database of its own, as
    the check's line is in the ledger."""
    setup = own_committed.session()
    place = Place(setup)
    transaction = write(setup, place)
    movement_id = setup.scalar(
        select(Movement.id).where(Movement.transaction_id == transaction.id)
    )
    assert movement_id is not None
    over_recorded.raise_checks(setup, {movement_id: Decimal(1)})
    admin = admin_id(setup)
    setup.commit()
    (check,) = checks(setup)
    first, second = own_committed.session(), own_committed.session()
    seen = over_recorded.seen(check)
    over_recorded.mark_checked(first, check.id, seen, admin, "both")
    worker = own_committed.blocked(
        second,
        lambda: over_recorded.mark_checked(second, check.id, seen, admin, "both"),
    )
    first.commit()
    finished = worker.finished()
    second.rollback()
    assert isinstance(finished.error, RuleBroken)
    assert finished.error.rule == "over_recorded_unchanged"


def test_an_install_corrected_is_found_again_where_it_left(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
) -> None:
    """An install has no place it arrives at: one adjustment only."""
    first = place.batch(kind="install", to_location_id=None, install_number=12)
    recorded_then_changed(
        registered_client, first, lines=[{"item_id": place.item.id, "quantity": "1"}]
    )
    (check,) = checks(session)
    admin_client.post(
        f"{PAGE}/{check.id}/correct", data={"seen": over_recorded.seen(check)}
    )
    adjusted = session.execute(
        select(LedgerTransaction.to_location_id, Movement.quantity)
        .join(Movement, Movement.transaction_id == LedgerTransaction.id)
        .where(LedgerTransaction.kind == "adjustment")
    ).all()
    assert adjusted == [(place.store.id, Decimal(2))]


def test_a_cut_taken_down_is_listed_from_its_open_package(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    spools = Spools(session)
    made = spools.opened()
    first = spools.place.batch(lines=[cut(made, "20")])
    recorded_then_changed(registered_client, first, lines=[cut(made, "15")])
    page = text(admin_client.get(PAGE))
    assert over_recorded.from_an_open_package("5 m") in page


def test_what_a_batch_was_changed_from_does_not_make_it_another(
    registered_client: TestClient, place: Place
) -> None:
    """Sent again with its lines as recorded, it is the same batch, whatever
    it says it was changed from."""
    first = place.batch()
    created = registered_client.post(URL, json=first)
    assert created.status_code == 201
    again = registered_client.post(
        URL,
        json=first | {"changed_from": [{"item_id": place.item.id, "quantity": "9"}]},
    )
    assert again.status_code == 200
    assert again.json() == created.json()


def moved_whole(
    client: TestClient, spools: Spools, reported: str | None = None
) -> OpenPackage:
    """A spool's open package moved whole from the store to the van and
    recorded, then sent again without it: it waits, whole. With
    `reported`, the batch that moved it said that length was left on it."""
    made = spools.opened()
    whole = cut(made, "305") | {"whole": True}
    loose = {"item_id": spools.place.item.id, "quantity": "1"}
    lengths = [length(made, reported)] if reported else None
    recorded_then_changed(
        client,
        spools.place.batch(lines=[whole, loose], lengths=lengths),
        lines=[loose],
        lengths=None,
    )
    return made


def counted(
    admin_client: TestClient, session: Session, count: str, **form: str
) -> tuple[OverRecorded, Any]:
    """The one waiting, and the answer to its count sent as `count`."""
    (check,) = checks(session)
    data = {"seen": over_recorded.seen(check), "count": count} | form
    return check, admin_client.post(f"{PAGE}/{check.id}/count", data=data)


def adjustments(session: Session) -> list[tuple[object, ...]]:
    return [
        tuple(each)
        for each in session.execute(
            select(
                LedgerTransaction.from_location_id,
                LedgerTransaction.to_location_id,
                Movement.quantity,
                Movement.from_open_package_id,
                Movement.to_open_package_id,
                AdjustmentReason.name,
            )
            .join(Movement, Movement.transaction_id == LedgerTransaction.id)
            .join(AdjustmentReason, AdjustmentReason.id == LedgerTransaction.reason_id)
            .where(LedgerTransaction.kind == "adjustment")
        )
    ]


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
    assert 'value="305"' in page
    assert f'<p class="hint" id="count-{check.id}-was" hidden>Was 305 m</p>' in page
    assert "Confirm count" in page
    assert "/checked" not in page
    assert "/correct" not in page


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
    assert str(over_recorded_page.COUNT_CORRECTED) in text(
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
    written = ledger.record_adjustment(
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
        assert f'value="{typed}"' in page
        # Edited, what it held shows under it.
        assert f'<p class="hint" id="count-{check.id}-was">Was 305 m</p>' in page
    assert checks(session)[0].settled_at is None
    assert adjustments(session) == []


def test_a_count_is_taken_only_for_an_open_package_moved_whole(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
    bags: ItemPackage,
) -> None:
    raised(registered_client, place, bags)
    with capture_logs() as logs:
        _, response = counted(admin_client, session, "50")
    assert "over_recorded_unchanged" in response.headers["location"]
    assert checks(session)[0].settled_at is None
    assert adjustments(session) == []
    refused = [line for line in logs if line["event"] == "over recorded not settled"]
    assert refused[0]["rule"] == "over_recorded_not_whole"


def test_the_page_says_what_it_is_for(admin_client: TestClient) -> None:
    page = " ".join(text(admin_client.get(PAGE)).split())
    assert (
        "Another tab recorded these before a volunteer changed them, so the "
        "inventory shows more moved than was. Correct moves the extra back with "
        "an adjustment. Mark checked if you already fixed it." in page
    )


# Refused by a rule (inventory-tng-axb).


def from_emptied_store(client: TestClient, session: Session) -> Place:
    """Three radios received into the store and moved to the van, then sent
    again as one: two wait, and the store holds nothing, retired."""
    place = Place(session)
    received(session, place)
    recorded_then_changed(
        client, place.batch(), lines=[{"item_id": place.item.id, "quantity": "1"}]
    )
    retire(session, place.store)
    return place


def correct(client: TestClient, session: Session) -> Response:
    (check,) = checks(session)
    return client.post(
        f"{PAGE}/{check.id}/correct", data={"seen": over_recorded.seen(check)}
    )


def bring_back(
    client: TestClient, session: Session, location: Location, **form: str
) -> Response:
    (check,) = checks(session)
    data = {"seen": over_recorded.seen(check), "place": str(location.id)} | form
    return client.post(f"{PAGE}/{check.id}/bring-back", data=data)


def test_refused_at_a_retired_place_it_says_why_and_offers_a_fix(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    place = from_emptied_store(registered_client, session)
    response = correct(admin_client, session)
    assert response.status_code == 409
    page = " ".join(text(response).split())
    assert over_recorded_page.RETIRED_EMPTY["found"](place.store.name, "2") in page
    assert f"Bring back {place.store.name}" in page
    (check,) = checks(session)
    assert f'action="{PAGE}/{check.id}/bring-back"' in response.text
    assert f'name="place" value="{place.store.id}"' in response.text
    assert "someone answered or changed it meanwhile" not in page
    assert check.settled_at is None
    assert adjustments(session) == []


def test_refused_where_it_arrived_it_names_that_place(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """The store current, the van retired after all it got left it: the
    write-off there is what is refused."""
    place = Place(session)
    received(session, place)
    recorded_then_changed(
        registered_client,
        place.batch(),
        lines=[{"item_id": place.item.id, "quantity": "1"}],
    )
    write(
        session,
        place,
        from_location_id=place.van.id,
        to_location_id=place.store.id,
    )
    retire(session, place.van)
    page = " ".join(text(correct(admin_client, session)).split())
    assert over_recorded_page.RETIRED_EMPTY["written_off"](place.van.name, "2") in page
    assert f"Bring back {place.van.name}" in page


def emptied_van(client: TestClient, session: Session) -> Spools:
    """An open package moved whole to the van, waiting, then moved back to
    the store with all the van held, and the van retired: its count, where
    it arrived, cannot be corrected there."""
    spools = Spools(session)
    made = moved_whole(client, spools)
    whole = Form(open_package_id=made.id)
    spools.write(
        {
            "kind": "move",
            "volunteer_id": spools.place.volunteer.id,
            "from_location_id": spools.van.id,
            "to_location_id": spools.store.id,
        },
        Entry(spools.cable.id, Decimal(305), whole, whole),
        Entry(spools.place.item.id, Decimal(1)),
    )
    retire(session, spools.van)
    return spools


def test_a_count_refused_where_it_arrived_names_that_place(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    spools = emptied_van(registered_client, session)
    check, response = counted(admin_client, session, "180")
    assert response.status_code == 409
    page = " ".join(text(response).split())
    assert (
        f"Not changed: {spools.van.name} is retired and holds nothing, so its "
        "count can't be corrected there." in page
    )
    assert f"Bring back {spools.van.name}" in page
    # Mark checked is offered there too, and what was typed is kept.
    assert f'action="{PAGE}/{check.id}/checked"' in response.text
    assert 'value="180"' in response.text


def refused_then(
    monkeypatch: pytest.MonkeyPatch, meanwhile: Callable[[], None]
) -> None:
    """`meanwhile` done by another administrator once a correction or count
    is refused, before the page says why (inventory-tng-k4l)."""
    attempt = over_recorded_page.attempt

    def then[T](at: Session, change: Callable[[], T]) -> tuple[T | None, str | None]:
        done = attempt(at, change)
        meanwhile()
        return done

    monkeypatch.setattr(over_recorded_page, "attempt", then)


def test_refused_at_a_place_brought_back_meanwhile_it_names_that_place(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The place as it refused, not as it is when the page is drawn."""
    place = from_emptied_store(registered_client, session)

    refused_then(monkeypatch, lambda: locations.restore(session, place.store))
    response = correct(admin_client, session)
    assert response.status_code == 409
    page = " ".join(text(response).split())
    assert over_recorded_page.RETIRED_EMPTY["found"](place.store.name, "2") in page
    assert f"Bring back {place.store.name}" in page


def test_a_count_refused_at_a_place_brought_back_meanwhile_names_it(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirm count, refused where it arrived: that place, as it refused."""
    spools = emptied_van(registered_client, session)
    refused_then(monkeypatch, lambda: locations.restore(session, spools.van))
    _, response = counted(admin_client, session, "180")
    assert response.status_code == 409
    page = " ".join(text(response).split())
    assert (
        f"Not changed: {spools.van.name} is retired and holds nothing, so its "
        "count can't be corrected there." in page
    )


def test_refused_then_settled_meanwhile_it_says_so(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from_emptied_store(registered_client, session)
    (check,) = checks(session)
    seen = over_recorded.seen(check)
    admin_id = session.scalar(select(Admin.id))
    assert admin_id is not None
    refused_then(
        monkeypatch,
        lambda: over_recorded.mark_checked(session, check.id, seen, admin_id, "both"),
    )
    response = correct(admin_client, session)
    assert response.status_code == 303
    assert "over_recorded_unchanged" in response.headers["location"]


def test_refused_by_another_rule_it_says_that_rule(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    place = Place(session)
    recorded_then_changed(
        registered_client,
        place.batch(),
        lines=[{"item_id": place.item.id, "quantity": "1"}],
    )

    def refused(*_: object) -> None:
        raise RuleBroken("movement_whole_units")

    monkeypatch.setattr(over_recorded, "correct", refused)
    response = correct(admin_client, session)
    assert response.status_code == 409
    page = " ".join(text(response).split())
    assert over_recorded_page.not_changed(str(RULES["movement_whole_units"])) in page
    assert "Bring back" not in page
    assert "Mark checked" in page
    # Asking why there too, as the row does (inventory-tng-zgy).
    assert response.text.count("<legend>Why it is right as it is</legend>") == 2


def test_bringing_the_place_back_lets_it_be_corrected(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    place = from_emptied_store(registered_client, session)
    with capture_logs() as logs:
        response = bring_back(admin_client, session, place.store)
    assert response.status_code == 303
    location = response.headers["location"]
    assert "over_recorded_brought_back" in location
    assert over_recorded_page.place_brought_back(place.store.name) in text(
        admin_client.get(location)
    )
    session.expire_all()
    assert place.store.retired_at is None
    # Recorded as any place change is: who, and when.
    actor = session.scalar(
        select(CatalogueHistory.actor)
        .where(
            CatalogueHistory.table_name == "location",
            CatalogueHistory.row_id == place.store.id,
            CatalogueHistory.new_row["retired_at"].astext.is_(None),
        )
        .order_by(CatalogueHistory.id.desc())
    )
    assert actor is not None
    assert actor.startswith("admin:")
    (event,) = [
        line for line in logs if line["event"] == "over recorded place brought back"
    ]
    assert event["location_id"] == place.store.id
    assert correct(admin_client, session).status_code == 303
    assert checks(session)[0].settled_how == "corrected"


def test_brought_back_twice_it_is_brought_back_once(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """A second tap, or another administrator first: said the same way."""
    place = from_emptied_store(registered_client, session)
    with capture_logs() as logs:
        first = bring_back(admin_client, session, place.store)
        again = bring_back(admin_client, session, place.store)
    assert first.headers["location"] == again.headers["location"]
    brought = [
        line for line in logs if line["event"] == "over recorded place brought back"
    ]
    assert len(brought) == 1


def test_a_place_is_not_brought_back_for_one_settled_meanwhile(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    place = from_emptied_store(registered_client, session)
    response = bring_back(admin_client, session, place.store, seen="9")
    assert "over_recorded_unchanged" in response.headers["location"]
    session.expire_all()
    assert place.store.retired_at is not None


def test_a_place_is_not_brought_back_for_one_settled_as_it_is(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Marked checked by another administrator after the page read it,
    before the place was brought back: nothing comes back."""
    place = from_emptied_store(registered_client, session)
    (check,) = checks(session)
    seen = over_recorded.seen(check)
    admin_id = session.scalar(select(Admin.id))
    assert admin_id is not None
    restore = locations.restore

    def checked_meanwhile(at: Session, location: Location) -> None:
        over_recorded.mark_checked(at, check.id, seen, admin_id, "left")
        restore(at, location)

    monkeypatch.setattr(locations, "restore", checked_meanwhile)
    response = bring_back(admin_client, session, place.store)
    assert "over_recorded_unchanged" in response.headers["location"]
    session.expire_all()
    assert place.store.retired_at is not None


def test_only_its_own_places_are_brought_back(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    place = from_emptied_store(registered_client, session)
    other = add(session, Location(name=f"Shelf {tag()}", kind="store"))
    retire(session, other)
    assert bring_back(admin_client, session, other).status_code == 404
    assert bring_back(admin_client, session, place.store, place="x").status_code == 404
    session.expire_all()
    assert other.retired_at is not None


def test_a_place_whose_own_place_is_retired_says_so(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """An area inside a retired store comes back only after it, as on the
    places page."""
    place = Place(session)
    shelf = add(
        session, Location(name=f"Shelf {tag()}", kind="area", parent_id=place.store.id)
    )
    received(session, place)
    write(
        session,
        place,
        kind="receipt",
        from_location_id=None,
        to_location_id=shelf.id,
    )
    recorded_then_changed(
        registered_client,
        place.batch(from_location_id=shelf.id),
        lines=[{"item_id": place.item.id, "quantity": "1"}],
    )
    retire(session, shelf, place.store)
    response = bring_back(admin_client, session, shelf)
    assert response.status_code == 409
    page = " ".join(text(response).split())
    assert f"Not changed: {RULES['location_parent_current']}" in page
    session.expire_all()
    assert shelf.retired_at is not None


# Mark checked refused from a refusal's alert (inventory-tng-e5b).


def checked_from_alert(
    client: TestClient, session: Session, at: Location, **form: str
) -> Response:
    """Mark checked sent from a refusal's alert, as refused at `at`."""
    return mark_checked(client, session, refused=str(at.id), **form)


def problem_of(check: OverRecorded) -> str:
    return f'<span class="problem" id="reason-{check.id}-problem">'


@pytest.mark.parametrize(
    ("form", "problem"),
    [
        ({}, over_recorded_page.SAY_WHY),
        ({"reason": "both", "words": "typed"}, over_recorded_page.WORDS_FOR_ANOTHER),
        ({"reason": "other", "words": "x" * 201}, up_to(200)),
    ],
)
def test_mark_checked_refused_from_an_alert_draws_the_alert_again(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    form: dict[str, str],
    problem: object,
) -> None:
    """With Bring back, the problem under it, in its form only, what was
    typed kept there (Claude, 2026-11-15, left to Claude by Ali)."""
    place = from_emptied_store(registered_client, session)
    refusal = correct(admin_client, session)
    assert f'name="refused" value="{place.store.id}"' in refusal.text
    response = checked_from_alert(admin_client, session, place.store, **form)
    assert response.status_code == 400
    page = " ".join(text(response).split())
    assert over_recorded_page.RETIRED_EMPTY["found"](place.store.name, "2") in page
    assert f"Bring back {place.store.name}" in page
    assert str(problem) in page
    (check,) = checks(session)
    alert, row = checked_forms(response.text)
    assert problem_of(check) in alert
    assert f'name="refused" value="{place.store.id}"' in alert
    assert problem_of(check) not in row
    if form:
        kept = (f'value="{form["reason"]}" checked', f'value="{form["words"]}"')
        assert all(each in alert and each not in row for each in kept)
    assert check.settled_at is None


def test_mark_checked_refused_from_a_counts_alert_draws_it_again(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """An open package moved whole, its count refused: that alert, not
    Mark checked asked again alone."""
    spools = emptied_van(registered_client, session)
    counted(admin_client, session, "180")
    response = checked_from_alert(admin_client, session, spools.van)
    assert response.status_code == 400
    page = " ".join(text(response).split())
    assert (
        f"Not changed: {spools.van.name} is retired and holds nothing, so its "
        "count can't be corrected there." in page
    )
    assert f"Bring back {spools.van.name}" in page
    assert str(over_recorded_page.SAY_WHY) in page


def test_a_marker_not_of_a_refusal_draws_no_alert(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """The place read again, not trusted: one not of its transaction,
    though retired and empty, one that may be counted (here, the van,
    current), or no id at all is not drawn as refused; the problem is the
    row's."""
    place = from_emptied_store(registered_client, session)
    elsewhere = add(session, Location(name=f"Store {tag()}", kind="store"))
    retire(session, elsewhere)
    (check,) = checks(session)
    for marker in (str(elsewhere.id), str(place.van.id), "x", "-1", "1.0"):
        response = mark_checked(admin_client, session, refused=marker)
        assert response.status_code == 400
        assert 'role="alert"' not in response.text
        assert "Bring back" not in response.text
        (row,) = checked_forms(response.text)
        assert problem_of(check) in row


def test_an_alert_drawn_again_keeps_only_one_item(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Its forms post back to the list of that item alone."""
    place = from_emptied_store(registered_client, session)
    (check,) = checks(session)
    response = admin_client.post(
        f"{PAGE}/{check.id}/checked?item={place.item.id}",
        data={"seen": over_recorded.seen(check), "refused": str(place.store.id)},
    )
    assert response.status_code == 400
    assert f"Only {place.item.name}." in text(response)
    for each in ("bring-back", "checked"):
        assert f'action="{PAGE}/{check.id}/{each}?item={place.item.id}"' in (
            response.text
        )


def test_a_place_brought_back_since_its_alert_draws_no_alert(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    place = from_emptied_store(registered_client, session)
    correct(admin_client, session)
    locations.restore(session, place.store)
    session.flush()
    response = checked_from_alert(admin_client, session, place.store)
    assert response.status_code == 400
    assert 'role="alert"' not in response.text
    assert "Bring back" not in response.text
    assert str(over_recorded_page.SAY_WHY) in response.text


# A count of an end puts it right (Ali, 2026-11-15, inventory-tng-qs8).


def radios_raised(client: TestClient, place: Place) -> None:
    """Three radios recorded moved from the store to the van, then one: two
    wait, loose at each end."""
    recorded_then_changed(
        client,
        place.batch(),
        lines=[{"item_id": place.item.id, "quantity": "1"}],
    )


def count_ids(session: Session) -> list[int]:
    return list(
        session.scalars(
            select(LedgerTransaction.id)
            .where(LedgerTransaction.kind == "count")
            .order_by(LedgerTransaction.id)
        )
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
    assert f"Correct {place.store.name}<span" in page
    link = f"{COUNTS}/{place.store.id}?item={place.item.id}"
    assert f'<a href="{link}">Count {place.store.name}<span' in page
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


def text_of(page: str) -> str:
    """A page's words, tags taken out, spaces as one."""
    return " ".join(re.sub(r"<[^>]+>", " ", page).split())


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
    assert f"Correct {place.store.name}<span" in page
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


def opening_raised(client: TestClient, spools: Spools) -> OverRecorded:
    """Both sealed spools in the store recorded opened, then one: one
    waits, sealed where it left, open (tracked) or loose where it
    arrived."""
    line = {
        "item_id": spools.cable.id,
        "quantity": "305",
        "package_id": spools.spool.id,
        "package_size": "305",
    }
    # A page sends a line for each tracked one opened, under its own key;
    # one line of them all for loose units.
    lines = (
        [line | {"open_package_key": str(uuid.uuid4())} for _ in range(2)]
        if spools.cable.track_opened
        else [line | {"quantity": "610"}]
    )
    opening = spools.place.batch(
        kind="open",
        from_location_id=spools.store.id,
        to_location_id=spools.store.id,
        lines=lines,
    )
    recorded_then_changed(client, opening, lines=[lines[0] | {"quantity": "305"}])
    (check,) = checks(spools.session)
    return check


def opened_into(session: Session, check: OverRecorded) -> Form:
    """The form its movement arrived in: the open package, or loose."""
    movement = session.get_one(Movement, check.movement_id)
    return Form(movement.to_package_id, movement.to_open_package_id)


def arrived_field(spools: Spools, into: Form) -> str:
    """The count form's field of the form an opening arrived in."""
    if into.open_package_id is not None:
        return f"opened_{into.open_package_id}"
    return f"loose_{spools.cable.id}"


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
    assert "Correct open SFP+ module<span" in page


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
    assert f"Correct {correct}<span" in page
    link = f"{COUNTS}/{spools.store.id}?item={spools.cable.id}"
    assert f'<a href="{link}">Count {spools.store.name}<span' in page


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


# An opening's sealed end can be counted though the records hold none of
# it sealed, and an open package it arrived in closed on a count puts that
# end right (Ali, 2026-11-15, inventory-tng-he9, inventory-tng-dd8).


def sealed_field(spools: Spools) -> str:
    return f"sealed_{spools.cable.id}_{spools.spool.id}"


def sealed_line(page: str, spools: Spools) -> str | None:
    """The value a count page fills a line of sealed spools with, if it has
    one."""
    found = re.search(rf'name="{sealed_field(spools)}"[^>]*value="([^"]*)"', page)
    return found and found.group(1)


def reason_adjustments(session: Session, reason: str) -> list[Movement]:
    """The lines of adjustments with `reason`."""
    return list(
        session.scalars(
            select(Movement)
            .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
            .join(AdjustmentReason, AdjustmentReason.id == LedgerTransaction.reason_id)
            .where(AdjustmentReason.name == reason)
        )
    )


@pytest.mark.parametrize("state", ["missing", "used_up"])
def test_its_open_package_closed_on_a_count_puts_that_end_right_then_correct_works(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    state: str,
) -> None:
    """Two sealed spools recorded opened, one really: the phantom open
    package said not there (or used up) on a count is at 0, as a count of 0
    would say, so Correct writes only the sealed spool found again."""
    spools = Spools(session)
    check = opening_raised(registered_client, spools)
    into = opened_into(session, check)
    assert into.open_package_id is not None
    open_id = into.open_package_id
    done = count_at(
        admin_client,
        spools.store.id,
        {f"opened_{open_id}": "", f"state_{open_id}": state},
    )
    assert done.status_code == 303
    (counted_at,) = count_ids(session)
    (check,) = checks(session)
    assert (check.left_count_id, check.arrived_count_id) == (None, counted_at)
    assert check.settled_at is None
    done = admin_client.post(
        f"{PAGE}/{check.id}/correct", data={"seen": over_recorded.seen(check)}
    )
    assert "over_recorded_corrected" in done.headers["location"]
    (line,) = reason_adjustments(session, over_recorded.REASON)
    assert (line.quantity, line.to_package_id, line.to_open_package_id) == (
        305,
        spools.spool.id,
        None,
    )
    session.expire_all()
    forms = ledger.expected_forms(
        session,
        spools.store.id,
        ledger.moment(session, spools.store.id),
        [spools.cable.id],
    )
    assert forms == {(spools.cable.id, spools.sealed): 305} | {
        (spools.cable.id, Form(open_package_id=other.id)): 305
        for other in session.scalars(
            select(OpenPackage).where(
                OpenPackage.closed_at.is_(None), OpenPackage.id != open_id
            )
        )
    }


def test_its_open_package_not_closed_by_the_count_puts_no_end_right(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closed or moved meanwhile, as closing finds it under its lock: this
    count did not close it, so puts nothing right of it."""
    spools = Spools(session)
    check = opening_raised(registered_client, spools)
    open_id = opened_into(session, check).open_package_id
    monkeypatch.setattr(open_packages, "close", lambda *_, **__: None)
    count_at(
        admin_client,
        spools.store.id,
        {f"opened_{open_id}": "", f"state_{open_id}": "missing"},
    )
    (check,) = checks(session)
    assert (check.left_count_id, check.arrived_count_id) == (None, None)


def test_closing_its_open_package_and_counting_the_sealed_spool_closes_it(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    spools = Spools(session)
    check = opening_raised(registered_client, spools)
    open_id = opened_into(session, check).open_package_id
    page = admin_client.get(f"{COUNTS}/{spools.store.id}").text
    assert sealed_line(page, spools) == "0"
    count_at(
        admin_client,
        spools.store.id,
        {
            sealed_field(spools): "1",
            f"opened_{open_id}": "",
            f"state_{open_id}": "missing",
        },
    )
    (counted_at,) = count_ids(session)
    (check,) = checks(session)
    assert (check.settled_how, check.left_count_id, check.arrived_count_id) == (
        "counted",
        counted_at,
        counted_at,
    )
    assert reason_adjustments(session, over_recorded.REASON) == []


def test_an_open_package_closed_on_a_count_puts_no_move_right(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Moved whole, its open package's own count puts its end right; closed
    on a count, nothing does: Confirm count or Mark checked, as before."""
    spools = Spools(session)
    made = moved_whole(registered_client, spools)
    count_at(
        admin_client,
        spools.van.id,
        {f"opened_{made.id}": "", f"state_{made.id}": "missing"},
    )
    (check,) = checks(session)
    assert (check.left_count_id, check.arrived_count_id) == (None, None)
    assert check.settled_at is None


@pytest.mark.parametrize("tracked", [True, False])
def test_counting_its_place_from_the_row_counts_the_sealed_spool_and_closes_it(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    tracked: bool,
) -> None:
    """Its open or loose end counted, 'Count <place>' leads to the item's
    count with a line of sealed spools, at 0 as the records hold none; not
    tracked, the store holds loose ones alone but is counted by form."""
    spools = Spools(session, tracked=tracked)
    check = opening_raised(registered_client, spools)
    count_at(
        admin_client,
        spools.store.id,
        {arrived_field(spools, opened_into(session, check)): "305"},
    )
    link = f"{COUNTS}/{spools.store.id}?item={spools.cable.id}"
    assert (
        f'<a href="{link}">' in admin_client.get(f"{PAGE}?item={spools.cable.id}").text
    )
    page = admin_client.get(link).text
    assert sealed_line(page, spools) == "0"
    assert f'name="counted_{spools.cable.id}"' not in page
    form = count_form_at(admin_client, spools.store.id)
    done = admin_client.post(link, data=form | {sealed_field(spools): "1"})
    assert done.status_code == 303
    (check,) = checks(session)
    assert check.settled_how == "counted"
    session.expire_all()
    store = spools.store.id
    sealed = (spools.cable.id, spools.sealed)
    found = ledger.expected_forms(
        session, store, ledger.moment(session, store), [spools.cable.id]
    )
    assert found[sealed] == 305


def test_the_sealed_line_is_shown_as_its_item_is_by_the_filter(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    spools = Spools(session, tracked=False)
    opening_raised(registered_client, spools)
    store = f"{COUNTS}/{spools.store.id}"
    for shown in ("", f"?q={spools.cable.name}", f"?item={spools.cable.id}"):
        assert sealed_line(admin_client.get(store + shown).text, spools) == "0"
    other = spools.place.item.id
    for hidden in (f"?item={other}", "?q=nothingcalledthis"):
        assert sealed_line(admin_client.get(store + hidden).text, spools) is None


def test_its_item_is_counted_though_the_records_hold_none_of_it_here(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Both open packages closed on a count, the store holds no cable: the
    sealed end still waits, so the store's count asks for it."""
    spools = Spools(session)
    opening_raised(registered_client, spools)
    shut = {
        field: value
        for row in session.scalars(select(OpenPackage))
        for field, value in ((f"opened_{row.id}", ""), (f"state_{row.id}", "used_up"))
    }
    count_at(admin_client, spools.store.id, shut)
    session.expire_all()
    assert expected(session, spools.store.id).get(spools.cable.id, 0) == 0
    page = admin_client.get(f"{COUNTS}/{spools.store.id}").text
    assert sealed_line(page, spools) == "0"
    count_at(admin_client, spools.store.id, {sealed_field(spools): "1"})
    (check,) = checks(session)
    assert check.settled_how == "counted"


def test_its_sealed_line_left_at_0_writes_nothing_and_counts_that_end(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Left as it is, 0 is what was counted, as any line: nothing written
    of sealed spools, the sealed end put right."""
    spools = Spools(session, tracked=False)
    check = opening_raised(registered_client, spools)
    count_at(
        admin_client,
        spools.store.id,
        {sealed_field(spools): "0", f"loose_{spools.cable.id}": "610"},
    )
    (counted_at,) = count_ids(session)
    assert (
        list(
            session.scalars(
                select(Movement).where(Movement.transaction_id == counted_at)
            )
        )
        == []
    )
    (check,) = checks(session)
    assert check.settled_how == "counted"


def test_its_loose_only_place_counted_by_form_puts_each_form_right(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Not tracked, the store holds 610 loose alone, really one spool sealed
    and 305 loose: counted by form, each form is put right, the total kept,
    and the store's other items as they were."""
    spools = Spools(session, tracked=False)
    opening_raised(registered_client, spools)
    store = spools.store.id
    others = {
        item_id: amount
        for item_id, amount in expected(session, store).items()
        if item_id != spools.cable.id
    }
    count_at(
        admin_client,
        store,
        {sealed_field(spools): "1", f"loose_{spools.cable.id}": "305"},
    )
    (check,) = checks(session)
    assert check.settled_how == "counted"
    session.expire_all()
    found = ledger.expected_forms(
        session, store, ledger.moment(session, store), [spools.cable.id]
    )
    assert found == {
        (spools.cable.id, spools.sealed): 305,
        (spools.cable.id, Form()): 305,
    }
    now = expected(session, store)
    assert now[spools.cable.id] == 610
    assert {item_id: now[item_id] for item_id in others} == others


def test_a_move_waiting_asks_no_sealed_line(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Where an open package moved whole arrived, the van holds none
    sealed, and none is asked for."""
    spools = Spools(session)
    moved_whole(registered_client, spools)
    for place in (spools.store, spools.van):
        moment = ledger.moment(session, place.id)
        assert over_recorded.openings_waiting(session, place.id, moment) == {}
    page = admin_client.get(f"{COUNTS}/{spools.van.id}").text
    assert sealed_line(page, spools) is None


def committed_check(
    own_committed: Committed,
) -> tuple[Place, OverRecorded, int, LedgerTransaction]:
    """A move of three radios from the store to the van with one recorded
    more, committed: its place, the check, an administrator, and the
    move."""
    setup = own_committed.session()
    place = Place(setup)
    moved = write(setup, place)
    movement_id = setup.scalar(
        select(Movement.id).where(Movement.transaction_id == moved.id)
    )
    assert movement_id is not None
    over_recorded.raise_checks(setup, {movement_id: Decimal(1)})
    admin = admin_id(setup)
    setup.commit()
    (check,) = checks(setup)
    return place, check, admin, moved


def count_van(
    session: Session, place: Place, admin: int, as_of: int, at: Location | None = None
) -> int:
    """The van, or `at`, counted as of `as_of`, as the count page does: how
    many it closed."""
    where = (at or place.van).id
    counted = ledger.record_count(
        session, uuid.uuid4(), admin, where, [(place.item.id, Decimal(2))], as_of
    )
    return over_recorded.counted(
        session, admin, where, as_of, counted.written, counted.put_right
    )


def correct_check(session: Session, check: OverRecorded, seen: str, admin: int) -> None:
    """Corrected as the page does: its places, then it."""
    ledger.lock_places(session, over_recorded.places_of(session, check.id))
    over_recorded.correct(session, check.id, seen, admin)


@pytest.mark.concurrency
def test_a_correct_waits_for_a_count_of_its_end_then_is_refused(
    own_committed: Committed,
) -> None:
    """The count holds the van, then the check; Correct takes its places
    first, so waits, then finds an end counted since it was seen."""
    place, check, admin, moved = committed_check(own_committed)
    first, second = own_committed.session(), own_committed.session()
    seen = over_recorded.seen(check)
    assert count_van(first, place, admin, moved.id) == 0
    worker = own_committed.blocked(
        second, lambda: correct_check(second, check, seen, admin)
    )
    first.commit()
    finished = worker.finished()
    second.rollback()
    assert isinstance(finished.error, RuleBroken)
    assert finished.error.rule == "over_recorded_unchanged"
    (check,) = checks(first)
    assert (check.settled_at, check.arrived_count_id) == (None, count_ids(first)[0])


@pytest.mark.concurrency
def test_a_count_waits_for_a_correct_then_puts_nothing_right(
    own_committed: Committed,
) -> None:
    place, check, admin, moved = committed_check(own_committed)
    first, second = own_committed.session(), own_committed.session()
    correct_check(first, check, over_recorded.seen(check), admin)
    worker = own_committed.blocked(
        second, lambda: count_van(second, place, admin, moved.id)
    )
    first.commit()
    finished = worker.finished()
    second.commit()
    assert (finished.error, finished.result) == (None, 0)
    (check,) = checks(first)
    assert (check.settled_how, check.arrived_count_id) == ("corrected", None)


@pytest.mark.concurrency
def test_both_ends_counted_at_once_the_second_closes_it(
    own_committed: Committed,
) -> None:
    """Each count holds the check it puts right, so the second reads the
    first's end once it is in, and closes it, never two ends counted and
    still waiting."""
    place, check, admin, moved = committed_check(own_committed)
    first, second = own_committed.session(), own_committed.session()
    assert count_van(first, place, admin, moved.id) == 0
    worker = own_committed.blocked(
        second, lambda: count_van(second, place, admin, moved.id, place.store)
    )
    first.commit()
    finished = worker.finished()
    second.commit()
    assert (finished.error, finished.result) == (None, 1)
    (check,) = checks(first)
    assert check.settled_how == "counted"


# An opening's open package closed since (Confirm used up, Close, a count
# whose lines were all withheld): Correct writes the sealed end alone, as
# closing wrote off what it showed (Ali, 2026-11-15, inventory-tng-nyl).


def closed_by_confirm(admin_client: TestClient, spools: Spools, open_id: int) -> None:
    """Reported used up by a volunteer, then confirmed."""
    session = spools.session
    open_packages.report(
        session, [open_id], spools.place.volunteer.id, datetime.now(UTC)
    )
    session.commit()
    done = admin_client.post(f"/admin/stock/used-up/{open_id}/confirm")
    assert "used_up_confirmed" in done.headers["location"]


def closed_from_its_item(
    admin_client: TestClient, spools: Spools, open_id: int
) -> None:
    done = admin_client.post(
        f"/admin/items/{spools.cable.id}/open-packages/{open_id}/close"
    )
    assert "open_package_closed" in done.headers["location"]


def sealed_found_only(session: Session, spools: Spools) -> None:
    """Correct wrote one line: the sealed spool found again."""
    (line,) = reason_adjustments(session, over_recorded.REASON)
    assert (line.quantity, line.to_package_id, line.to_open_package_id) == (
        305,
        spools.spool.id,
        None,
    )


@pytest.mark.parametrize("close", [closed_by_confirm, closed_from_its_item])
def test_its_open_package_closed_outside_a_count_correct_writes_the_sealed_end(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    close: Callable[[TestClient, Spools, int], None],
) -> None:
    spools = Spools(session)
    check = opening_raised(registered_client, spools)
    open_id = opened_into(session, check).open_package_id
    assert open_id is not None
    close(admin_client, spools, open_id)
    on = session.get_one(OpenPackage, open_id).closed_at
    assert on is not None
    day = show_day(on, site_zone(session))
    page = admin_client.get(f"{PAGE}?item={spools.cable.id}").text
    assert f"Open spool closed {day}. Sealed spool still to put right." in text_of(page)
    assert "Correct sealed spool<span" in page
    (check,) = checks(session)
    with capture_logs() as logs:
        done = admin_client.post(
            f"{PAGE}/{check.id}/correct", data={"seen": over_recorded.seen(check)}
        )
    assert "over_recorded_corrected" in done.headers["location"]
    sealed_found_only(session, spools)
    (check,) = checks(session)
    assert (check.settled_how, check.left_count_id, check.arrived_count_id) == (
        "corrected",
        None,
        None,
    )
    assert {
        "event": "over recorded closed end skipped",
        "log_level": "info",
        "over_recorded_id": check.id,
        "open_package_id": open_id,
    } in logs
    # Sent again: settled, so nothing more is written.
    again = admin_client.post(
        f"{PAGE}/{check.id}/correct", data={"seen": over_recorded.seen(check)}
    )
    assert "over_recorded_unchanged" in again.headers["location"]
    sealed_found_only(session, spools)


def test_its_open_package_closed_on_a_count_whose_lines_were_withheld(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Every line of the count withheld as moved meanwhile, it writes no
    count transaction, so puts no end right; its close still closes the
    open package, and Correct then writes the sealed end alone."""
    spools = Spools(session)
    check = opening_raised(registered_client, spools)
    open_id = opened_into(session, check).open_package_id
    (other,) = session.scalars(
        select(OpenPackage).where(
            OpenPackage.item_id == spools.cable.id, OpenPackage.id != open_id
        )
    )
    shown = count_form_at(admin_client, spools.store.id)
    spools.move(Entry(spools.cable.id, Decimal(5), Form(open_package_id=other.id)))
    session.commit()
    count_at(
        admin_client,
        spools.store.id,
        {
            f"opened_{other.id}": "300",
            f"opened_{open_id}": "",
            f"state_{open_id}": "missing",
        },
        shown,
        status=409,
    )
    assert count_ids(session) == []
    (check,) = checks(session)
    assert (check.left_count_id, check.arrived_count_id) == (None, None)
    assert session.get_one(OpenPackage, open_id).closed_at is not None
    assert (
        "over_recorded_corrected" in correct(admin_client, session).headers["location"]
    )
    sealed_found_only(session, spools)


def test_a_cut_from_an_open_package_closed_since_is_still_refused(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Moves unchanged: only an opening skips an end closed since."""
    spools = Spools(session)
    made = spools.opened()
    session.commit()
    first = spools.place.batch(lines=[cut(made, "20")])
    recorded_then_changed(registered_client, first, lines=[cut(made, "15")])
    closed_from_its_item(admin_client, spools, made.id)
    page = admin_client.get(f"{PAGE}?item={spools.cable.id}").text
    assert " closed " not in text_of(page)
    response = correct(admin_client, session)
    assert response.status_code == 409
    assert reason_adjustments(session, over_recorded.REASON) == []
    (check,) = checks(session)
    assert check.settled_at is None


@pytest.mark.concurrency
def test_a_correct_waits_for_a_close_of_its_open_package_then_skips_it(
    own_committed: Committed,
) -> None:
    """The close holds the store, then the open package; Correct takes the
    store first, so waits, then finds it closed and writes the sealed end
    alone."""
    setup = own_committed.session()
    spools = Spools(setup)
    made = spools.opened()
    movement_id = setup.scalar(
        select(Movement.id).where(Movement.to_open_package_id == made.id)
    )
    assert movement_id is not None
    over_recorded.raise_checks(setup, {movement_id: Decimal(305)})
    admin = admin_id(setup)
    setup.commit()
    (check,) = checks(setup)
    first, second = own_committed.session(), own_committed.session()
    assert open_packages.close(first, made.id, admin, datetime.now(UTC)) is not None
    worker = own_committed.blocked(
        second, lambda: correct_check(second, check, over_recorded.seen(check), admin)
    )
    first.commit()
    finished = worker.finished()
    assert finished.error is None
    second.commit()
    (line,) = reason_adjustments(first, over_recorded.REASON)
    assert (line.to_package_id, line.to_open_package_id) == (spools.spool.id, None)


@pytest.mark.parametrize("close", [closed_by_confirm, closed_from_its_item])
def test_its_open_package_closed_then_its_sealed_spool_counted_closes_it(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    close: Callable[[TestClient, Spools, int], None],
) -> None:
    """Closed outside a count, the row offers 'Count <place>'; that count
    of the sealed spool puts the other end right, so it closes as counted:
    no Correct is left that would write nothing."""
    spools = Spools(session)
    check = opening_raised(registered_client, spools)
    open_id = opened_into(session, check).open_package_id
    assert open_id is not None
    close(admin_client, spools, open_id)
    link = f"{COUNTS}/{spools.store.id}?item={spools.cable.id}"
    assert (
        f'<a href="{link}">' in admin_client.get(f"{PAGE}?item={spools.cable.id}").text
    )
    form = count_form_at(admin_client, spools.store.id)
    with capture_logs() as logs:
        done = admin_client.post(link, data=form | {sealed_field(spools): "1"})
    assert done.status_code == 303
    (counted_at,) = count_ids(session)
    (check,) = checks(session)
    assert (check.settled_how, check.left_count_id, check.arrived_count_id) == (
        "counted",
        counted_at,
        None,
    )
    assert any(each["event"] == "over recorded counted" for each in logs)
    assert reason_adjustments(session, over_recorded.REASON) == []


def test_a_moves_open_package_closed_then_its_place_counted_puts_nothing_right(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Moves unchanged: only an opening's open package closed puts its end
    right."""
    spools = Spools(session)
    made = moved_whole(registered_client, spools)
    closed_from_its_item(admin_client, spools, made.id)
    count_at(admin_client, spools.van.id, {f"loose_{spools.cable.id}": "0"})
    assert count_ids(session) != []
    (check,) = checks(session)
    assert check.settled_at is None
    assert " closed " not in text_of(
        admin_client.get(f"{PAGE}?item={spools.cable.id}").text
    )


# An opening's sealed end counted, then its open package closed outside a
# count: closing closes it as counted, as the close wrote off what the open
# package showed (Ali, 2026-11-15, inventory-tng-die).


def closed_ticked(admin_client: TestClient, spools: Spools, open_id: int) -> None:
    """Reported used up, then ticked and confirmed with the others."""
    session = spools.session
    open_packages.report(
        session, [open_id], spools.place.volunteer.id, datetime.now(UTC)
    )
    session.commit()
    done = admin_client.post(
        "/admin/stock/used-up", data={"open_package": [str(open_id)], "do": "confirm"}
    )
    assert "used_up_confirmed" in done.headers["location"]


def closed_on_a_count(admin_client: TestClient, spools: Spools, open_id: int) -> None:
    """Said used up on a later count of its place: the close settles it
    before the count's own rows (over_recorded.counted), which then leave
    it be."""
    count_at(
        admin_client,
        spools.store.id,
        {f"opened_{open_id}": "", f"state_{open_id}": "used_up"},
    )


@pytest.mark.parametrize(
    "close", [closed_by_confirm, closed_ticked, closed_from_its_item, closed_on_a_count]
)
def test_its_sealed_spool_counted_then_its_open_package_closed_closes_it(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    close: Callable[[TestClient, Spools, int], None],
) -> None:
    spools = Spools(session)
    check = opening_raised(registered_client, spools)
    open_id = opened_into(session, check).open_package_id
    assert open_id is not None
    count_at(admin_client, spools.store.id, {sealed_field(spools): "1"})
    (counted_at,) = count_ids(session)
    with capture_logs() as logs:
        close(admin_client, spools, open_id)
    (check,) = checks(session)
    closed = session.get_one(OpenPackage, open_id)
    assert (check.settled_how, check.left_count_id, check.arrived_count_id) == (
        "counted",
        counted_at,
        None,
    )
    assert (check.settled_by_admin_id, check.settled_at) == (
        closed.closed_by_admin_id,
        closed.closed_at,
    )
    assert {
        "event": "over recorded closed",
        "log_level": "info",
        "over_recorded_id": check.id,
        "open_package_id": open_id,
        "admin_id": closed.closed_by_admin_id,
    } in logs
    assert reason_adjustments(session, over_recorded.REASON) == []
    page = admin_client.get(f"{PAGE}?item={spools.cable.id}").text
    assert f"{PAGE}/{check.id}/" not in page


def test_its_sealed_spool_counted_then_its_open_package_moved_whole_and_closed(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Closed where it went, not where it was opened: the close holds that
    place, not the opening's, and closes it all the same."""
    spools = Spools(session)
    check = opening_raised(registered_client, spools)
    open_id = opened_into(session, check).open_package_id
    assert open_id is not None
    count_at(admin_client, spools.store.id, {sealed_field(spools): "1"})
    made = session.get_one(OpenPackage, open_id)
    whole = cut(made, "305") | {"whole": True}
    moved = registered_client.post(URL, json=spools.place.batch(lines=[whole]))
    assert moved.status_code == 201
    session.expire_all()
    assert session.get_one(OpenPackage, open_id).location_id != spools.store.id
    closed_from_its_item(admin_client, spools, open_id)
    (check,) = checks(session)
    assert (check.settled_how, check.arrived_count_id) == ("counted", None)
    assert reason_adjustments(session, over_recorded.REASON) == []


def test_its_open_package_closed_with_neither_end_counted_leaves_it_waiting(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """The sealed end still to put right: Correct writes it (inventory-tng-nyl)."""
    spools = Spools(session)
    check = opening_raised(registered_client, spools)
    open_id = opened_into(session, check).open_package_id
    assert open_id is not None
    closed_from_its_item(admin_client, spools, open_id)
    (check,) = checks(session)
    assert check.settled_at is None


def test_a_cut_counted_where_it_left_then_its_open_package_closed_still_waits(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Moves unchanged: only an opening's row closes with its open package."""
    spools = Spools(session)
    made = spools.opened()
    session.commit()
    first = spools.place.batch(lines=[cut(made, "20")])
    recorded_then_changed(registered_client, first, lines=[cut(made, "15")])
    count_at(admin_client, spools.store.id, {f"opened_{made.id}": "290"})
    (check,) = checks(session)
    assert check.left_count_id is not None
    closed_from_its_item(admin_client, spools, made.id)
    (check,) = checks(session)
    assert check.settled_at is None


def sealed_counted_check(
    own_committed: Committed, *, whole: bool = False
) -> tuple[OverRecorded, OpenPackage, int]:
    """One sealed spool of the store's two opened, recorded with 305 more,
    its sealed end counted, then its open package moved whole to the van if
    `whole`, committed: the check, its open package and an
    administrator."""
    setup = own_committed.session()
    spools = Spools(setup)
    made = spools.opened()
    movement_id = setup.scalar(
        select(Movement.id).where(Movement.to_open_package_id == made.id)
    )
    assert movement_id is not None
    over_recorded.raise_checks(setup, {movement_id: Decimal(305)})
    admin = admin_id(setup)
    as_of = ledger.moment(setup, spools.store.id)
    count = ledger.record_count(
        setup,
        uuid.uuid4(),
        admin,
        spools.store.id,
        [],
        as_of,
        by_form=[(spools.cable.id, spools.sealed, Decimal(305))],
    )
    over_recorded.counted(
        setup, admin, spools.store.id, as_of, count.written, count.put_right
    )
    if whole:
        held = Form(open_package_id=made.id)
        spools.move(Entry(spools.cable.id, Decimal(305), held, held))
    setup.commit()
    (check,) = checks(setup)
    assert (check.left_count_id is not None, check.settled_at) == (True, None)
    return check, made, admin


@pytest.mark.concurrency
def test_a_correct_waits_for_a_close_that_closes_it_then_is_refused(
    own_committed: Committed,
) -> None:
    """The close holds the store, then the check; Correct takes the store
    first, so waits, then finds it settled."""
    check, made, admin = sealed_counted_check(own_committed)
    first, second = own_committed.session(), own_committed.session()
    done = open_packages.close(first, made.id, admin, datetime.now(UTC))
    assert done is not None
    assert done.over_recorded == 1
    worker = own_committed.blocked(
        second, lambda: correct_check(second, check, over_recorded.seen(check), admin)
    )
    first.commit()
    finished = worker.finished()
    second.rollback()
    assert isinstance(finished.error, RuleBroken)
    assert finished.error.rule == "over_recorded_unchanged"
    (check,) = checks(first)
    assert check.settled_how == "counted"
    assert reason_adjustments(first, over_recorded.REASON) == []


@pytest.mark.concurrency
def test_a_close_waits_for_a_correct_then_closes_only_its_package(
    own_committed: Committed,
) -> None:
    """Correct holds the store, then the check, and writes the open end;
    the close waits for the store, then finds the check settled."""
    check, made, admin = sealed_counted_check(own_committed)
    first, second = own_committed.session(), own_committed.session()
    correct_check(first, check, over_recorded.seen(check), admin)
    worker = own_committed.blocked(
        second, lambda: open_packages.close(second, made.id, admin, datetime.now(UTC))
    )
    first.commit()
    finished = worker.finished()
    second.commit()
    assert finished.error is None
    assert finished.result == open_packages.Closed(False, 0)
    (check,) = checks(first)
    assert check.settled_how == "corrected"
    (line,) = reason_adjustments(first, over_recorded.REASON)
    assert line.from_open_package_id == made.id


# Both ends right while it waits: an opening's sealed end counted, then its
# open package, moved whole, closed where it went while a count held the
# row, so the close skipped it (open_package_closed). The row says so and
# offers Close it, which settles it as counted, writing nothing; Correct
# does the same (Ali, 2026-11-15, inventory-tng-dro).


@pytest.mark.concurrency
def test_a_close_skips_one_a_count_holds_and_correct_then_closes_it_as_counted(
    own_committed: Committed,
) -> None:
    """The open package moved whole to the van: a count at the store holds
    the check (counted() locks those of the items it counts), so a close at
    the van leaves it out rather than wait (inventory-tng-die's residual).
    Both commit with both ends right; Correct writes nothing and settles it
    as counted."""
    check, made, admin = sealed_counted_check(own_committed, whole=True)
    first, second = own_committed.session(), own_committed.session()
    movement = first.get_one(Movement, check.movement_id)
    place = first.get_one(LedgerTransaction, movement.transaction_id).from_location_id
    assert place is not None
    sealed = Form(package_id=movement.from_package_id)
    as_of = ledger.moment(first, place)
    count = ledger.record_count(
        first,
        uuid.uuid4(),
        admin,
        place,
        [],
        as_of,
        by_form=[(made.item_id, sealed, Decimal(305))],
    )
    assert count.written
    assert (
        over_recorded.counted(
            first, admin, place, as_of, count.written, count.put_right
        )
        == 0
    )
    closed = open_packages.close(second, made.id, admin, datetime.now(UTC))
    assert closed == open_packages.Closed(False, 0)
    second.commit()
    first.commit()
    (waits,) = checks(first)
    assert waits.settled_at is None
    third = own_committed.session()
    ledger.lock_places(third, over_recorded.places_of(third, check.id))
    assert over_recorded.correct(third, check.id, over_recorded.seen(waits), admin) == (
        "counted",
        [],
    )
    third.commit()
    (check,) = checks(first)
    assert (check.settled_how, check.settled_by_admin_id) == ("counted", admin)
    assert (check.left_count_id, check.arrived_count_id) == (waits.left_count_id, None)
    assert reason_adjustments(first, over_recorded.REASON) == []


def both_right(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Spools, OverRecorded, str]:
    """An opening's sealed spool counted, then its open package moved whole
    to the van and closed there, the close skipping the check as if a count
    at the store held it: the spools, the check, and the page's words of
    it."""
    spools = Spools(session)
    check = opening_raised(registered_client, spools)
    open_id = opened_into(session, check).open_package_id
    assert open_id is not None
    count_at(admin_client, spools.store.id, {sealed_field(spools): "1"})
    (counted_at,) = count_ids(session)
    made = session.get_one(OpenPackage, open_id)
    whole = cut(made, "305") | {"whole": True}
    moved = registered_client.post(URL, json=spools.place.batch(lines=[whole]))
    assert moved.status_code == 201
    with monkeypatch.context() as patched:
        patched.setattr(over_recorded, "open_package_closed", lambda *_: 0)
        closed_from_its_item(admin_client, spools, open_id)
    session.expire_all()
    (check,) = checks(session)
    assert (check.settled_at, check.left_count_id) == (None, counted_at)
    zone = site_zone(session)
    sealed = show_day(session.get_one(LedgerTransaction, counted_at).created_at, zone)
    on = session.get_one(OpenPackage, open_id).closed_at
    assert on is not None
    words = (
        f"Both ends are right: sealed spool counted {sealed}, "
        f"open spool closed {show_day(on, zone)}."
    )
    return spools, check, words


def test_both_ends_right_it_says_so_and_offers_close_it(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spools, check, words = both_right(
        admin_client, registered_client, session, monkeypatch
    )
    page = admin_client.get(f"{PAGE}?item={spools.cable.id}").text
    assert words in text_of(page)
    assert f'action="{PAGE}/{check.id}/close?item={spools.cable.id}"' in page
    assert "Close it<span" in page
    assert f"{PAGE}/{check.id}/correct" not in page
    assert "still to put right" not in page


@pytest.mark.parametrize("do", ["close", "correct"])
def test_both_ends_right_close_it_or_correct_settles_it_as_counted(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    do: str,
) -> None:
    """Nothing left to write: settled as counted by the administrator, now,
    writing nothing, whether Close it or a Correct shown before."""
    _, check, _ = both_right(admin_client, registered_client, session, monkeypatch)
    ends = (check.left_count_id, check.arrived_count_id)
    written = session.scalar(select(func.count()).select_from(LedgerTransaction))
    began = session.scalar(select(func.now()))
    with capture_logs() as logs:
        done = admin_client.post(
            f"{PAGE}/{check.id}/{do}", data={"seen": over_recorded.seen(check)}
        )
    assert "over_recorded_counted" in done.headers["location"]
    session.expire_all()
    (check,) = checks(session)
    assert (check.settled_how, check.settled_by_admin_id) == (
        "counted",
        ada_id(session),
    )
    assert check.settled_at == began
    assert (check.left_count_id, check.arrived_count_id) == ends
    assert (
        session.scalar(select(func.count()).select_from(LedgerTransaction)) == written
    )
    assert {
        "event": "over recorded closed as right",
        "log_level": "info",
        "over_recorded_id": check.id,
        "admin_id": ada_id(session),
    } in logs
    page = admin_client.get(f"{PAGE}?notice=over_recorded_counted").text
    assert "Closed." in page
    # Sent again: settled, so refused.
    again = admin_client.post(
        f"{PAGE}/{check.id}/{do}", data={"seen": over_recorded.seen(check)}
    )
    assert "over_recorded_unchanged" in again.headers["location"]


def test_close_it_from_a_page_shown_before_is_refused(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shown before a batch said another amount: changed meanwhile."""
    _, check, _ = both_right(admin_client, registered_client, session, monkeypatch)
    seen = over_recorded.seen(check)
    over_recorded.raise_checks(session, {check.movement_id: Decimal(300)})
    session.commit()
    done = admin_client.post(f"{PAGE}/{check.id}/close", data={"seen": seen})
    assert "over_recorded_unchanged" in done.headers["location"]
    (check,) = checks(session)
    assert check.settled_at is None


def test_close_it_with_an_end_still_to_put_right_is_refused(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
) -> None:
    """Only its sealed end counted: Close it would leave the open end
    wrong, so nothing is settled; Correct still writes that end."""
    spools = Spools(session)
    opening_raised(registered_client, spools)
    count_at(admin_client, spools.store.id, {sealed_field(spools): "1"})
    (check,) = checks(session)
    page = admin_client.get(PAGE).text
    assert f"{PAGE}/{check.id}/close" not in page
    done = admin_client.post(
        f"{PAGE}/{check.id}/close", data={"seen": over_recorded.seen(check)}
    )
    assert "over_recorded_unchanged" in done.headers["location"]
    (check,) = checks(session)
    assert check.settled_at is None
    assert (
        "over_recorded_corrected" in correct(admin_client, session).headers["location"]
    )
