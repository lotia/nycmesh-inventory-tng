"""An item counted each has whole amounts on its page: its Reorder at
refuses a decimal, and its amount boxes show the number keypad, while an
item in another unit keeps decimals and the decimal keypad (Ali,
2026-11-22; inventory-tng-qt6z)."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.catalogue.paths import ITEMS
from inventory_tng.models.catalogue import Category, ItemPackage
from inventory_tng.web.forms import WHOLE_UNITS

from helpers.items import new_item, shown_item_form
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
    item = new_item(session, radios, unit=unit)
    add(session, ItemPackage(item_id=item.id, name="Box", size=Decimal(10)))
    boxes = read(admin_client.get(f"{ITEMS}/{item.id}").text)
    assert boxes.one("input", name="minimum").attrs["inputmode"] == keyboard
    assert boxes.one("input", name="short_below").attrs["inputmode"] == keyboard
    # The package's Holds, and the new package's.
    sizes = boxes.all("input", name="size")
    assert [box.attrs["inputmode"] for box in sizes] == [keyboard, keyboard]


@pytest.mark.parametrize(
    ("unit", "status", "kept"), [("each", 400, None), ("m", 303, Decimal("2.5"))]
)
def test_reorder_at_is_whole_for_an_item_counted_each(
    admin_client: TestClient,
    session: Session,
    radios: Category,
    unit: str,
    status: int,
    kept: Decimal | None,
) -> None:
    item = new_item(session, radios, unit=unit)
    sent = admin_client.post(
        f"{ITEMS}/{item.id}", data=shown_item_form(item, minimum="2.5")
    )
    session.refresh(item)
    assert sent.status_code == status
    assert item.minimum == kept


def test_reorder_at_is_checked_against_the_unit_sent(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """A change to each with a decimal Reorder at is refused, and the box
    comes back with the keypad of the unit it was checked against."""
    item = new_item(session, radios, unit="m")
    form = shown_item_form(item, unit="each", minimum="2.5")
    refused = admin_client.post(f"{ITEMS}/{item.id}", data=form)
    session.refresh(item)
    assert refused.status_code == 400
    assert problem(refused.text, "minimum") == str(WHOLE_UNITS)
    assert (item.unit, item.minimum) == ("m", None)
    box = read(refused.text).one("input", name="minimum")
    assert box.attrs["inputmode"] == "numeric"
