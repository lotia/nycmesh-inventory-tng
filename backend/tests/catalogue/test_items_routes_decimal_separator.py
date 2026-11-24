"""An item's number boxes in the decimal separator of the browser's
locale (web/decimal_separator.py): read in it, filled in it, and the other
refused with words naming the one to use (inventory-tng-qnwz)."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.catalogue.paths import ITEMS
from inventory_tng.models.catalogue import Category, ItemPackage

from helpers.items import new_item, shown_item_form
from helpers.markup import box_values, problem
from helpers.rows import add

GERMAN = {"Accept-Language": "de-DE,de;q=0.9"}
ENGLISH = {"Accept-Language": "en-US,en;q=0.9"}


@pytest.mark.parametrize(
    ("headers", "read", "refused", "said"),
    [
        (GERMAN, "1,5", "1.5", "Use “,” for decimals, such as 0,5."),
        (ENGLISH, "1.5", "1,5", "Use “.” for decimals, such as 0.5."),
        (GERMAN, "1,5", "1.000", "Type thousands without a separator: 1000."),
        (ENGLISH, "1.5", "1,000", "Type thousands without a separator: 1000."),
    ],
)
def test_a_minimum_is_read_in_the_locales_separator(
    admin_client: TestClient,
    session: Session,
    radios: Category,
    headers: dict[str, str],
    read: str,
    refused: str,
    said: str,
) -> None:
    item = new_item(session, radios, name="Cat6 cable", unit="m")
    path = f"{ITEMS}/{item.id}"
    answer = admin_client.post(
        path, data=shown_item_form(item, minimum=refused), headers=headers
    )
    assert answer.status_code == 400
    assert problem(answer.text, "minimum") == said
    assert item.minimum is None
    saved = admin_client.post(
        path, data=shown_item_form(item, minimum=read), headers=headers
    )
    assert saved.status_code == 303
    assert item.minimum == Decimal("1.5")


def test_an_unchanged_box_posts_back_valid_in_a_comma_locale(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Filled in the page's separator, as it reads it: a package's size
    and too-short length, and the item's minimum, sent back as shown."""
    item = new_item(session, radios, name="Cat6 cable", unit="m")
    item.minimum = Decimal("2.5")
    package = add(
        session,
        ItemPackage(
            item_id=item.id,
            name="Spool",
            size=Decimal("305.5"),
            short_below=Decimal("10.25"),
        ),
    )
    path = f"{ITEMS}/{item.id}"
    page = admin_client.get(path, headers=GERMAN).text
    assert box_values(page, "minimum") == ["2,5"]
    assert box_values(page, "size")[0] == "305,5"
    assert box_values(page, "short_below") == ["10,25"]
    shown = shown_item_form(item, minimum="2,5")
    assert admin_client.post(path, data=shown, headers=GERMAN).status_code == 303
    assert item.minimum == Decimal("2.5")
    settings = admin_client.post(
        f"{path}/packages/{package.id}/settings",
        data={"short_below": "10,25", "minimum_sealed": ""},
        headers=GERMAN,
    )
    assert settings.status_code == 303
    assert package.short_below == Decimal("10.25")
