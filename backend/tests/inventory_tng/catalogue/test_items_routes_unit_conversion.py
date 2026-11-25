"""An item's unit changed between m and ft after stock has moved, converting
what is recorded once the administrator answers Yes to "Were the recorded
amounts really in m?" (Ali, 2026-11-24, inventory-tng-y50b): its stock,
Reorder at, what is on order, its vendor offers and what volunteers said is
on its open packages. Its old ledger lines and its packages keep their own
unit. No converts nothing; each and a length never convert."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.catalogue import unit_changes
from inventory_tng.catalogue.items_routes import RECORDED, RECORDED_IN
from inventory_tng.catalogue.paths import ITEMS
from inventory_tng.labels import labels
from inventory_tng.models.catalogue import Item, ItemPackage, VendorOffer
from inventory_tng.models.stock import Movement
from inventory_tng.scan.batch import BatchRefused, KeyReused, record
from inventory_tng.scan.batch_schemas import Batch
from inventory_tng.scan.catalogue_api import catalogue
from inventory_tng.stock import moments, open_packages, queries
from inventory_tng.stock.ledger import Entry
from inventory_tng.stock.moments import expected
from inventory_tng.stock.paths import LENGTHS
from inventory_tng.web.forms import RULES

from helpers.items import NOW, shown_item_form
from helpers.markup import button, problem, read, words_on
from helpers.rows import add, shown
from helpers.stock import (
    Place,
    Spools,
    count_at,
    counts_written,
    length,
    opened_line,
    opening,
    raise_check,
    received,
    report,
    sealed_line,
    write,
)

QUESTION = "Were the recorded amounts really in m?"


def _yes(item: Item, **fields: str) -> dict[str, str]:
    """The item's form answered Yes to the question about its unit now."""
    return shown_item_form(item, **fields) | {RECORDED: "yes", RECORDED_IN: item.unit}


def _shelf_a(session: Session) -> Place:
    """The bead's example: a cable in m, 305 m of it on Shelf A (the
    place's store), Reorder at 100 m, 50 m on order."""
    place = Place(session)
    received(
        session, place, [{"item_id": place.cable.id, "quantity": "305", "unit": "m"}]
    )
    place.cable.minimum, place.cable.on_order = Decimal(100), Decimal(50)
    session.flush()
    return place


def test_m_to_ft_asks_then_converts_every_amount(
    admin_client: TestClient, session: Session
) -> None:
    """305 m on Shelf A reads 1000.656 ft (305 / 0.3048, kept to
    thousandths); Reorder at and on order are converted, a vendor offer's
    units per order too; the receipt's line still reads 305 m."""
    place = _shelf_a(session)
    cable = place.cable
    offer = add(
        session,
        VendorOffer(item_id=cable.id, url="https://example.org", units_per_order=305),
    )
    # One that says nothing of how many: only its unit follows.
    unsaid = add(session, VendorOffer(item_id=cable.id, url="https://example.net"))
    asked = admin_client.post(
        f"{ITEMS}/{cable.id}", data=shown_item_form(cable, unit="ft")
    )
    assert asked.status_code == 200
    page = read(asked.text)
    assert QUESTION in words_on(page)
    assert button(page, "Yes").attrs["value"] == "yes"
    assert button(page, "No").attrs["value"] == "no"
    # Enter in a field presses the form's first button: No, converting nothing.
    (asking,) = [f for f in page.all("form") if f.all("div", id="unit-question")]
    first = asking.all("button")[0]
    assert first.attrs["value"] == "no"
    assert cable.unit == "m"
    done = admin_client.post(
        f"{ITEMS}/{cable.id}", data=_yes(cable, unit="ft", minimum="100")
    )
    assert done.status_code == 303
    session.refresh(cable)
    assert (cable.unit, cable.minimum, cable.on_order) == (
        "ft",
        Decimal("328.084"),
        Decimal("164.042"),
    )
    assert expected(session, place.store.id) == {cable.id: Decimal("1000.656")}
    session.refresh(offer)
    assert (offer.units_per_order, offer.unit) == (Decimal("1000.656"), "ft")
    session.refresh(unsaid)
    assert (unsaid.units_per_order, unsaid.unit) == (None, "ft")
    lines = session.execute(
        select(Movement.quantity, Movement.unit).where(Movement.item_id == cable.id)
    ).all()
    assert lines == [(Decimal(305), "m")]


def test_a_reorder_at_typed_with_the_change_is_converted_from_the_old_unit(
    admin_client: TestClient, session: Session
) -> None:
    """The form says Reorder at is in m, its unit when the form was shown."""
    cable = _shelf_a(session).cable
    admin_client.post(f"{ITEMS}/{cable.id}", data=_yes(cable, unit="ft", minimum="10"))
    session.refresh(cable)
    assert cable.minimum == Decimal("32.808")


def test_ft_to_m_converts_back(admin_client: TestClient, session: Session) -> None:
    """Either way between lengths: 1000 ft is 304.8 m exactly."""
    place = Place(session, cable_unit="ft")
    received(
        session, place, [{"item_id": place.cable.id, "quantity": "1000", "unit": "ft"}]
    )
    admin_client.post(f"{ITEMS}/{place.cable.id}", data=_yes(place.cable, unit="m"))
    assert expected(session, place.store.id) == {place.cable.id: Decimal("304.8")}


def test_no_converts_nothing_and_says_what_to_do(
    admin_client: TestClient, session: Session
) -> None:
    place = _shelf_a(session)
    cable = place.cable
    answered = shown_item_form(cable, unit="ft") | {RECORDED: "no", RECORDED_IN: "m"}
    declined = admin_client.post(f"{ITEMS}/{cable.id}", data=answered)
    assert declined.status_code == 400
    assert problem(declined.text, "unit") == (
        "Nothing was converted. If they were really in ft, count its stock in m "
        "first, then change its unit."
    )
    assert QUESTION not in words_on(declined.text)
    session.refresh(cable)
    assert (cable.unit, cable.minimum, cable.on_order) == ("m", 100, 50)
    assert expected(session, place.store.id) == {cable.id: Decimal(305)}


def test_an_answer_about_another_unit_is_asked_again(
    admin_client: TestClient, session: Session
) -> None:
    """Yes, the amounts were in ft, said of a cable in m since: asked again
    in m, nothing converted."""
    cable = _shelf_a(session).cable
    stale = shown_item_form(cable, unit="ft") | {RECORDED: "yes", RECORDED_IN: "ft"}
    asked = admin_client.post(f"{ITEMS}/{cable.id}", data=stale)
    assert QUESTION in words_on(asked.text)
    assert cable.unit == "m"


def test_each_and_a_length_never_convert(
    admin_client: TestClient, session: Session
) -> None:
    """A radio counted each, with stock, may not become m; a cable with
    stock in m may not become each. Neither form offers it, nor asks."""
    place = _shelf_a(session)
    received(session, place)
    for item, unit, rule in (
        (place.item, "m", "item_unit_fixed"),
        (place.cable, "each", "item_unit_fixed"),
    ):
        refused = admin_client.post(f"{ITEMS}/{item.id}", data=_yes(item, unit=unit))
        assert refused.status_code == 400
        assert problem(refused.text, "unit") == str(RULES[rule])
    assert (place.item.unit, place.cable.unit) == ("each", "m")


def test_refused_while_a_line_recorded_with_more_than_moved_waits(
    admin_client: TestClient, session: Session
) -> None:
    """Its check is undone in the line's unit (stock/over_recorded.py), so
    it is settled first, as said before the question is asked."""
    place = _shelf_a(session)
    moved = write(
        session,
        place,
        lines=[{"item_id": place.cable.id, "quantity": "5", "unit": "m"}],
    )
    raise_check(session, moved)
    for form in (shown_item_form(place.cable, unit="ft"), _yes(place.cable, unit="ft")):
        refused = admin_client.post(f"{ITEMS}/{place.cable.id}", data=form)
        assert refused.status_code == 400
        assert QUESTION not in words_on(refused.text)
        assert problem(refused.text, "unit") == str(
            RULES[unit_changes.UNIT_CHECKS_WAITING]
        )
    assert place.cable.unit == "m"


def test_refused_when_an_amount_would_not_keep_in_the_new_unit(
    admin_client: TestClient, session: Session
) -> None:
    """A Reorder at of 400,000,000 m is more than an amount holds in ft;
    a too-short length of 0.001 ft, or a package of 0.001 ft, is less than
    one holds in m. None converts, and nothing is saved."""
    cable = _shelf_a(session).cable
    huge = _yes(cable, unit="ft", minimum="400000000")
    refused = admin_client.post(f"{ITEMS}/{cable.id}", data=huge)
    assert refused.status_code == 400
    assert problem(refused.text, "unit") == str(RULES[unit_changes.UNIT_OUT_OF_RANGE])
    session.refresh(cable)
    assert (cable.unit, cable.minimum) == ("m", 100)
    place = Place(session, cable_unit="ft")
    reel = ItemPackage(
        item_id=place.cable.id,
        name="Reel",
        size=Decimal(10),
        short_below=Decimal("0.001"),
    )
    add(session, reel)
    received(
        session, place, [{"item_id": place.cable.id, "quantity": "10", "unit": "ft"}]
    )
    for crumb in (None, Decimal("0.001")):
        if crumb is not None:
            reel.short_below = None
            add(session, ItemPackage(item_id=place.cable.id, name="Crumb", size=crumb))
        tiny = admin_client.post(
            f"{ITEMS}/{place.cable.id}", data=_yes(place.cable, unit="m")
        )
        assert problem(tiny.text, "unit") == str(RULES[unit_changes.UNIT_OUT_OF_RANGE])
        assert place.cable.unit == "ft"


def test_a_length_check_shown_before_the_change_is_not_set(
    admin_client: TestClient, session: Session
) -> None:
    """Its page said 300 m; set after the cable is in ft, 300 would be read
    as ft. So it is refused as changed since, nothing counted, and the
    page shows it again in ft."""
    spools = Spools(session)
    made = spools.opened()
    record(
        session,
        Batch.model_validate(report(spools, length(made, "300"))),
        device_id=None,
    )
    session.flush()
    seen = open_packages.check_seen(made, "m")
    cable = spools.cable
    followed = _yes(cable, unit="ft", track_opened="on")
    assert admin_client.post(f"{ITEMS}/{cable.id}", data=followed).status_code == 303
    before = counts_written(session)
    form = {"left": "300", "seen": seen, "showed": "305", "as_of": "1"}
    admin_client.post(f"{LENGTHS}/{made.id}/set", data=form)
    assert counts_written(session) == before
    session.refresh(made)
    assert made.length_reported_at is not None
    ticked = {"open_package": [f"{made.id} {seen}"], "do": "set"}
    admin_client.post(LENGTHS, data=ticked)
    assert counts_written(session) == before


def test_packages_keep_their_size_and_move_whole_in_the_new_unit(
    admin_client: TestClient, session: Session
) -> None:
    """Two spools of 305 m received in m; once the cable is in ft each is
    1000.656 ft, as the scan page is sent it, and one leaves whole. What a
    volunteer said is on an open one is converted with its unit."""
    spools = Spools(session)
    made = spools.opened()
    record(
        session,
        Batch.model_validate(report(spools, length(made, "300"))),
        device_id=None,
    )
    cable = spools.cable
    # Still followed once opened: unticked, its open one would be made loose.
    followed = _yes(cable, unit="ft", track_opened="on")
    assert admin_client.post(f"{ITEMS}/{cable.id}", data=followed).status_code == 303
    session.refresh(spools.spool)
    session.refresh(made)
    assert (spools.spool.size, spools.spool.unit) == (Decimal(305), "m")
    assert (made.length_reported, made.length_reported_unit) == (
        Decimal("984.252"),
        "ft",
    )
    sent = catalogue(session)
    (entry,) = [each for each in sent.items if each.id == cable.id]
    (package,) = entry.packages
    assert package.size == Decimal("1000.656")
    assert [(each.location_id, each.count) for each in package.sealed] == [
        (spools.store.id, 1)
    ]
    spools.move(Entry(cable.id, Decimal("1000.656"), spools.sealed, spools.sealed))
    assert queries.item_total(session, cable.id) == Decimal("2001.312")
    assert expected(session, spools.store.id) == {cable.id: Decimal("1000.656")}


def test_a_form_shown_before_the_change_is_refused(
    admin_client: TestClient, session: Session
) -> None:
    """The item's version moved: an older form, answered Yes, is not saved
    over it."""
    cable = _shelf_a(session).cable
    before = _yes(cable, unit="ft")
    cable.notes = "Changed meanwhile"
    session.flush()
    assert shown(cable) != before["version"]
    refused = admin_client.post(f"{ITEMS}/{cable.id}", data=before)
    assert refused.status_code != 303
    assert cable.unit == "m"


def test_a_scan_batch_opens_a_spool_in_the_new_unit_and_one_drawn_before_is_refused(
    admin_client: TestClient, session: Session
) -> None:
    """Once the cable is in ft, a spool opened from the scan page is 1000.656
    ft, as the catalogue sends it, and makes an open package of all of it.
    A batch drawn in m, at 305, is refused as shown otherwise, and told the
    spool's size and unit now, so the page asks again."""
    spools = Spools(session)
    cable = spools.cable
    admin_client.post(
        f"{ITEMS}/{cable.id}", data=_yes(cable, unit="ft", track_opened="on")
    )
    drawn_before = opening(spools, opened_line(spools, unit="m"))
    with pytest.raises(BatchRefused) as refused:
        record(session, Batch.model_validate(drawn_before), device_id=None)
    assert {kind for _, kind in refused.value.problems} == {
        "unit_changed",
        "package_resized",
    }
    now = opened_line(spools, unit="ft", quantity="1000.656", package_size="1000.656")
    written = record(
        session, Batch.model_validate(opening(spools, now)), device_id=None
    ).transaction
    (made,) = session.scalars(
        select(Movement.to_open_package_id).where(Movement.transaction_id == written.id)
    )
    assert made is not None
    latest = queries.latest_transaction_id(session)
    assert moments.left_at(session, made, latest) == Decimal("1000.656")


def test_a_length_reported_on_a_page_drawn_before_the_change_is_refused(
    admin_client: TestClient, session: Session
) -> None:
    """A report of 50 m on an open spool, from a page drawn while the cable
    was in m, sent once it is in ft, is refused as shown otherwise, never
    kept as 50 ft (inventory-tng-fs50). One in ft is kept in ft."""
    spools = Spools(session)
    made = spools.opened()
    cable = spools.cable
    admin_client.post(
        f"{ITEMS}/{cable.id}", data=_yes(cable, unit="ft", track_opened="on")
    )
    drawn_before = report(spools, length(made, "50", "m"))
    with pytest.raises(BatchRefused) as refused:
        record(session, Batch.model_validate(drawn_before), device_id=None)
    assert refused.value.problems == [(("lengths", 0, "unit"), "unit_changed")]
    session.refresh(made)
    assert made.length_reported is None
    record(
        session,
        Batch.model_validate(report(spools, length(made, "50", "ft"))),
        device_id=None,
    )
    assert (made.length_reported, made.length_reported_unit) == (Decimal(50), "ft")


def test_a_count_after_the_change_counts_spools_in_the_new_unit(
    admin_client: TestClient, session: Session
) -> None:
    """Two spools received in m, counted as one once the cable is in ft:
    one of 1000.656 ft is written off, and the store holds the other."""
    spools = Spools(session)
    cable = spools.cable
    admin_client.post(
        f"{ITEMS}/{cable.id}", data=_yes(cable, unit="ft", track_opened="on")
    )
    count_at(
        admin_client, spools.store.id, {f"sealed_{cable.id}_{spools.spool.id}": "1"}
    )
    assert expected(session, spools.store.id) == {cable.id: Decimal("1000.656")}
    (count,) = counts_written(session)
    lines = session.execute(
        select(Movement.quantity, Movement.unit).where(
            Movement.transaction_id == count.id
        )
    ).all()
    assert lines == [(Decimal("1000.656"), "ft")]


def test_a_batch_sent_again_is_answered_in_the_unit_it_was_recorded_in(
    admin_client: TestClient, session: Session
) -> None:
    """Two spools moved in m, the cable in ft since: the batch sent again
    with one is told what was recorded, spools of 305 m, not of their size
    in ft now."""
    spools = Spools(session)
    batch = spools.place.batch(lines=[sealed_line(spools, "610")])
    record(session, Batch.model_validate(batch), device_id=None)
    cable = spools.cable
    admin_client.post(
        f"{ITEMS}/{cable.id}", data=_yes(cable, unit="ft", track_opened="on")
    )
    less = batch | {"lines": [sealed_line(spools, "305") | {"unit": "m"}]}
    with pytest.raises(KeyReused) as reused:
        record(session, Batch.model_validate(less), device_id=None)
    assert reused.value.recorded is not None
    (line,) = reused.value.recorded.lines
    assert (line.quantity, line.unit, line.package_size) == (
        Decimal(610),
        "m",
        Decimal(305),
    )


def test_a_batch_sent_again_in_a_unit_its_package_is_never_in_is_another(
    session: Session,
) -> None:
    """An opening sent again under its key, its line in each, is told it
    is not the one recorded (KeyReused), not failed reading a spool's size
    in each."""
    spools = Spools(session)
    sent = opening(spools, sealed_line(spools, "305"))
    record(session, Batch.model_validate(sent), device_id=None)
    each = sealed_line(spools, "305") | {"unit": "each"}
    again = opening(spools, each) | {"key": sent["key"]}
    with pytest.raises(KeyReused):
        record(session, Batch.model_validate(again), device_id=None)


def test_no_for_a_labelled_item_says_to_revoke_its_labels(
    admin_client: TestClient, session: Session
) -> None:
    """Nothing moved, only labelled: what was meant in ft from the start
    is put right by revoking its labels, then changing its unit, which
    converts nothing. Answered on a page drawn before they were revoked,
    No is shown the form again, as nothing is asked any more."""
    place = Place(session)
    label = labels.make_for_item(session, place.cable.id)
    assert label is not None
    answered = shown_item_form(place.cable, unit="ft") | {
        RECORDED: "no",
        RECORDED_IN: "m",
    }
    declined = admin_client.post(f"{ITEMS}/{place.cable.id}", data=answered)
    assert problem(declined.text, "unit") == (
        "Nothing was converted. If they were really in ft, revoke its labels "
        "first, then change its unit."
    )
    assert labels.revoke(session, label.id, NOW)
    again = admin_client.post(f"{ITEMS}/{place.cable.id}", data=answered)
    assert again.status_code == 200
    assert "Nothing was converted" not in words_on(again.text)
    assert place.cable.unit == "m"
