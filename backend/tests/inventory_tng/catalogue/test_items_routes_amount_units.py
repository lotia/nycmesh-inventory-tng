"""The item page's amounts say their unit right after their box, and to
a screen reader in its description, not its name (decision 0019,
Amounts; Ali, 2026-11-26): the item's Reorder at (inventory-tng-h39f), a
package's Holds and Too short to use, and the Add package form's Holds
(inventory-tng-0mor). Refused for what was
typed, such a box is marked invalid, its problem heard in its label
(inventory-tng-h3tw's rule; inventory-tng-qyf6). Sent back with its unit
changed, Reorder at says the unit it is read in (inventory-tng-zhnd)."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.catalogue.paths import ITEMS
from inventory_tng.models.catalogue import Category, ItemPackage

from helpers.items import new_item, shown_item_form
from helpers.markup import Element, marked, problem, read, unit_beside
from helpers.rows import add, shown
from helpers.stock import Place, received


@pytest.mark.parametrize("unit", ["m", "ft", "each"])
def test_reorder_at_says_its_unit_after_its_box(
    admin_client: TestClient, session: Session, radios: Category, unit: str
) -> None:
    """Its hint no longer says the unit."""
    item = new_item(session, radios, name="Cable", unit=unit)
    page = read(admin_client.get(f"{ITEMS}/{item.id}").text)
    form = page.one("form", action=f"{ITEMS}/{item.id}")
    assert unit_beside(page, form.one("input", name="minimum")) == (
        unit,
        [unit],
        "Reorder at total across stores; blank never reorders",
    )


def _reorder_at(page: Element, item_id: int) -> tuple[str, str, str | None]:
    """Reorder at's unit, said after its box and in its description; its
    keypad; and the select whose unit it follows in the browser
    (hints.followUnits), if any."""
    box = page.one("form", action=f"{ITEMS}/{item_id}").one("input", name="minimum")
    unit, described, _ = unit_beside(page, box)
    assert described == [unit]
    follows = page.one("span", id="unit-minimum").attrs.get("data-unit-of")
    return unit, box.attrs["inputmode"], follows


def test_reorder_at_sent_back_says_the_unit_sent_while_it_can_change(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """The bead's example: an item in m with no records, its unit changed
    to each, Reorder at 2 and its name emptied. Refused for its name, the
    page says each after Reorder at, with each's keypad, as the 2 is
    saved in each once the name is put right."""
    item = new_item(session, radios, name="Cable", unit="m")
    assert _reorder_at(read(admin_client.get(f"{ITEMS}/{item.id}").text), item.id) == (
        "m",
        "decimal",
        "unit",
    )
    sent = shown_item_form(item, unit="each", minimum="2")
    refused = admin_client.post(f"{ITEMS}/{item.id}", data=sent | {"name": ""})
    assert refused.status_code == 400
    page = read(refused.text)
    assert _reorder_at(page, item.id) == ("each", "numeric", "unit")
    options = page.one("select", name="unit").all("option")
    assert {each.text: each.attrs["data-inputmode"] for each in options} == {
        "m": "decimal",
        "ft": "decimal",
        "each": "numeric",
    }
    assert all(each.attrs["data-unit"] == each.text for each in options)
    assert admin_client.post(f"{ITEMS}/{item.id}", data=sent).status_code == 303
    session.refresh(item)
    assert (item.unit, item.minimum) == ("each", Decimal(2))


def test_reorder_at_sent_back_says_the_item_unit_while_it_is_fixed(
    admin_client: TestClient, session: Session
) -> None:
    """Stock has moved in m, so a change to ft converts a Reorder at typed
    with it from m (unit_changes.convert): refused for its name, and asked
    whether its amounts were in m, the page says m after Reorder at, and
    it follows no select."""
    place = Place(session)
    received(
        session, place, [{"item_id": place.cable.id, "quantity": "305", "unit": "m"}]
    )
    cable = place.cable
    sent = shown_item_form(cable, unit="ft", minimum="10")
    refused = admin_client.post(f"{ITEMS}/{cable.id}", data=sent | {"name": ""})
    assert refused.status_code == 400
    assert _reorder_at(read(refused.text), cable.id) == ("m", "decimal", None)
    asked = admin_client.post(f"{ITEMS}/{cable.id}", data=sent)
    assert asked.status_code == 200
    assert _reorder_at(read(asked.text), cable.id) == ("m", "decimal", None)


@pytest.mark.parametrize("unit", ["m", "ft", "each"])
def test_each_package_amount_says_its_unit_after_its_box(
    admin_client: TestClient, session: Session, radios: Category, unit: str
) -> None:
    """Nothing moved in it and its opened ones followed, so its Holds and
    Too short to use boxes are both shown. The longer hints stay in the
    label, without the unit."""
    item = new_item(session, radios, name="Cable", unit=unit)
    item.track_opened = True
    reel = add(session, ItemPackage(item_id=item.id, name="Reel", size=Decimal(305)))
    page = read(admin_client.get(f"{ITEMS}/{item.id}").text)
    own = page.one("form", action=f"{ITEMS}/{item.id}/packages/{reel.id}")
    assert unit_beside(page, own.one("input", name="size")) == (
        unit,
        [unit],
        "Holds fixed once some has moved or been opened, or while it is labelled",
    )
    settings = page.one("form", action=f"{ITEMS}/{item.id}/packages/{reel.id}/settings")
    assert unit_beside(page, settings.one("input", name="short_below")) == (
        unit,
        [unit],
        "Too short to use below; blank: none",
    )
    adding = page.one("form", id="add-package")
    assert unit_beside(page, adding.one("input", name="size")) == (
        unit,
        [unit],
        "Holds",
    )


@pytest.mark.parametrize(
    ("sent_to", "box"),
    [("reel", "size"), ("reel settings", "short_below"), ("add", "size")],
)
def test_a_refused_package_amount_is_marked_invalid_and_keeps_its_unit(
    admin_client: TestClient, session: Session, radios: Category, sent_to: str, box: str
) -> None:
    """A Holds or Too short to use sent as "lots" from one form: only that
    form's box is marked invalid, still described by its unit alone, as
    its problem is in its label; the same boxes of the other forms are as
    first shown."""
    item = new_item(session, radios, name="Cable", unit="m")
    item.track_opened = True
    reel = add(session, ItemPackage(item_id=item.id, name="Reel", size=Decimal(305)))
    spool = add(session, ItemPackage(item_id=item.id, name="Spool", size=Decimal(50)))
    here = f"{ITEMS}/{item.id}"
    own, settings = f"{here}/packages/{reel.id}", f"{here}/packages/{reel.id}/settings"
    action, data = {
        "reel": (own, {"package_name": "Reel", "package_version": shown(reel)}),
        "reel settings": (settings, {"minimum_sealed": ""}),
        "add": (f"{here}/packages", {"package_name": "Box"}),
    }[sent_to]
    refused = admin_client.post(action, data=data | {box: "lots", "unit_shown": "m"})
    assert refused.status_code == 400
    page = read(refused.text)
    assert problem(page, box)
    boxes = {
        "reel": (own, "size", f"unit-size-{reel.id}"),
        "reel settings": (settings, "short_below", f"unit-short_below-{reel.id}"),
        "spool": (f"{here}/packages/{spool.id}", "size", f"unit-size-{spool.id}"),
        "spool settings": (
            f"{here}/packages/{spool.id}/settings",
            "short_below",
            f"unit-short_below-{spool.id}",
        ),
        "add": (f"{here}/packages", "size", "unit-size"),
    }
    assert marked(
        {
            which: page.one("form", action=at).one("input", name=name)
            for which, (at, name, _) in boxes.items()
        }
    ) == ({which: unit for which, (_, _, unit) in boxes.items()}, [sent_to])
