"""A count form's boxes in the decimal separator of the browser's locale
(web/decimal_separator.py): filled in it, and sent back unchanged, saved
as shown (inventory-tng-qnwz)."""

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.stock.ledger import LOOSE, Entry
from inventory_tng.stock.moments import expected
from inventory_tng.stock.paths import COUNTS

from helpers.markup import read
from helpers.stock import Spools, count_form

GERMAN = {"Accept-Language": "de"}


def test_a_count_sent_back_as_filled_in_a_comma_locale_saves(
    admin_client: TestClient, session: Session
) -> None:
    spools = Spools(session)
    spools.write(
        {"kind": "receipt", "to_location_id": spools.store.id},
        Entry(spools.cable.id, Decimal("10.5"), LOOSE, LOOSE),
    )
    path = f"{COUNTS}/{spools.store.id}"
    page = admin_client.get(path, headers=GERMAN)
    boxes = {
        box.attrs["name"]: box.attrs.get("value", "")
        for box in read(page.text).all("input", inputmode="decimal")
    }
    assert "10,5" in boxes.values()
    saved = admin_client.post(
        path, data=count_form(page) | boxes, headers=GERMAN, follow_redirects=False
    )
    assert saved.status_code == 303
    assert expected(session, spools.store.id)[spools.cable.id] == Decimal("620.5")
