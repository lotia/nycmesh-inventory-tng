"""What is where (admin/stock.py)."""

import re

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_ledger import Place, stocked, write
from test_sign_in import text

from inventory_tng.admin.stock import STOCK


def test_nothing_yet(admin_client: TestClient) -> None:
    assert "Nothing is recorded anywhere yet." in text(admin_client.get(STOCK))


def test_by_place_and_by_item(admin_client: TestClient, session: Session) -> None:
    place = stocked(session)
    write(session, place)  # Three of them to the van.
    by_place = text(admin_client.get(STOCK))
    store_part = by_place[by_place.index(place.store.name) :]
    # Whatever the indentation.
    assert re.search(rf"{re.escape(place.item.name)}</td>\s*<td>2 each", store_part)
    van_part = by_place[by_place.index(place.van.name) :]
    assert "3 each" in van_part
    by_item = text(admin_client.get(STOCK, params={"by": "item"}))
    assert "5 each in all" in by_item


def test_less_than_nothing_asks_for_a_count(
    admin_client: TestClient, session: Session
) -> None:
    place = Place(session)
    write(session, place)  # Three taken from a store that had none.
    page = text(admin_client.get(STOCK))
    assert "count it" in page
    assert "less than nothing somewhere" in page
