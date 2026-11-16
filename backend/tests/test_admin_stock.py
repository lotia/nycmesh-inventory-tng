"""What is where (admin/stock.py)."""

import re
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.admin.counts import COUNTS
from inventory_tng.admin.stock import NEEDS_COUNTING, REORDER, STOCK
from inventory_tng.models import Item, Location

from helpers.rows import add, tag
from helpers.sign_in import text
from helpers.stock import Place, at_retired_van, received, retire, stocked, write


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


def reorder_page(admin_client: TestClient) -> str:
    return text(admin_client.get(REORDER))


def needs_counting_page(admin_client: TestClient) -> str:
    return text(admin_client.get(NEEDS_COUNTING))


def reorder_shows(page: str, item: Item, amount: int) -> bool:
    """Whether the reorder list has the item, with this much in the stores."""
    pattern = rf"{re.escape(item.name)}</a></td>\s*<td>{amount} each"
    return re.search(pattern, page) is not None


def test_reorder_lists_what_is_at_or_below_its_level(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)  # Five radios in the store.
    assert place.item.name not in reorder_page(admin_client)  # No level set.
    place.item.minimum = Decimal(4)
    session.flush()
    assert place.item.name not in reorder_page(admin_client)
    place.item.minimum = Decimal(5)
    session.flush()
    page = reorder_page(admin_client)
    assert reorder_shows(page, place.item, 5)
    assert "count first" not in page


def test_reorder_counts_the_stores_and_their_areas_only(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session, "2")
    shelf = add(
        session, Location(name=f"Shelf {tag()}", kind="area", parent_id=place.store.id)
    )
    received(session, place, [{"item_id": place.item.id, "quantity": "2"}])
    write(
        session,
        place,
        to_location_id=shelf.id,
        from_location_id=place.store.id,
        lines=[{"item_id": place.item.id, "quantity": "1"}],
    )
    write(session, place)  # Three from the store to the van: gone from the stores.
    place.item.minimum = Decimal(10)
    session.flush()
    # 4 received, 3 to the van: 1 in the store and its shelf together.
    assert reorder_shows(reorder_page(admin_client), place.item, 1)


def test_reorder_leaves_out_retired_items_and_says_count_first(
    admin_client: TestClient, session: Session
) -> None:
    place = Place(session)
    place.item.minimum = Decimal(1)
    place.cable.minimum = Decimal(1)
    retire(session, place.cable)
    write(session, place)  # Three taken from a store that had none.
    page = reorder_page(admin_client)
    assert place.cable.name not in page
    assert reorder_shows(page, place.item, -3)
    assert "count first" in page


def test_needs_counting_lists_each_place_short(
    admin_client: TestClient, session: Session
) -> None:
    assert "Nothing shows less than nothing." in needs_counting_page(admin_client)
    place = Place(session)
    write(session, place)
    page = needs_counting_page(admin_client)
    assert place.store.name in page
    assert f'href="{COUNTS}/{place.store.id}"' in page
    assert place.van.name not in page  # It has three, which is not short.


def test_count_first_only_for_what_is_short_in_the_stores(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session, "1")
    place.item.minimum = Decimal(10)
    session.flush()
    # Three from the van, which had none, to the store: the van is short.
    write(session, place, from_location_id=place.van.id, to_location_id=place.store.id)
    page = reorder_page(admin_client)
    assert reorder_shows(page, place.item, 4)
    assert "count first" not in page


def test_a_retired_place_short_is_offered_for_a_count(
    admin_client: TestClient, session: Session
) -> None:
    """A save took from it after it was retired."""
    place = Place(session)
    retire(session, place.van)
    write(session, place, from_location_id=place.van.id, to_location_id=place.store.id)
    page = needs_counting_page(admin_client)
    van = page[page.index(f"<h2>{place.van.name}</h2>") :].split("</section>")[0]
    assert f'href="{COUNTS}/{place.van.id}"' in van
    assert f"Start counting {place.van.name}</a> (retired)" in van


def test_the_stock_page_warns_of_stock_at_a_retired_place(
    admin_client: TestClient, session: Session
) -> None:
    assert "retired place" not in text(admin_client.get(STOCK))
    place = at_retired_van(session)
    page = text(admin_client.get(STOCK))
    assert "1 retired place holds" in page
    assert (
        f'<a href="{COUNTS}/{place.van.id}">Start counting {place.van.name}</a>' in page
    )
    assert f"<h2>{place.van.name} (retired)</h2>" in page
    by_item = text(admin_client.get(STOCK, params={"by": "item"}))
    assert "1 retired place holds" in by_item
    assert f"{place.van.name} (retired)</td>" in by_item


def test_the_stock_page_counts_the_places_short(
    admin_client: TestClient, session: Session
) -> None:
    place = Place(session)
    write(session, place)  # The store is short three.
    write(session, place, lines=[{"item_id": place.cable.id, "quantity": "1"}])
    assert re.search(
        r"1 place shows\s+less than nothing", text(admin_client.get(STOCK))
    )
    write(
        session,
        place,
        from_location_id=place.van.id,
        to_location_id=place.store.id,
        lines=[{"item_id": place.cable.id, "quantity": "5"}],
    )
    assert re.search(
        r"2 places show\s+less than nothing", text(admin_client.get(STOCK))
    )


def amounts_shown(page: str) -> list[str]:
    """Each row's amount, in the page's order."""
    return re.findall(r"<td>(-?\d+) (?:each|m)", page)


def test_stock_sorts_within_each_group_and_filters(
    admin_client: TestClient, session: Session
) -> None:
    """Groups in name order; a heading sorts the rows within each; a search
    of the items and a place filter (Ali, 2026-11-15, inventory-tng-qwb)."""
    place = Place(session)
    received(
        session,
        place,
        [
            {"item_id": place.item.id, "quantity": "5"},
            {"item_id": place.cable.id, "quantity": "2"},
        ],
    )
    write(session, place)  # Three radios to the van.

    def amounts(**query: str) -> list[str]:
        return amounts_shown(text(admin_client.get(STOCK, params=query)))

    # The store's cable, then its radios; then the van's radios.
    assert amounts() == ["2", "2", "3"]
    assert amounts(sort="quantity") == ["2", "2", "3"]
    assert amounts(sort="item", order="desc") == ["2", "2", "3"]
    received(session, place, [{"item_id": place.cable.id, "quantity": "5"}])
    assert amounts() == ["7", "2", "3"]
    assert amounts(sort="quantity") == ["2", "7", "3"]
    assert amounts(sort="quantity", order="desc") == ["7", "2", "3"]
    assert amounts(sort="item", order="desc") == ["2", "7", "3"]
    # By item: the cable at the store; the radios at the store, then the van.
    assert amounts(by="item") == ["7", "2", "3"]
    assert amounts(by="item", sort="quantity", order="desc") == ["7", "3", "2"]
    assert amounts(by="item", sort="place", order="desc") == ["7", "3", "2"]
    page = admin_client.get(STOCK, params={"by": "item"}).text
    assert (
        f'<th aria-sort="ascending"><a href="{STOCK}?by=item&amp;sort=place&amp;'
        'order=desc">Place'
    ) in page
    assert f'<a href="{STOCK}?by=item&amp;sort=quantity">How much</a>' in page
    # Filtered: the items a search finds, and one place.
    assert amounts(q="cable") == ["7"]
    assert amounts(place=str(place.van.id)) == ["3"]
    assert amounts(by="item", place=str(place.store.id)) == ["7", "2"]
    assert amounts(place="x") == ["7", "2", "3"]
    filtered = text(admin_client.get(STOCK, params={"place": str(place.van.id)}))
    assert f'<option value="{place.van.id}" selected>' in filtered
    assert f'<a href="{STOCK}">Clear</a>' in filtered
    nothing = text(admin_client.get(STOCK, params={"q": "zzzqqqxxx"}))
    assert "None is in the filter." in nothing


def test_stock_pages_count_groups_and_never_split_one(
    admin_client: TestClient, session: Session
) -> None:
    """Page size counts places (or items), so each group is whole on one
    page (Ali, 2026-11-15, inventory-tng-qwb)."""
    place = Place(session)
    stores = [
        add(session, Location(name=f"Shelf {tag()}", kind="store")) for _ in range(26)
    ]
    for store in stores:
        place.store = store
        received(
            session,
            place,
            [
                {"item_id": place.item.id, "quantity": "1"},
                {"item_id": place.cable.id, "quantity": "1"},
            ],
        )
    first = text(admin_client.get(STOCK, params={"per": "25"}))
    assert len(amounts_shown(first)) == 50
    assert "Page 1 of 2" in first
    second = text(admin_client.get(STOCK, params={"per": "25", "page": "2"}))
    assert len(amounts_shown(second)) == 2
    assert len(amounts_shown(text(admin_client.get(STOCK)))) == 52
    # By item: two groups, each with every place, on one page.
    by_item = text(admin_client.get(STOCK, params={"by": "item", "per": "25"}))
    assert len(amounts_shown(by_item)) == 52
    assert "Page 1 of" not in by_item


def test_reorder_sorts_by_each_heading_and_shows_25_50_100_or_all(
    admin_client: TestClient, session: Session
) -> None:
    """By name unless a heading sorts them (Ali, 2026-11-15,
    inventory-tng-qwb)."""
    place = Place(session)
    received(session, place, [{"item_id": place.cable.id, "quantity": "4"}])
    place.item.minimum = Decimal(1)  # None held: 0.
    place.cable.minimum = Decimal(9)
    session.flush()

    def listed(**query: str) -> list[str]:
        page = text(admin_client.get(REORDER, params=query))
        return [
            name
            for name in re.findall(r'<a href="/admin/items/\d+">([^<]+)</a>', page)
            if name in (place.item.name, place.cable.name)
        ]

    cable, radio = place.cable.name, place.item.name
    assert listed() == [cable, radio]
    assert listed(sort="item", order="desc") == [radio, cable]
    assert listed(sort="usable") == [radio, cable]
    assert listed(sort="usable", order="desc") == [cable, radio]
    assert listed(sort="reorder_at") == [radio, cable]
    assert listed(sort="reorder_at", order="desc") == [cable, radio]
    assert listed(per="all") == [cable, radio]
    page = admin_client.get(REORDER).text
    assert f'<a href="{REORDER}?sort=usable">Usable in the stores</a>' in page
    assert f'<a href="{REORDER}?per=25">25</a>' in page


def test_needs_counting_sorts_within_each_place_and_pages_places(
    admin_client: TestClient, session: Session
) -> None:
    place = Place(session)
    write(session, place)  # Three radios from a store that had none.
    write(
        session,
        place,
        from_location_id=place.store.id,
        to_location_id=place.van.id,
        lines=[{"item_id": place.cable.id, "quantity": "5"}],
    )

    def amounts(**query: str) -> list[str]:
        return amounts_shown(text(admin_client.get(NEEDS_COUNTING, params=query)))

    # The store's cable, then its radios.
    assert amounts() == ["-5", "-3"]
    assert amounts(sort="quantity", order="desc") == ["-3", "-5"]
    assert amounts(sort="item", order="desc") == ["-3", "-5"]
    page = admin_client.get(NEEDS_COUNTING, params={"per": "25"}).text
    assert f'<a href="{NEEDS_COUNTING}?sort=quantity&amp;per=25">How much</a>' in page
