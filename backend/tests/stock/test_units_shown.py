"""Work saved as it was shown (stock/units_shown.py; Ali, 2026-11-23,
inventory-tng-57u8): a scan page's batch line, a count form and an
adjustment say the unit and package sizes they showed; one changed since
is refused and asked again in the new ones, never converted."""

import uuid
from decimal import Decimal

from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng.models.catalogue import ItemPackage
from inventory_tng.stock.count_forms import changed_words
from inventory_tng.stock.forms import now_counted_in, now_holds, shown_field, shown_in
from inventory_tng.stock.moments import expected
from inventory_tng.stock.paths import ADJUSTMENTS, COUNTS
from inventory_tng.stock.units_shown import Now, Shown

from helpers.markup import box_values, hidden_fields, text
from helpers.over_recorded import adjustments, checks, counted, moved_whole
from helpers.package_forms import quantities_by_form
from helpers.rows import add, tag
from helpers.stock import (
    URL,
    Place,
    Spools,
    adjustment_form,
    count_form_at,
    counts_written,
)


def box_of_ten(session: Session, place: Place) -> ItemPackage:
    """A box of ten radios, never moved, opened or labelled, so its size
    may change."""
    return add(
        session, ItemPackage(item_id=place.item.id, name=f"Box {tag()}", size=10)
    )


def resized_to_twelve(session: Session, box: ItemPackage) -> None:
    box.size = Decimal(12)
    session.flush()


def to_feet(session: Session, place: Place) -> None:
    """The cable, never moved, switched from m to ft."""
    place.cable.unit = "ft"
    session.flush()


def problems_of(response: Response) -> list[tuple[list[object], str]]:
    """A 422's problems: where in the batch, and what."""
    detail = response.json()["detail"]
    return [(problem["loc"][1:], problem["type"]) for problem in detail]


# The scan page's batches (POST /api/transactions).


def test_a_batch_line_shown_in_m_saved_after_a_switch_to_ft_is_refused(
    registered_client: TestClient, session: Session
) -> None:
    """A queued receipt of 50 m, sent once the cable counts in ft: refused,
    for the outbox to ask again, never 50 ft."""
    place = Place(session)
    line = {"item_id": place.cable.id, "quantity": "50", "unit": "m"}
    receipt = place.batch(
        kind="receipt",
        from_location_id=None,
        to_location_id=place.store.id,
        lines=[line],
    )
    to_feet(session, place)
    response = registered_client.post(URL, json=receipt)
    assert response.status_code == 422
    assert problems_of(response) == [(["lines", 0, "unit"], "unit_changed")]
    assert expected(session, place.store.id) == {}
    # Shown in ft, it is saved; a line saying no unit, from a build from
    # before it was sent, is not compared.
    for sent in ({**line, "unit": "ft"}, {"item_id": place.cable.id, "quantity": "50"}):
        again = receipt | {"key": str(uuid.uuid4()), "lines": [sent]}
        response = registered_client.post(URL, json=again)
        assert response.status_code == 201


def test_a_batch_line_shown_whole_in_each_is_read_in_each(
    registered_client: TestClient, session: Session
) -> None:
    """A line of 1.5 m sent as each: refused as changed, not as a part of
    one, as it was typed in m."""
    place = Place(session)
    line = {"item_id": place.cable.id, "quantity": "1.5", "unit": "m"}
    receipt = place.batch(
        kind="receipt",
        from_location_id=None,
        to_location_id=place.store.id,
        lines=[line],
    )
    place.cable.unit = "each"
    session.flush()
    response = registered_client.post(URL, json=receipt)
    assert problems_of(response) == [(["lines", 0, "unit"], "unit_changed")]
    # Typed as each, now in m: a part of one is still said.
    place.cable.unit = "m"
    session.flush()
    as_each = receipt | {"lines": [line | {"unit": "each"}]}
    response = registered_client.post(URL, json=as_each)
    assert problems_of(response) == [
        (["lines", 0, "unit"], "unit_changed"),
        (["lines", 0, "quantity"], "not_whole"),
    ]


def test_a_batch_line_of_a_box_of_ten_saved_once_it_holds_twelve_is_refused(
    registered_client: TestClient, session: Session
) -> None:
    place = Place(session)
    box = box_of_ten(session, place)
    line = {
        "item_id": place.item.id,
        "quantity": "30",
        "package_id": box.id,
        "package_size": "10",
        "unit": "each",
    }
    receipt = place.batch(
        kind="receipt",
        from_location_id=None,
        to_location_id=place.store.id,
        lines=[line],
    )
    resized_to_twelve(session, box)
    response = registered_client.post(URL, json=receipt)
    assert problems_of(response) == [(["lines", 0, "package_size"], "package_resized")]
    assert quantities_by_form(session, place.item) == {}


# A count form.


def test_what_a_form_showed_is_read_leaving_out_what_is_not_an_id_and_a_value() -> None:
    shown = shown_in({"units": "5:m x:ft 6: 7:each", "sizes": "9:10.000 10:abc"})
    assert shown == Shown({5: "m", 7: "each"}, {9: Decimal(10)})
    assert shown_field({7: "each", 5: "m"}) == "5:m 7:each"


def test_every_box_of_an_item_or_package_changed_says_what_it_is_now(
    session: Session,
) -> None:
    """Each kind of box on a count form, by the item it counts: totals,
    loose, sealed, open packages, something else here, an open package
    nobody recorded, and what arrived from an area gone."""
    spools = Spools(session)
    made = spools.opened()
    cable, spool, item = spools.cable.id, spools.spool.id, spools.place.item.id
    store = spools.store.id
    form = {
        "key": "not an amount",
        f"counted_{item}": "1",
        f"loose_{cable}": "2",
        f"sealed_{cable}_{spool}": "1",
        f"opened_{made.id}": "5",
        "extra_item_id": str(cable),
        "extra_quantity": "3",
        "found_package_id": str(spool),
        "found_left": "4",
        f"arrived_{spools.van.id}_loose_{cable}": "1",
    }
    words = changed_words(session, form, store, [store], Now({cable: "ft"}, {}))
    assert words == dict.fromkeys(
        [
            f"loose_{cable}",
            f"sealed_{cable}_{spool}",
            f"opened_{made.id}",
            "extra_quantity",
            "found_left",
            f"arrived_{spools.van.id}_loose_{cable}",
        ],
        now_counted_in("ft"),
    )


def test_a_count_shows_the_units_and_sizes_it_shows(
    admin_client: TestClient, session: Session
) -> None:
    place = Place(session)
    fields = dict(hidden_fields(admin_client.get(f"{COUNTS}/{place.store.id}").text))
    units = dict(each.split(":") for each in fields["units"].split())
    assert (units[str(place.item.id)], units[str(place.cable.id)]) == ("each", "m")


def test_a_count_of_50_m_saved_after_a_switch_to_ft_is_asked_again(
    admin_client: TestClient, session: Session
) -> None:
    """Something else here, 50 m of a cable never moved: once it counts in
    ft, the form is shown again, in ft, saying so beside the box, nothing
    written."""
    place = Place(session)
    form = count_form_at(admin_client, place.store.id) | {
        "extra_item_id": str(place.cable.id),
        "extra_quantity": "50",
    }
    to_feet(session, place)
    response = admin_client.post(f"{COUNTS}/{place.store.id}", data=form)
    assert response.status_code == 400
    assert now_counted_in("ft") in text(response)
    assert box_values(response.text, "extra_quantity") == ["50"]
    assert f"{place.cable.id}:ft" in dict(hidden_fields(response.text))["units"]
    assert counts_written(session) == []
    # Sent again from the form shown, it is saved, in ft.
    again = count_form_at(admin_client, place.store.id) | {
        "extra_item_id": str(place.cable.id),
        "extra_quantity": "50",
    }
    sent = admin_client.post(
        f"{COUNTS}/{place.store.id}", data=again, follow_redirects=False
    )
    assert sent.status_code == 303
    assert expected(session, place.store.id) == {place.cable.id: Decimal(50)}


def test_a_count_of_a_box_of_ten_saved_once_it_holds_twelve_is_asked_again(
    admin_client: TestClient, session: Session
) -> None:
    """Three boxes counted as of ten: once the box holds twelve, refused,
    not saved as 36. A form shows a row of sealed boxes only of those held
    somewhere, whose size is fixed, so this one is sent from elsewhere, and
    its words have no box to stand beside: logged by its id."""
    place = Place(session)
    box = box_of_ten(session, place)
    field = f"sealed_{place.item.id}_{box.id}"
    form = count_form_at(admin_client, place.store.id) | {
        "sizes": f"{box.id}:10",
        field: "3",
    }
    resized_to_twelve(session, box)
    with capture_logs() as logs:
        response = admin_client.post(f"{COUNTS}/{place.store.id}", data=form)
    assert response.status_code == 400
    assert counts_written(session) == []
    (refused,) = [
        line for line in logs if line["event"] == "save refused: shown otherwise"
    ]
    assert (refused["save"], refused["item_package_ids"]) == ("count", [box.id])
    assert changed_words(
        session,
        form,
        place.store.id,
        [place.store.id],
        Now({}, {box.id: (Decimal(12), "each")}),
    ) == {field: now_holds(Decimal(12), "each")}


# An adjustment, and an open package's count confirmed.


def test_an_adjustment_of_50_m_saved_after_a_switch_to_ft_is_asked_again(
    admin_client: TestClient, session: Session
) -> None:
    place = Place(session)
    form = adjustment_form(
        session, place.store, place.cable, amount="50", direction="found"
    )
    assert form["units"] == f"{place.cable.id}:m"
    to_feet(session, place)
    response = admin_client.post(ADJUSTMENTS, data=form)
    assert response.status_code == 400
    assert now_counted_in("ft") in text(response)
    assert counts_written(session) == []
    shown = dict(hidden_fields(admin_client.get(COUNTS).text))
    assert f"{place.cable.id}:ft" in shown["units"]
    saved = admin_client.post(
        ADJUSTMENTS,
        data=form | {"units": f"{place.cable.id}:ft"},
        follow_redirects=False,
    )
    assert saved.status_code == 303


def test_a_confirmed_count_shown_in_another_unit_is_asked_again(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Never so on the page, as a line moved fixes its item's unit: a form
    sent from elsewhere."""
    spools = Spools(session)
    moved_whole(registered_client, spools)
    _, response = counted(admin_client, session, "300", unit="ft")
    assert response.status_code == 400
    assert now_counted_in("m") in text(response)
    assert checks(session)[0].settled_at is None
    assert adjustments(session) == []
