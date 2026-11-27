"""The item page's amounts say their unit right after their box, and to
a screen reader in its description, not its name (decision 0019,
Amounts; Ali, 2026-11-26): the item's Reorder at (inventory-tng-h39f), a
package's Holds and Too short to use, and the Add package form's Holds
(inventory-tng-0mor). Refused for what was
typed, such a box is marked invalid, its problem heard in its label
(inventory-tng-h3tw's rule; inventory-tng-qyf6)."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.catalogue.paths import ITEMS
from inventory_tng.models.catalogue import Category, ItemPackage

from helpers.items import new_item
from helpers.markup import marked, problem, read, unit_beside
from helpers.rows import add, shown


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
