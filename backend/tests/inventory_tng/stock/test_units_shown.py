"""Work saved as it was shown (stock/units_shown.py; Ali, 2026-11-23,
inventory-tng-57u8): a scan page's batch line, a count form and an
adjustment say the unit and package sizes they showed; one changed since
is refused and asked again in the new ones, never converted."""

import uuid
from collections.abc import Callable
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng.catalogue import items
from inventory_tng.core.db_locking import UNGUARDED_INFO
from inventory_tng.core.phrases import Words
from inventory_tng.labels import labels
from inventory_tng.models.catalogue import Item, ItemPackage
from inventory_tng.stock import count_routes, counts_routes
from inventory_tng.stock.count_forms import (
    NO_SUCH_OPEN_PACKAGE,
    NO_SUCH_PACKAGE,
    ON_THE_FORM_TWICE,
    TYPE_IT_HERE,
    WHOLE_PACKAGES,
    changed_words,
)
from inventory_tng.stock.forms import (
    HOW_MUCH,
    now_counted_in,
    now_holds,
    shown_field,
    shown_in,
)
from inventory_tng.stock.moments import expected
from inventory_tng.stock.paths import ADJUSTMENTS, COUNTS
from inventory_tng.stock.units_shown import Now, Shown
from inventory_tng.web.forms import (
    ABOVE_ZERO,
    QUANTITY_MAX,
    RULES,
    TOO_BIG,
    WHOLE_UNITS,
    ZERO_OR_MORE,
    type_a_number,
    use_separator,
)

from helpers.items import NOW
from helpers.markup import box_values, hidden_fields, problem, read, text
from helpers.over_recorded import adjustments, checks, counted, moved_whole
from helpers.package_forms import quantities_by_form
from helpers.rows import add, item_fields, setting_up, tag
from helpers.stock import (
    URL,
    Place,
    Spools,
    adjustment_form,
    count_form_at,
    counts_written,
    received,
    retire,
    to_feet,
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
    to_feet(session, place.cable)
    response = registered_client.post(URL, json=receipt)
    assert response.status_code == 422
    assert problems_of(response) == [(["lines", 0, "unit"], "unit_changed")]
    assert expected(session, place.store.id) == {}
    # Saying no unit, it is refused as the schema says; shown in ft, saved.
    unsaid = {"item_id": place.cable.id, "quantity": "50"}
    response = registered_client.post(URL, json=receipt | {"lines": [unsaid]})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "lines", 0, "unit"]
    again = receipt | {"key": str(uuid.uuid4()), "lines": [{**line, "unit": "ft"}]}
    assert registered_client.post(URL, json=again).status_code == 201


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


@pytest.mark.parametrize("resized", [False, True])
def test_a_line_scanned_from_a_label_revoked_since_is_refused_once_corrected(
    registered_client: TestClient, session: Session, resized: bool
) -> None:
    """A spool's label scanned into the outbox, then revoked so its unit or
    size set wrong could be corrected (Ali, 2026-11-23, inventory-tng-37dj):
    the queued line says the unit and size it was shown, so it is refused,
    never recorded in the corrected ones."""
    place = Place(session)
    spool = add(session, ItemPackage(item_id=place.cable.id, name="Spool", size=305))
    label = labels.make_for_item(session, place.cable.id, spool.id)
    assert label is not None
    line = {
        "item_id": place.cable.id,
        "quantity": "305",
        "package_id": spool.id,
        "package_size": "305",
        "unit": "m",
    }
    receipt = place.batch(
        kind="receipt",
        from_location_id=None,
        to_location_id=place.store.id,
        lines=[line],
    )
    assert labels.revoke(session, label.id, NOW)
    if resized:
        items.save_package(session, spool, "Spool", Decimal(300), seen=None)
        refused = (["lines", 0, "package_size"], "package_resized")
    else:
        items.save(session, place.cable, item_fields(place.cable, unit="ft"), seen=None)
        refused = (["lines", 0, "unit"], "unit_changed")
    response = registered_client.post(URL, json=receipt)
    assert problems_of(response) == [refused]
    assert quantities_by_form(session, place.cable) == {}


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
    words = changed_words(
        session, form, store, [store], Now({cable: "ft"}, {}), separator="."
    )
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


def test_every_box_wrong_in_any_unit_says_so_after_its_new_unit(
    session: Session,
) -> None:
    """Each kind of box of a cable switched to ft, its number wrong in any
    unit: the new unit, then what is wrong, as the box reads it, so both
    are fixed at once (Ali, 2026-11-25, inventory-tng-yxd1). An open
    package said used up is not read, so only its unit is said."""
    spools = Spools(session)
    made = spools.opened()
    cable, spool, store = spools.cable.id, spools.spool.id, spools.store.id
    too_big = str(QUANTITY_MAX + 1)
    form = {
        f"loose_{cable}": "-1",
        f"sealed_{cable}_{spool}": "1.5",
        f"opened_{made.id}": "x",
        "extra_item_id": str(cable),
        "extra_quantity": "2,5",
        "found_package_id": str(spool),
        "found_left": "0",
        f"arrived_{spools.van.id}_loose_{cable}": too_big,
    }
    ft = Now({cable: "ft"}, {})
    words = changed_words(session, form, store, [store], ft, separator=".")

    assert words == {
        f"loose_{cable}": now_counted_in("ft", ZERO_OR_MORE),
        f"sealed_{cable}_{spool}": now_counted_in("ft", WHOLE_PACKAGES),
        f"opened_{made.id}": now_counted_in("ft", type_a_number(".")),
        "extra_quantity": now_counted_in("ft", use_separator(".")),
        "found_left": now_counted_in("ft", ABOVE_ZERO),
        f"arrived_{spools.van.id}_loose_{cable}": now_counted_in("ft", TOO_BIG),
    }
    used_up = form | {f"state_{made.id}": "used_up"}
    words = changed_words(session, used_up, store, [store], ft, separator=".")
    assert words[f"opened_{made.id}"] == now_counted_in("ft")


def test_a_box_wrong_only_in_its_new_unit_says_only_the_unit(
    session: Session,
) -> None:
    """Half a cable typed, the cable now counted each: only the new unit is
    said, as the number is typed again in it (Ali, 2026-11-25,
    inventory-tng-yxd1)."""
    spools = Spools(session)
    cable, store = spools.cable.id, spools.store.id
    form = {f"loose_{cable}": "0.5"}
    each = Now({cable: "each"}, {})
    words = changed_words(session, form, store, [store], each, separator=".")
    assert words == {f"loose_{cable}": now_counted_in("each")}


def test_a_box_wrong_otherwise_says_so_after_its_new_unit(session: Session) -> None:
    """A box read_count refused for something other than its number says
    that after the new unit, in place of what is wrong with its number
    (Ali, 2026-11-25, inventory-tng-jnwh); one refused for its number in
    the old unit says what is wrong in any, as before."""
    spools = Spools(session)
    cable, spool, store = spools.cable.id, spools.spool.id, spools.store.id
    form = {f"sealed_{cable}_{spool}": "x", f"loose_{cable}": "0.5"}
    others = {f"sealed_{cable}_{spool}": NO_SUCH_PACKAGE, f"loose_{cable}": WHOLE_UNITS}
    ft = Now({cable: "ft"}, {})
    words = changed_words(
        session, form, store, [store], ft, separator=".", others=others
    )
    assert words == {
        f"sealed_{cable}_{spool}": now_counted_in("ft", NO_SUCH_PACKAGE),
        f"loose_{cable}": now_counted_in("ft"),
    }


def _named_twice(
    client: TestClient, session: Session
) -> tuple[int, int, dict[str, str], str, Words]:
    """Cable counted on two lines, as only a hand-made form sends."""
    place = Place(session)
    cable = place.cable.id
    received(session, place, [{"item_id": cable, "quantity": "5", "unit": "m"}])
    form = count_form_at(client, place.store.id) | {
        f"counted_{cable}": "3",
        f"counted_0{cable}": "2",
    }
    return place.store.id, cable, form, f"counted_{cable}", ON_THE_FORM_TWICE


def _typed_on_another_line(
    client: TestClient, session: Session
) -> tuple[int, int, dict[str, str], str, Words]:
    """Cable on its loose line and as something else here."""
    spools = Spools(session)
    cable = spools.cable.id
    form = count_form_at(client, spools.store.id) | {
        f"sealed_{cable}_{spools.spool.id}": "2",
        f"loose_{cable}": "0",
        "extra_item_id": str(cable),
        "extra_quantity": "3",
    }
    return spools.store.id, cable, form, f"loose_{cable}", TYPE_IT_HERE


def _no_such_state(
    client: TestClient, session: Session
) -> tuple[int, int, dict[str, str], str, Words]:
    """An open spool said to be something it cannot be."""
    spools = Spools(session)
    made = spools.opened()
    form = count_form_at(client, spools.store.id) | {
        f"opened_{made.id}": "3",
        f"state_{made.id}": "lent",
    }
    return (
        spools.store.id,
        spools.cable.id,
        form,
        f"opened_{made.id}",
        NO_SUCH_OPEN_PACKAGE,
    )


@pytest.mark.parametrize("switched", [True, False], ids=["switched", "same"])
@pytest.mark.parametrize("sent", [_named_twice, _typed_on_another_line, _no_such_state])
def test_a_count_refused_otherwise_after_a_switch_says_both(
    admin_client: TestClient,
    session: Session,
    sent: Callable[[TestClient, Session], tuple[int, int, dict[str, str], str, Words]],
    switched: bool,
) -> None:
    """A count refused for a box's other problem, its item switched to ft
    meanwhile: the box says the new unit, then that problem, so both are
    fixed at once (Ali, 2026-11-25, inventory-tng-jnwh). Not switched, it
    says the problem alone, as before."""
    place_id, item_id, form, field, other = sent(admin_client, session)
    if switched:
        to_feet(session, session.get_one(Item, item_id))
    response = admin_client.post(f"{COUNTS}/{place_id}", data=form)
    assert response.status_code == 400
    said = now_counted_in("ft", other) if switched else str(other)
    assert problem(response.text, field) == said
    assert counts_written(session) == []


@pytest.mark.parametrize(
    ("listed", "switched"),
    [(True, True), (True, False), (False, True)],
    ids=["both", "listed", "switched"],
)
def test_a_count_listed_elsewhere_after_a_switch_says_both_alerts(
    admin_client: TestClient, session: Session, listed: bool, switched: bool
) -> None:
    """Cable picked as something else here, though the store lists it, and
    switched to ft meanwhile: above the form, the new unit, then where it
    is listed (Ali, 2026-11-25, inventory-tng-zoyw). Each alone is said
    alone, as before."""
    spools = Spools(session)
    cable, store = spools.cable.id, spools.store.id
    form = count_form_at(admin_client, store) | {
        f"sealed_{cable}_{spools.spool.id}": "2",
        f"loose_{cable}": "0",
    }
    if listed:
        form |= {"extra_item_id": str(cable), "extra_quantity": "3"}
    if switched:
        to_feet(session, spools.cable)
    response = admin_client.post(f"{COUNTS}/{store}", data=form)
    assert response.status_code == 400
    where = count_routes.already_listed(spools.cable.name_shown, spools.store.name)
    alerts = [each.text for each in read(response.text).all("p", role="alert")]
    assert alerts == [now_counted_in("ft")] * switched + [where] * listed
    assert counts_written(session) == []


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
    to_feet(session, place.cable)
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


def test_a_count_box_not_read_after_a_switch_to_ft_says_so(
    admin_client: TestClient, session: Session
) -> None:
    """2,5 typed for the cable on a point page, so not read, and the cable
    switched to ft: the form shown again says beside the box that it is now
    counted in ft, so 2.5 typed again is not saved in ft unawares."""
    place = Place(session)
    form = count_form_at(admin_client, place.store.id) | {
        "extra_item_id": str(place.cable.id),
        "extra_quantity": "2,5",
    }
    to_feet(session, place.cable)
    response = admin_client.post(f"{COUNTS}/{place.store.id}", data=form)
    assert response.status_code == 400
    assert problem(response.text, "extra_quantity") == now_counted_in(
        "ft", use_separator(".")
    )
    assert box_values(response.text, "extra_quantity") == ["2,5"]
    assert counts_written(session) == []


def test_a_count_box_below_0_after_a_switch_to_ft_says_both(
    admin_client: TestClient, session: Session
) -> None:
    """-5 typed for the cable in m, the cable switched to ft: both said at
    once, not the second only once it is typed again (Ali, 2026-11-25,
    inventory-tng-yxd1)."""
    place = Place(session)
    form = count_form_at(admin_client, place.store.id) | {
        "extra_item_id": str(place.cable.id),
        "extra_quantity": "-5",
    }
    to_feet(session, place.cable)
    response = admin_client.post(f"{COUNTS}/{place.store.id}", data=form)
    assert response.status_code == 400
    assert problem(response.text, "extra_quantity") == (
        "This item is now counted in ft. Check the amount and save again. "
        "Use 0 or more."
    )
    assert counts_written(session) == []


def test_a_count_refused_by_a_rule_after_a_switch_says_both(
    admin_client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refused by a rule that marks no line, and the cable switched to ft
    just after: the form shown again says the rule above it and the switch
    beside the 50 typed in m."""
    place = Place(session)
    form = count_form_at(admin_client, place.store.id) | {
        "extra_item_id": str(place.cable.id),
        "extra_quantity": "50",
    }

    def refused_once(*_: object) -> tuple[object, str]:
        # Another administrator's edit, as the test's own rows are written.
        to_feet(setting_up(session), place.cable)
        del session.info[UNGUARDED_INFO]
        return None, count_routes.ONCE

    monkeypatch.setattr(count_routes, "refusal", refused_once)
    response = admin_client.post(f"{COUNTS}/{place.store.id}", data=form)
    assert response.status_code == 400
    assert str(RULES[count_routes.ONCE]) in text(response)
    assert problem(response.text, "extra_quantity") == now_counted_in("ft")
    assert box_values(response.text, "extra_quantity") == ["50"]
    assert counts_written(session) == []


def test_a_count_saved_then_sent_again_after_a_switch_is_answered_as_saved(
    admin_client: TestClient, session: Session
) -> None:
    """Sent again, as the browser's Back sends it, a saved count is
    answered as it was, whatever the unit is now: what it showed was
    checked as it was saved. The switch is made straight in the database,
    as a moved item's unit is fixed."""
    place = Place(session)
    form = count_form_at(admin_client, place.store.id) | {
        "extra_item_id": str(place.cable.id),
        "extra_quantity": "50",
    }
    path = f"{COUNTS}/{place.store.id}"
    saved = admin_client.post(path, data=form, follow_redirects=False)
    assert saved.status_code == 303
    to_feet(session, place.cable)
    again = admin_client.post(path, data=form, follow_redirects=False)
    assert again.headers["location"] == saved.headers["location"]


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
    # No box of it is shown again: said above the form.
    assert now_holds(Decimal(12), "each") in text(response)
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
        separator=".",
    ) == {field: now_holds(Decimal(12), "each")}


# What a spool box says once the spool holds 305 m.
HOLDS_305 = "This package now holds 305 m. Check the count and save again."


@pytest.mark.parametrize(
    ("typed", "said"),
    [
        ("x", f"{HOLDS_305} A whole number of packages."),
        ("99999999", HOLDS_305),
        ("3", HOLDS_305),
    ],
    ids=["not-whole", "too-many", "resized-alone"],
)
def test_a_sealed_box_wrong_otherwise_says_so_after_its_new_size(
    admin_client: TestClient, session: Session, typed: str, said: str
) -> None:
    """A sealed box of a package shown at another size says its new size,
    then what else is wrong with it at any size (Ali, 2026-11-25,
    inventory-tng-ehfu); not too many, which depends on the size, as the
    count is typed again at the new one. Resized alone, the size alone.
    A held package's size is fixed but by a unit change, which says the
    unit instead, so only a form saying another size reaches this."""
    spools = Spools(session)
    cable, spool, store = spools.cable.id, spools.spool.id, spools.store.id
    field = f"sealed_{cable}_{spool}"
    form = count_form_at(admin_client, store) | {
        "sizes": f"{spool}:300",
        field: typed,
    }
    response = admin_client.post(f"{COUNTS}/{store}", data=form)
    assert response.status_code == 400
    assert problem(response.text, field) == said
    assert counts_written(session) == []


def test_a_sealed_box_of_another_items_package_is_not_said_resized(
    admin_client: TestClient, session: Session
) -> None:
    """A sealed box naming the cable and a box of radios, shown at another
    size: refused as no such package, never said to hold a size, nor
    refused as shown otherwise (inventory-tng-ehfu)."""
    spools = Spools(session)
    box = box_of_ten(session, spools.place)
    store = spools.store.id
    form = count_form_at(admin_client, store)
    form |= {
        "sizes": f"{form['sizes']} {box.id}:9",
        f"sealed_{spools.cable.id}_{box.id}": "1",
    }
    with capture_logs() as logs:
        response = admin_client.post(f"{COUNTS}/{store}", data=form)
    assert response.status_code == 400
    assert now_holds(Decimal(10), "each") not in text(response)
    assert "save refused: shown otherwise" not in [each["event"] for each in logs]
    assert counts_written(session) == []


# An adjustment, and an open package's count confirmed.


def test_an_adjustment_of_50_m_saved_after_a_switch_to_ft_is_asked_again(
    admin_client: TestClient, session: Session
) -> None:
    place = Place(session)
    form = adjustment_form(
        session, place.store, place.cable, amount="50", direction="found"
    )
    assert form["units"] == f"{place.cable.id}:m"
    to_feet(session, place.cable)
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


def test_an_adjustment_shown_in_m_says_so_whatever_else_is_wrong(
    admin_client: TestClient, session: Session
) -> None:
    """Sent with no reason once the cable counts in ft: the form shown
    again asks for a reason and says the cable is now counted in ft, so
    it is never saved in ft unawares."""
    place = Place(session)
    form = adjustment_form(
        session, place.store, place.cable, amount="50", direction="found"
    )
    to_feet(session, place.cable)
    response = admin_client.post(ADJUSTMENTS, data=form | {"reason_id": ""})
    assert response.status_code == 400
    assert now_counted_in("ft") in text(response)
    assert counts_written(session) == []


@pytest.mark.parametrize(
    ("typed", "unit", "said"),
    [
        ("-5", "ft", now_counted_in("ft", ABOVE_ZERO)),
        ("", "ft", now_counted_in("ft", HOW_MUCH)),
        ("1,5", "ft", now_counted_in("ft", use_separator("."))),
        ("1.5", "each", now_counted_in("each")),
    ],
    ids=["below 0", "blank", "comma", "half of one"],
)
def test_an_adjustment_wrong_after_a_switch_says_what_in_any_unit(
    admin_client: TestClient, session: Session, typed: str, unit: str, said: str
) -> None:
    """An amount typed in m, the cable switched since: what is wrong with
    it in any unit is said after the new unit, so both are fixed at once;
    whole units, which depend on the unit, are not (Ali, 2026-11-25,
    inventory-tng-yxd1)."""
    place = Place(session)
    form = adjustment_form(
        session, place.store, place.cable, amount=typed, direction="found"
    )
    place.cable.unit = unit
    session.flush()
    response = admin_client.post(ADJUSTMENTS, data=form)
    assert response.status_code == 400
    assert problem(response.text, "amount") == said
    assert counts_written(session) == []


def test_an_adjustment_refused_otherwise_says_a_switch_since(
    admin_client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refused as the store was retired, and the cable switched to ft just
    after: the form shown again in ft says so beside the 50 typed in m."""
    place = Place(session)
    form = adjustment_form(
        session, place.store, place.cable, amount="50", direction="found"
    )
    retire(session, place.store)
    refusal = counts_routes.refusal

    def switched_after(
        held: Session, change: Callable[[], object], *expected: str
    ) -> tuple[object, str | None]:
        refused = refusal(held, change, *expected)
        # Another administrator's edit, as the test's own rows are written.
        to_feet(setting_up(session), place.cable)
        del session.info[UNGUARDED_INFO]
        return refused

    monkeypatch.setattr(counts_routes, "refusal", switched_after)
    response = admin_client.post(ADJUSTMENTS, data=form)
    assert response.status_code == 400
    assert problem(response.text, "amount") == now_counted_in("ft")
    assert box_values(response.text, "amount") == ["50"]
    assert counts_written(session) == []


def test_a_confirmed_count_shown_in_another_unit_is_asked_again(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Never so on the page, which shows it in its line's unit: a form
    sent from elsewhere."""
    spools = Spools(session)
    moved_whole(registered_client, spools)
    _, response = counted(admin_client, session, "300", unit="ft")
    assert response.status_code == 400
    assert now_counted_in("m") in text(response)
    assert checks(session)[0].settled_at is None
    assert adjustments(session) == []


@pytest.mark.parametrize(
    ("typed", "said"),
    [
        ("-5", now_counted_in("m", ZERO_OR_MORE)),
        ("1,5", now_counted_in("m", use_separator("."))),
        ("", now_counted_in("m", type_a_number("."))),
    ],
)
def test_a_confirmed_count_in_another_unit_says_what_is_wrong_in_any(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    typed: str,
    said: str,
) -> None:
    """-5 typed in ft for a spool's line in m: the box says both at once,
    as the count and adjustment forms do (inventory-tng-fezg), and a
    comma is counted refused once."""
    spools = Spools(session)
    moved_whole(registered_client, spools)
    with capture_logs() as logs:
        check, response = counted(admin_client, session, typed, unit="ft")
    assert response.status_code == 400
    assert problem(response.text, f"count-{check.id}") == said
    refused = [each for each in logs if each["event"] == "number refused"]
    assert len(refused) == (typed == "1,5")
    assert checks(session)[0].settled_at is None


def test_a_confirmed_count_wrong_only_in_its_new_unit_says_only_the_unit(
    admin_client: TestClient, registered_client: TestClient, session: Session
) -> None:
    """Half a spool's worth in m, for a line counted each: whole units are
    checked once it is typed again in each."""
    spools = Spools(session, cable_unit="each")
    moved_whole(registered_client, spools)
    check, response = counted(admin_client, session, "0.5", unit="m")
    assert response.status_code == 400
    assert problem(response.text, f"count-{check.id}") == now_counted_in("each")
