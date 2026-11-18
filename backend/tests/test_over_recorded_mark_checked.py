"""Lines recorded with more than was moved, on the administrators' page:
"Needs your action" lists them, Correct undoes one on each side, and Mark
checked asks why and writes nothing (inventory-tng-0gg)."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng.core.db import RuleBroken, attempt
from inventory_tng.models import (
    OVER_RECORDED_REASONS,
    ItemPackage,
    LedgerTransaction,
    Movement,
)
from inventory_tng.stock import forms as stock_forms
from inventory_tng.stock import over_recorded
from inventory_tng.stock import over_recorded_routes as over_recorded_page
from inventory_tng.web.forms import up_to

from helpers.concurrency import Committed
from helpers.markup import read, text
from helpers.over_recorded import (
    PAGE,
    checked_forms,
    checks,
    correct,
    from_emptied_store,
    mark_checked,
    moved_whole,
    new_admin_id,
    one_bag_waits,
    recorded_then_changed,
)
from helpers.stock import URL, Place, Spools, cut, write

# The administrators' page.


def test_needs_your_action_lists_it(
    admin_client: TestClient,
    registered_client: TestClient,
    place: Place,
    bags: ItemPackage,
) -> None:
    one_bag_waits(registered_client, place, bags)
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
    one_bag_waits(registered_client, place, bags)
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
    one_bag_waits(registered_client, place, bags)
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


def test_marking_one_checked_offers_each_reason(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
    bags: ItemPackage,
) -> None:
    """Ali, 2026-11-15 (inventory-tng-zgy)."""
    one_bag_waits(registered_client, place, bags)
    page = admin_client.get(PAGE).text
    shown = read(page)
    assert shown.all("legend", "Why it is right as it is")
    offered = {
        (radio.attrs["value"], label.text)
        for label in shown.all("label")
        for radio in label.all("input", type="radio", name="reason")
        if "required" in radio.attrs
    }
    reasons = over_recorded.CHECKED_REASONS.items()
    assert {(value, str(words)) for value, words in reasons} <= offered


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
    one_bag_waits(registered_client, place, bags)
    with capture_logs() as logs:
        response = mark_checked(admin_client, session, **form)
    assert response.status_code == 400
    (check,) = checks(session)
    # Exact markup on purpose: the id each reason's aria-describedby names.
    assert (
        f'<span class="problem" id="reason-{check.id}-problem">'
        f"{stock_forms.SAY_WHY}</span>" in response.text
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
    one_bag_waits(registered_client, place, bags)
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
        one_bag_waits(client, place, bags)
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
    # Exact markup on purpose: the id each reason's aria-describedby names.
    assert (
        f'<span class="problem" id="reason-{check.id}-problem">'
        f"{stock_forms.WORDS_FOR_ANOTHER}</span>" in response.text
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
    one_bag_waits(registered_client, place, bags)
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
    one_bag_waits(registered_client, place, bags)
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
    one_bag_waits(registered_client, place, bags)
    (check,) = checks(session)
    admin = new_admin_id(session)

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


def test_enter_in_the_words_marks_checked(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
    bags: ItemPackage,
) -> None:
    """Enter submits a form by its first button: Mark checked's form has
    no other, so Enter in the words never corrects it."""
    one_bag_waits(registered_client, place, bags)
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
    one_bag_waits(registered_client, place, bags)
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
    assert str(stock_forms.SAY_WHY) in response.text
    assert 'role="alert"' not in response.text


def test_one_said_again_since_it_was_shown_is_not_settled(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    place: Place,
    bags: ItemPackage,
) -> None:
    one_bag_waits(registered_client, place, bags)
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
    one_bag_waits(registered_client, place, bags)
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
    admin = new_admin_id(setup)
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
