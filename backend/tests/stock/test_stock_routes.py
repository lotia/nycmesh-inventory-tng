"""What is where (stock/stock_routes.py)."""

import re
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.models.catalogue import Item
from inventory_tng.models.places import Location
from inventory_tng.stock.paths import COUNTS, NEEDS_COUNTING, REORDER, STOCK

from helpers.markup import (
    Element,
    addresses,
    links,
    options,
    read,
    row_links,
    sorted_columns,
    table_rows,
    text,
    words_on,
)
from helpers.rows import add, tag
from helpers.stock import Place, at_retired_van, received, retire, stocked, write


def test_nothing_yet(admin_client: TestClient) -> None:
    assert "Nothing is recorded anywhere yet." in text(admin_client.get(STOCK))


def test_by_place_and_by_item(admin_client: TestClient, session: Session) -> None:
    place = stocked(session)
    write(session, place)  # Three of them to the van.
    by_place = admin_client.get(STOCK).text
    assert shows(rows_under(by_place, place.store.name), place.item, 2)
    assert shows(rows_under(by_place, place.van.name), place.item, 3)
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


def rows_under(page: str, heading: str) -> list[list[str]]:
    """The rows of the section headed `heading`."""
    (section,) = [s for s in read(page).all("section") if s.one("h2").text == heading]
    return table_rows(section)


def shows(rows: list[list[str]], item: Item, amount: int) -> bool:
    """Whether a row has the item, with this much: the amount first in the
    cell after its name."""
    return any(
        row[:1] == [item.name] and row[1].startswith(f"{amount} each") for row in rows
    )


def reorder_page(admin_client: TestClient) -> str:
    return admin_client.get(REORDER).text


def needs_counting_page(admin_client: TestClient) -> str:
    return text(admin_client.get(NEEDS_COUNTING))


def reorder_shows(page: str, item: Item, amount: int) -> bool:
    """Whether the reorder list has the item, with this much in the stores."""
    return shows(table_rows(page), item, amount)


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
    shown = admin_client.get(NEEDS_COUNTING).text
    assert f"{COUNTS}/{place.store.id}" in addresses(shown)
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
    page = admin_client.get(NEEDS_COUNTING).text
    (van,) = [
        section
        for section in read(page).all("section")
        if section.all("h2", place.van.name)
    ]
    start = f"Start counting {place.van.name}"
    assert (start, f"{COUNTS}/{place.van.id}") in links(van)
    assert f"{start} (retired)" in van.text


def test_the_stock_page_warns_of_stock_at_a_retired_place(
    admin_client: TestClient, session: Session
) -> None:
    assert "retired place" not in text(admin_client.get(STOCK))
    place = at_retired_van(session)
    page = admin_client.get(STOCK).text
    assert "1 retired place holds" in page
    start = f"Start counting {place.van.name}"
    assert (start, f"{COUNTS}/{place.van.id}") in links(page)
    assert read(page).all("h2", f"{place.van.name} (retired)")
    by_item = admin_client.get(STOCK, params={"by": "item"}).text
    assert "1 retired place holds" in by_item
    cells = read(by_item).all("td")
    assert any(cell.text.endswith(f"{place.van.name} (retired)") for cell in cells)


def test_the_stock_page_counts_the_places_short(
    admin_client: TestClient, session: Session
) -> None:
    place = Place(session)
    write(session, place)  # The store is short three.
    write(session, place, lines=[{"item_id": place.cable.id, "quantity": "1"}])
    assert "1 place shows less than nothing" in words_on(admin_client.get(STOCK).text)
    write(
        session,
        place,
        from_location_id=place.van.id,
        to_location_id=place.store.id,
        lines=[{"item_id": place.cable.id, "quantity": "5"}],
    )
    assert "2 places show less than nothing" in words_on(admin_client.get(STOCK).text)


def amounts_shown(page: str) -> list[str]:
    """Each row's amount, in the page's order."""
    return [
        found[1]
        for cell in read(page).all("td")
        if (found := re.match(r"(-?\d+) (?:each|m)", cell.text))
    ]


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
        return amounts_shown(admin_client.get(STOCK, params=query).text)

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
    page = read(admin_client.get(STOCK, params={"by": "item"}).text)
    # Sorted alike in each group's table.
    assert set(sorted_columns(page)) == {
        ("ascending", "Place", f"{STOCK}?by=item&sort=place&order=desc")
    }
    assert ("How much", f"{STOCK}?by=item&sort=quantity") in links(page)
    # Filtered: the items a search finds, and one place.
    assert amounts(q="cable") == ["7"]
    assert amounts(place=str(place.van.id)) == ["3"]
    assert amounts(by="item", place=str(place.store.id)) == ["7", "2"]
    assert amounts(place="x") == ["7", "2", "3"]
    filtered = read(admin_client.get(STOCK, params={"place": str(place.van.id)}).text)
    chosen = [value for value, _, selected in options(filtered) if selected]
    assert str(place.van.id) in chosen
    assert ("Clear", STOCK) in links(filtered)
    nothing = text(admin_client.get(STOCK, params={"q": "zzzqqqxxx"}))
    assert "None is in the filter." in nothing
    # A search that folds to nothing, such as a lone accent, is none.
    accent = admin_client.get(STOCK, params={"q": "\u0301"}).text
    assert ("Clear", STOCK) not in links(accent)


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
    first = admin_client.get(STOCK, params={"per": "25"})
    assert len(amounts_shown(first.text)) == 50
    assert "Page 1 of 2" in text(first)
    second = admin_client.get(STOCK, params={"per": "25", "page": "2"}).text
    assert len(amounts_shown(second)) == 2
    assert len(amounts_shown(admin_client.get(STOCK).text)) == 52
    # By item: two groups, each with every place, on one page.
    by_item = admin_client.get(STOCK, params={"by": "item", "per": "25"})
    assert len(amounts_shown(by_item.text)) == 52
    assert "Page 1 of" not in text(by_item)


def size_row(page: str) -> tuple[str, str]:
    """The page-size row's name and the words before its sizes."""
    (row,) = [
        nav
        for nav in read(page).all("nav", class_="filters")
        if any(href.endswith("per=25") for _, href in links(nav))
    ]
    # The words come first in the row, before its sizes.
    first = row.find(True, recursive=False)
    assert isinstance(first, Element)
    assert first.name == "span"
    return row.attrs["aria-label"], first.text


def test_stock_size_rows_say_they_count_places_or_items(
    admin_client: TestClient,
) -> None:
    """Not lines: each group whole, however many lines (Ali, 2026-11-16,
    inventory-tng-xjh)."""
    by_place = admin_client.get(STOCK).text
    assert size_row(by_place) == ("Places shown", "Places shown:")
    by_item = admin_client.get(STOCK, params={"by": "item"}).text
    assert size_row(by_item) == ("Items shown", "Items shown:")


def test_needs_counting_size_row_says_it_counts_places(
    admin_client: TestClient,
) -> None:
    """As Stock's By place: a place's lines are never split (Ali,
    2026-11-16, inventory-tng-r9ex)."""
    page = admin_client.get(NEEDS_COUNTING).text
    assert size_row(page) == ("Places shown", "Places shown:")


def test_other_lists_size_rows_still_say_show(admin_client: TestClient) -> None:
    assert size_row(admin_client.get(REORDER).text) == ("Rows", "Show")


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
        page = admin_client.get(REORDER, params=query).text
        return [
            name
            for name in row_links(page, "/admin/items")
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
    page = read(admin_client.get(REORDER).text)
    assert ("Usable in the stores", f"{REORDER}?sort=usable") in links(page)
    assert ("25", f"{REORDER}?per=25") in links(page)


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
        return amounts_shown(admin_client.get(NEEDS_COUNTING, params=query).text)

    # The store's cable, then its radios.
    assert amounts() == ["-5", "-3"]
    assert amounts(sort="quantity", order="desc") == ["-3", "-5"]
    assert amounts(sort="item", order="desc") == ["-3", "-5"]
    page = admin_client.get(NEEDS_COUNTING, params={"per": "25"}).text
    assert ("How much", f"{NEEDS_COUNTING}?sort=quantity&per=25") in links(page)


def test_a_filter_leaves_the_whole_stocks_figures_whole(
    admin_client: TestClient, session: Session
) -> None:
    """An item's "in all" and the count of those short say the whole
    stock's, whatever a filter shows (inventory-tng-qwb)."""
    place = stocked(session)  # Five radios at the store.
    write(session, place)  # Three of them to the van.
    write(session, place, lines=[{"item_id": place.cable.id, "quantity": "1"}])
    van = {"by": "item", "place": str(place.van.id)}
    by_item = words_on(admin_client.get(STOCK, params=van).text)
    assert "5 each in all" in by_item
    # The cable is short at the store, not at the van.
    assert "1 item shows less than nothing" in by_item
    at_van = admin_client.get(STOCK, params={"place": str(place.van.id)})
    assert "1 place shows less than nothing" in words_on(at_van.text)
