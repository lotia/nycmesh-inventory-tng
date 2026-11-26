"""What each box of a count form is counted in, right after it and to a
screen reader (templates/stock/count.html; inventory-tng-mdvz): the
item's unit, or "sealed" for a box counting sealed packages. Refused, a
box's problem comes first (inventory-tng-h3tw)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.stock.count_forms import WHOLE_PACKAGES
from inventory_tng.stock.paths import COUNTS
from inventory_tng.web.formatting import PRINTED_LENGTH
from inventory_tng.web.forms import type_a_number

from helpers.markup import Element, read, said_beside, units_offered
from helpers.stock import Place, Spools, count_form_at, received, to_feet


def page_at(client: TestClient, place_id: int) -> Element:
    return read(client.get(f"{COUNTS}/{place_id}").text)


def refused(client: TestClient, place_id: int, typed: dict[str, str]) -> Element:
    """The count form sent with `typed`, refused, as shown again."""
    form = count_form_at(client, place_id) | typed
    response = client.post(f"{COUNTS}/{place_id}", data=form)
    assert response.status_code == 400
    return read(response.text)


def invalid(page: Element, name: str) -> bool:
    return page.one("*", name=name).attrs.get("aria-invalid") == "true"


def beside(page: Element, name: str) -> tuple[str, list[str]]:
    """said_beside the box named `name`."""
    return said_beside(page, page.one("input", name=name))


def loose_cable_and_radios(session: Session, unit: str) -> Place:
    """Twenty of a cable in `unit`, by no package, and three radios, each,
    received into the store."""
    place = Place(session, cable_unit=unit)
    received(
        session,
        place,
        [
            {"item_id": place.cable.id, "quantity": "20", "unit": unit},
            {"item_id": place.item.id, "quantity": "3", "unit": place.item.unit},
        ],
    )
    return place


@pytest.mark.parametrize("unit", ["m", "ft"])
def test_a_loose_box_says_its_item_s_unit(
    admin_client: TestClient, session: Session, unit: str
) -> None:
    place = loose_cable_and_radios(session, unit)
    page = page_at(admin_client, place.store.id)
    assert beside(page, f"counted_{place.cable.id}") == (unit, [unit])
    assert place.item.unit == "each"
    assert beside(page, f"counted_{place.item.id}") == ("each", ["each"])


@pytest.mark.parametrize("unit", ["m", "ft"])
def test_a_box_of_sealed_packages_says_sealed_and_an_open_one_its_unit(
    admin_client: TestClient, session: Session, unit: str
) -> None:
    """A sealed box's label already says "sealed", so a screen reader
    isn't told it again (Ali, 2026-11-25). The open package's box keeps
    how to read a printed length, after its unit."""
    spools = Spools(session, cable_unit=unit)
    opened = spools.opened()
    page = page_at(admin_client, spools.store.id)
    sealed = f"sealed_{spools.cable.id}_{spools.spool.id}"
    assert beside(page, sealed) == ("sealed", [])
    assert "sealed" in page.one("input", name=sealed).attrs["aria-label"]
    assert beside(page, f"opened_{opened.id}") == (unit, [unit, str(PRINTED_LENGTH)])


def test_a_box_says_the_unit_its_item_is_counted_in_now(
    admin_client: TestClient, session: Session
) -> None:
    place = loose_cable_and_radios(session, "m")
    box = f"counted_{place.cable.id}"
    assert beside(page_at(admin_client, place.store.id), box) == ("m", ["m"])
    to_feet(session, place.cable)
    assert beside(page_at(admin_client, place.store.id), box) == ("ft", ["ft"])


def test_something_else_here_says_the_unit_of_each_item_offered(
    admin_client: TestClient, session: Session
) -> None:
    """Shown, in the browser, beside How much once one is picked
    (frontend/src/admin/hints.ts, followUnits)."""
    place = loose_cable_and_radios(session, "m")
    other = Place(session, cable_unit="ft")
    page = page_at(admin_client, place.store.id)
    units = units_offered(page, "extra_item_id")
    assert units[str(other.cable.id)] == "ft"
    assert units[str(other.item.id)] == "each"
    after = page.one("input", name="extra_quantity").parent
    assert isinstance(after, Element)
    assert after.one("span", data_unit_of="extra_item_id").text == ""


def test_a_refused_box_is_described_by_its_problem_then_its_unit(
    admin_client: TestClient, session: Session
) -> None:
    """A screen reader on the box hears why it was refused, then its unit;
    a box with no problem is as before (inventory-tng-h3tw)."""
    place = loose_cable_and_radios(session, "m")
    cable, radios = f"counted_{place.cable.id}", f"counted_{place.item.id}"
    page = refused(admin_client, place.store.id, {cable: "lots", radios: "3"})
    assert beside(page, cable) == ("m", [type_a_number(), "m"])
    assert invalid(page, cable)
    assert beside(page, radios) == ("each", ["each"])
    assert not invalid(page, radios)


def test_a_refused_sealed_or_open_box_is_described_by_its_problem_first(
    admin_client: TestClient, session: Session
) -> None:
    """A sealed box says only its problem, its label saying "sealed"; an
    open package's box, its problem, unit and how to read a printed
    length."""
    spools = Spools(session)
    opened = spools.opened()
    sealed = f"sealed_{spools.cable.id}_{spools.spool.id}"
    typed = {sealed: "1.5", f"opened_{opened.id}": "lots"}
    page = refused(admin_client, spools.store.id, typed)
    assert beside(page, sealed) == ("sealed", [str(WHOLE_PACKAGES)])
    assert beside(page, f"opened_{opened.id}") == (
        "m",
        [type_a_number(), "m", str(PRINTED_LENGTH)],
    )
    assert invalid(page, sealed)
    assert invalid(page, f"opened_{opened.id}")


def test_a_refused_box_whose_problem_is_in_its_label_is_marked_invalid(
    admin_client: TestClient, session: Session
) -> None:
    """How much's problem is inside its label, so in its name already: it
    is not described by it again, only marked invalid."""
    place = loose_cable_and_radios(session, "m")
    typed = {"extra_item_id": str(place.cable.id), "extra_quantity": "lots"}
    page = refused(admin_client, place.store.id, typed)
    box = page.one("input", name="extra_quantity")
    label = box.find_parent("label")
    assert isinstance(label, Element)
    assert type_a_number() in label.spoken
    assert "aria-describedby" not in box.attrs
    assert invalid(page, "extra_quantity")
