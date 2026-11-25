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
from helpers.markup import box_values, problem, read
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
    item.track_opened = True  # Else it has no Too short to use box.
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


@pytest.mark.parametrize(
    ("headers", "size", "fraction", "highest"),
    [(GERMAN, "1500", "1,5", "1499"), (ENGLISH, "1500", "1.5", "1499")],
)
def test_amounts_in_words_follow_the_locales_separator(
    admin_client: TestClient,
    session: Session,
    radios: Category,
    headers: dict[str, str],
    size: str,
    fraction: str,
    highest: str,
) -> None:
    """On a comma page with a comma, elsewhere with a point, ungrouped on
    both, in the page's words and in a refusal the server writes (Ali,
    2026-11-23, inventory-tng-4mc3; 2026-11-24, inventory-tng-ua04)."""
    item = new_item(session, radios, name="Cat6 cable", unit="m")
    package = add(
        session,
        ItemPackage(
            item_id=item.id,
            name="Reel",
            size=Decimal(1500),
            short_below=Decimal("1.5"),
        ),
    )
    path = f"{ITEMS}/{item.id}"
    page = read(admin_client.get(path, headers=headers).text)
    picker = page.one("select", name="package_id")
    assert picker.one("option", value=str(package.id)).text == f"Reel of {size}"
    refused = admin_client.post(
        path, data=shown_item_form(item, unit="each"), headers=headers
    )
    assert refused.status_code == 400
    assert problem(refused.text, "unit") == (
        f"The Reel package has a fraction ({fraction}): set it to a whole number "
        f"from 1 to {highest} before switching the item count to each."
    )
