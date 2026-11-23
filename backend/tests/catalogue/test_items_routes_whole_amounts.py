"""An item counted each has whole amounts on its page: its Reorder at
refuses a decimal, and its amount boxes show the number keypad, while an
item in another unit keeps decimals and the decimal keypad (Ali,
2026-11-22; inventory-tng-qt6z)."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.catalogue.paths import ITEMS
from inventory_tng.models.catalogue import Category, Item, ItemPackage
from inventory_tng.web.forms import WHOLE_UNITS

from helpers.items import shown_item_form
from helpers.markup import problem, read
from helpers.rows import add


@pytest.mark.parametrize(("unit", "keyboard"), [("each", "numeric"), ("m", "decimal")])
def test_its_amount_boxes_show_the_keypad_its_unit_takes(
    admin_client: TestClient,
    session: Session,
    radios: Category,
    unit: str,
    keyboard: str,
) -> None:
    item = add(session, Item(name="LiteBeam", category_id=radios.id, unit=unit))
    add(session, ItemPackage(item_id=item.id, name="Box", size=Decimal(10)))
    boxes = read(admin_client.get(f"{ITEMS}/{item.id}").text)
    assert boxes.one("input", name="minimum").attrs["inputmode"] == keyboard
    assert boxes.one("input", name="short_below").attrs["inputmode"] == keyboard
    # The package's Holds, and the new package's.
    sizes = boxes.all("input", name="size")
    assert [box.attrs["inputmode"] for box in sizes] == [keyboard, keyboard]


@pytest.mark.parametrize("unit", ["each", "m"])
def test_reorder_at_is_whole_for_an_item_counted_each(
    admin_client: TestClient, session: Session, radios: Category, unit: str
) -> None:
    item = add(session, Item(name="LiteBeam", category_id=radios.id, unit=unit))
    sent = admin_client.post(
        f"{ITEMS}/{item.id}", data=shown_item_form(item, minimum="2.5")
    )
    session.refresh(item)
    if unit == "each":
        assert sent.status_code == 400
        assert problem(sent.text, "minimum") == str(WHOLE_UNITS)
        assert item.minimum is None
    else:
        assert sent.status_code == 303
        assert item.minimum == Decimal("2.5")
