"""What each box of a count form is counted in, right after it and to a
screen reader (templates/stock/count.html; inventory-tng-mdvz): the
item's unit, or "sealed" for a box counting sealed packages."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.stock.paths import COUNTS
from inventory_tng.web.formatting import PRINTED_LENGTH

from helpers.markup import Element, read
from helpers.stock import Place, Spools, received, to_feet


def page_at(client: TestClient, place_id: int) -> Element:
    return read(client.get(f"{COUNTS}/{place_id}").text)


def beside(page: Element, name: str) -> tuple[str, list[str]]:
    """The words right after the box named `name`, and each that describes
    it to a screen reader (aria-describedby), in order."""
    box = page.one("input", name=name)
    after = box.parent
    assert isinstance(after, Element)
    assert "with-unit" in after.classes
    ids = box.attrs.get("aria-describedby", "").split()
    return after.text, [page.one("*", id=each).text for each in ids]


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
    units = {
        option.attrs["value"]: option.attrs.get("data-unit")
        for option in page.one("select", name="extra_item_id").all("option")
    }
    assert units[str(other.cable.id)] == "ft"
    assert units[str(other.item.id)] == "each"
    after = page.one("input", name="extra_quantity").parent
    assert isinstance(after, Element)
    assert after.one("span", data_unit_of="extra_item_id").text == ""
