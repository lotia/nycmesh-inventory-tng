"""The item page's amounts say their unit right after their box, and to
a screen reader in its description, not its name (decision 0019,
Amounts; Ali, 2026-11-26): the item's Reorder at (inventory-tng-h39f), a
package's Holds and Too short to use, and the Add package form's Holds
(inventory-tng-0mor). Refused for what was
typed, such a box is marked invalid, its problem heard in its label
(inventory-tng-h3tw's rule; inventory-tng-qyf6). Sent back with its unit
changed, Reorder at says the unit it is read in (inventory-tng-zhnd), and
a save that would read it in another, as stock moved or a label was
printed or revoked since, is refused, saying which (Ali, 2026-11-27)."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.catalogue.items_routes import UNIT_FREE
from inventory_tng.catalogue.paths import ITEMS
from inventory_tng.labels import labels
from inventory_tng.models.catalogue import Category, Item, ItemPackage
from inventory_tng.web.forms import CHANGED, RULES

from helpers.items import NOW, new_item, shown_item_form
from helpers.labels import labelled
from helpers.markup import Element, alerts_on, marked, problem, read, unit_beside
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


def _unit_free(page: Element, item_id: int) -> str:
    """Whether the item form says its unit is free, as it sends it."""
    form = page.one("form", action=f"{ITEMS}/{item_id}")
    return form.one("input", name=UNIT_FREE).attrs["value"]


def _refused_since(
    client: TestClient,
    session: Session,
    item: Item,
    drawn: str,
    minimum: str,
    rule: str,
) -> None:
    """A change of `item`'s unit to ft, Reorder at `minimum`, sent from a
    form `drawn` free or not: refused with `rule`'s words alone, which say
    what changed since, nothing saved."""
    unit, before = item.unit, item.minimum
    sent = shown_item_form(item, unit="ft", minimum=minimum) | {UNIT_FREE: drawn}
    refused = client.post(f"{ITEMS}/{item.id}", data=sent)
    assert alerts_on(refused.text) == [str(RULES[rule])]
    session.refresh(item)
    assert (item.unit, item.minimum) == (unit, before)


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
    assert all(each.attrs["data-unit"] == each.text for each in options)
    assert _unit_free(page, item.id) == "yes"
    saved = admin_client.post(f"{ITEMS}/{item.id}", data=sent | {UNIT_FREE: "yes"})
    assert saved.status_code == 303
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
    page = read(refused.text)
    assert _reorder_at(page, cable.id) == ("m", "decimal", None)
    asked = admin_client.post(
        f"{ITEMS}/{cable.id}", data=sent | {UNIT_FREE: _unit_free(page, cable.id)}
    )
    assert asked.status_code == 200
    page = read(asked.text)
    assert _reorder_at(page, cable.id) == ("m", "decimal", None)
    assert _unit_free(page, cable.id) == "no"


def test_reorder_at_typed_while_free_is_refused_once_stock_has_moved(
    admin_client: TestClient, session: Session
) -> None:
    """Drawn while the cable's unit was free, Reorder at in the unit
    chosen; stock received in m before Save, which would read the 10 in m
    and convert it: refused, saying stock moved, nothing saved."""
    place = Place(session)
    cable = place.cable
    drawn = _unit_free(read(admin_client.get(f"{ITEMS}/{cable.id}").text), cable.id)
    assert drawn == "yes"
    received(session, place, [{"item_id": cable.id, "quantity": "305", "unit": "m"}])
    _refused_since(
        admin_client, session, cable, drawn, "10", "item_unit_moved_since_shown"
    )


def test_reorder_at_typed_while_free_is_refused_once_a_label_is_printed(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Drawn while the cable's unit was free; a label printed before Save,
    which would read the 10 in m and convert it: refused, saying a label
    was printed, nothing saved."""
    cable = new_item(session, radios, "Cat6 cable", unit="m")
    drawn = _unit_free(read(admin_client.get(f"{ITEMS}/{cable.id}").text), cable.id)
    assert drawn == "yes"
    labelled(session, cable, 0)
    _refused_since(
        admin_client, session, cable, drawn, "10", "item_unit_labelled_since_shown"
    )


def test_reorder_at_typed_while_free_is_refused_once_converted_by_another(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Drawn while the cable's unit was free; its amounts converted by
    another's save before Save, which fixes it for good: refused as any
    save changed since, nothing saved. That save changed the item's
    version, so the stale check refuses it before the unit guard; the
    guard's own case is in test_telemetry."""
    cable = new_item(session, radios, "Cat6 cable", unit="m")
    drawn = _unit_free(read(admin_client.get(f"{ITEMS}/{cable.id}").text), cable.id)
    assert drawn == "yes"
    cable.unit_converted = True
    session.flush()
    _refused_since(admin_client, session, cable, drawn, "10", CHANGED)


def test_reorder_at_typed_while_labelled_is_refused_once_they_are_revoked(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Drawn while a label fixed the cable's unit, Reorder at in m, to be
    converted; the label revoked before Save, which would keep the 100 as
    ft and its packages' numbers unconverted: refused, saying its labels
    were revoked, nothing saved."""
    cable = new_item(session, radios, "Cat6 cable", unit="m")
    (label,) = labelled(session, cable, 0)
    drawn = _unit_free(read(admin_client.get(f"{ITEMS}/{cable.id}").text), cable.id)
    assert drawn == "no"
    assert labels.revoke(session, label.id, NOW)
    _refused_since(
        admin_client, session, cable, drawn, "100", "item_unit_revoked_since_shown"
    )


def test_a_unit_fixed_since_keeps_what_was_typed_beside_its_alert(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Drawn while the cable's unit was free; a label printed before Save:
    refused, saying so above the form sent back, so the part number and
    Reorder at typed are kept, Reorder at saying m, the unit it is read in
    now, to check before saving again."""
    cable = new_item(session, radios, "Cat6 cable", unit="m")
    labelled(session, cable, 0)
    sent = shown_item_form(cable, unit="ft", minimum="10", part_number="C6-OUT")
    refused = admin_client.post(f"{ITEMS}/{cable.id}", data=sent | {UNIT_FREE: "yes"})
    assert refused.status_code == 409
    assert alerts_on(refused.text) == [str(RULES["item_unit_labelled_since_shown"])]
    page = read(refused.text)
    form = page.one("form", action=f"{ITEMS}/{cable.id}")
    assert form.one("input", name="part_number").attrs["value"] == "C6-OUT"
    assert form.one("input", name="minimum").attrs["value"] == "10"
    assert _reorder_at(page, cable.id) == ("m", "decimal", None)
    assert _unit_free(page, cable.id) == "no"


def test_a_unit_fixed_since_is_said_when_sent_back_for_another_box(
    admin_client: TestClient, session: Session
) -> None:
    """Drawn while the cable's unit was free, its unit changed to ft,
    Reorder at 10 and its name emptied; stock received in m before Save.
    Refused for its name before its save could check the unit, the page
    sent back says m after Reorder at, as the 10 would now be read, and
    says stock moved, so the change is not saved unnoticed in m."""
    place = Place(session)
    cable = place.cable
    received(session, place, [{"item_id": cable.id, "quantity": "305", "unit": "m"}])
    sent = shown_item_form(cable, unit="ft", minimum="10", name="")
    refused = admin_client.post(f"{ITEMS}/{cable.id}", data=sent | {UNIT_FREE: "yes"})
    assert refused.status_code == 409
    assert alerts_on(refused.text) == [str(RULES["item_unit_moved_since_shown"])]
    page = read(refused.text)
    assert marked({"name": page.one("input", name="name")})[1] == ["name"]
    assert _reorder_at(page, cable.id) == ("m", "decimal", None)
    unchanged = shown_item_form(cable, unit="ft", minimum="10", name="")
    again = admin_client.post(f"{ITEMS}/{cable.id}", data=unchanged | {UNIT_FREE: "no"})
    assert str(RULES["item_unit_moved_since_shown"]) not in alerts_on(again.text)


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
