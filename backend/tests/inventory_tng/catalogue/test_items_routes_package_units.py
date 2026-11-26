"""The item page's package amounts say their unit right after their box,
and to a screen reader in its description, not its name (decision 0019,
Amounts; Ali, 2026-11-26, inventory-tng-0mor): a package's Holds and Too
short to use, and the Add package form's Holds."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.catalogue.paths import ITEMS
from inventory_tng.models.catalogue import Category, ItemPackage

from helpers.items import new_item
from helpers.markup import read, unit_beside
from helpers.rows import add


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
