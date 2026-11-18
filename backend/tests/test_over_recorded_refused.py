"""A correction refused by a rule: it says why, offers to bring a retired
place back, and a Mark checked refused from a refusal's alert draws the
alert again (inventory-tng-axb, inventory-tng-e5b)."""

from collections.abc import Callable
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng import locations, over_recorded
from inventory_tng.admin import over_recorded as over_recorded_page
from inventory_tng.core.db import RuleBroken
from inventory_tng.ledger import Entry, Form
from inventory_tng.models import Admin, CatalogueHistory, Location, OverRecorded
from inventory_tng.stock import forms as stock_forms
from inventory_tng.web.forms import RULES, up_to

from helpers.markup import read, text
from helpers.over_recorded import (
    PAGE,
    adjustments,
    checked_forms,
    checks,
    correct,
    counted,
    from_emptied_store,
    mark_checked,
    moved_whole,
    recorded_then_changed,
)
from helpers.rows import add, tag
from helpers.stock import Place, Spools, received, retire, write

# Refused by a rule (inventory-tng-axb).


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
    assert len(read(response.text).all("legend", "Why it is right as it is")) == 2


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
    # Exact markup on purpose: the id each reason's aria-describedby names.
    return f'<span class="problem" id="reason-{check.id}-problem">'


@pytest.mark.parametrize(
    ("form", "problem"),
    [
        ({}, stock_forms.SAY_WHY),
        ({"reason": "both", "words": "typed"}, stock_forms.WORDS_FOR_ANOTHER),
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
    assert str(stock_forms.SAY_WHY) in page


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
    assert str(stock_forms.SAY_WHY) in response.text
