"""The items list (catalogue/items_routes.py): search and suggestions,
current and retired, pages, sorting and filtering."""

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng.catalogue.paths import ITEMS
from inventory_tng.models.catalogue import Category, Item
from inventory_tng.web.listing import PER_PAGE

from helpers.items import items_named, new_item
from helpers.markup import (
    addresses,
    hidden_fields,
    links,
    read,
    sorted_columns,
    text,
    words_on,
)
from helpers.rows import add, tag


def test_a_search_forgives_typos_best_first(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    for name in ("LiteAP AC", "LiteBeam 5AC", "Patch cable", "NanoBeam"):
        new_item(session, radios, name)
    found = admin_client.get(ITEMS, params={"q": "litebeam"})
    shown = items_named(found.text)
    assert shown[0] == "LiteBeam 5AC"
    assert "Patch cable" not in shown
    total = session.scalar(
        select(func.count()).select_from(Item).where(Item.retired_at.is_(None))
    )
    said = words_on(found.text)
    assert f"{len(shown)} of {total} items, matching “litebeam”" in said
    assert ("Clear search", "/admin/items") in links(found.text)
    # A typo still finds it.
    misspelt = items_named(admin_client.get(ITEMS, params={"q": "litebeem"}).text)
    assert "LiteBeam 5AC" in misspelt
    assert "Patch cable" not in misspelt


def test_suggestions_come_from_the_filter_the_list_shows(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    beam = new_item(session, radios, f"LiteBeam {tag()}")
    old = new_item(session, radios, f"LiteBeam {tag()}")
    admin_client.post(f"{ITEMS}/{old.id}/retire")
    current = admin_client.get(f"{ITEMS}/suggest", params={"q": "litebem"}).json()
    names = [each["name"] for each in current]
    assert beam.name in names
    assert old.name not in names
    assert current[names.index(beam.name)] == {
        "name": beam.name,
        "hint": radios.name,
        "href": f"{ITEMS}?pick={beam.id}",
    }
    retired = admin_client.get(
        f"{ITEMS}/suggest", params={"q": "litebem", "show": "retired"}
    ).json()
    assert [each["name"] for each in retired] == [old.name]
    assert retired[0]["href"] == f"{ITEMS}?show=retired&pick={old.id}"
    assert admin_client.get(f"{ITEMS}/suggest").json() == []
    # A page size kept, as Find keeps it (inventory-tng-qwb).
    page = admin_client.get(ITEMS, params={"per": "100", "sort": "unit"}).text
    assert read(page).all("input", data_suggest=f"{ITEMS}/suggest?per=100")
    sized = admin_client.get(
        f"{ITEMS}/suggest", params={"q": "litebem", "per": "100"}
    ).json()
    assert sized[0]["href"] == f"{ITEMS}?pick={beam.id}&per=100"


def test_picking_a_suggestion_narrows_the_list_to_it(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    beam = new_item(session, radios, "LiteBeam 5AC")
    new_item(session, radios, "LiteBeam M5")
    picked = admin_client.get(ITEMS, params={"pick": str(beam.id)})
    assert items_named(picked.text) == ["LiteBeam 5AC"]
    assert "matching “LiteBeam 5AC”" in words_on(picked.text)


def test_current_retired_and_all(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    kept = new_item(session, radios, "Kept")
    gone = new_item(session, radios, "Gone")
    admin_client.post(f"{ITEMS}/{gone.id}/retire")
    for show, expected in (
        ("", {"Kept"}),
        ("retired", {"Gone"}),
        ("all", {"Kept", "Gone"}),
    ):
        page = read(admin_client.get(ITEMS, params={"show": show}).text)
        assert {kept.name, gone.name} & set(items_named(page)) == expected
        chosen = show or "current"
        link = f"{ITEMS}{'?show=' + show if show else ''}"
        assert read(page).all("a", href=link, aria_current="page"), chosen
    assert read(admin_client.get(ITEMS, params={"show": "all"}).text).all(
        "span", "Retired", class_="tag"
    )


def test_pages_of_fifty(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    for number in range(PER_PAGE + 5):
        new_item(session, radios, f"Paged {number:03}")
    first = admin_client.get(ITEMS, params={"q": "Paged"})
    assert len(items_named(first.text)) == PER_PAGE
    assert "Page 1 of 2" in text(first)
    assert read(first.text).one("a", rel="next").attrs["href"] == (
        f"{ITEMS}?q=Paged&page=2"
    )
    second = admin_client.get(ITEMS, params={"q": "Paged", "page": "2"}).text
    assert len(items_named(second)) == 5
    # A page past the end, or none, is the last or the first.
    assert "Page 2 of 2" in text(
        admin_client.get(ITEMS, params={"q": "Paged", "page": "9"})
    )
    assert "Page 1 of 2" in text(
        admin_client.get(ITEMS, params={"q": "Paged", "page": "x"})
    )


def test_items_sort_by_each_heading_either_way(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """By name unless a heading sorts them; pressed again, the other way
    (Ali, 2026-11-15, inventory-tng-qwb)."""
    mark = tag()
    antennas = add(session, Category(name=f"Antennas {mark}"))
    for name, category, unit, maker in (
        ("Bravo", radios, "m", "Ubiquiti"),
        ("Alpha", antennas, "each", "Mikrotik"),
        ("Charlie", antennas, "ft", None),
    ):
        add(
            session,
            Item(
                name=f"Sorted {mark} {name}",
                category_id=category.id,
                unit=unit,
                manufacturer=maker,
            ),
        )

    def order(**query: str) -> list[str]:
        page = admin_client.get(ITEMS, params={"per": "all"} | query).text
        return [n.rsplit(" ", 1)[1] for n in items_named(page) if mark in n]

    assert order() == ["Alpha", "Bravo", "Charlie"]
    assert order(sort="name", order="desc") == ["Charlie", "Bravo", "Alpha"]
    # Within a category, by name.
    assert order(sort="category") == ["Alpha", "Charlie", "Bravo"]
    assert order(sort="category", order="desc") == ["Bravo", "Alpha", "Charlie"]
    assert order(sort="unit") == ["Alpha", "Charlie", "Bravo"]
    assert order(sort="part") == ["Alpha", "Bravo", "Charlie"]
    assert order(sort="part", order="desc") == ["Charlie", "Bravo", "Alpha"]
    assert order(sort="nonsense", order="up") == ["Alpha", "Bravo", "Charlie"]
    page = read(admin_client.get(ITEMS).text)
    assert sorted_columns(page) == [
        (
            "ascending",
            "Item",
            "/admin/items?sort=name&order=desc",
        )
    ]
    assert ("Category", "/admin/items?sort=category") in links(page)
    page = read(admin_client.get(ITEMS, params={"sort": "category"}).text)
    assert sorted_columns(page) == [
        (
            "ascending",
            "Category",
            "/admin/items?sort=category&order=desc",
        )
    ]
    # Its column is shown only on a wider screen.
    heading = read(page).one("th", aria_sort="ascending")
    assert {"hidden", "sm:table-cell"} <= set(heading.classes)
    assert ("Item", "/admin/items?sort=name") in links(page)


def test_items_show_25_50_100_or_all(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    mark = tag()
    for number in range(30):
        new_item(session, radios, f"Sized {mark} {number:03}")
    query = {"q": f"Sized {mark}"}
    page = read(admin_client.get(ITEMS, params=query).text)
    assert len(items_named(page)) == 30
    assert ("25", f"{ITEMS}?q=Sized+{mark}&per=25") in links(page)
    fifty = read(page).one("a", "50", aria_current="page")
    assert fifty.attrs["href"] == f"{ITEMS}?q=Sized+{mark}"
    sized = admin_client.get(ITEMS, params=query | {"per": "25"})
    assert len(items_named(sized.text)) == 25
    assert "Page 1 of 2" in text(sized)
    second = admin_client.get(ITEMS, params=query | {"per": "25", "page": "2"}).text
    assert len(items_named(second)) == 5
    every = admin_client.get(ITEMS, params=query | {"per": "all"}).text
    assert len(items_named(every)) == 30
    assert read(every).one("a", "All", aria_current="page")
    assert "Page 1 of" not in every
    # A size not offered is 50; a new search keeps the size.
    assert len(items_named(admin_client.get(ITEMS, params={"per": "7"}).text)) <= 50
    assert ("per", "25") in hidden_fields(
        admin_client.get(ITEMS, params={"per": "25"}).text
    )


def test_items_filter_by_category(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    mark = tag()
    antennas = add(session, Category(name=f"Antennas {mark}"))
    kept = new_item(session, antennas, f"Filtered {mark} dish")
    new_item(session, radios, f"Filtered {mark} radio")
    page = read(admin_client.get(ITEMS, params={"category": str(antennas.id)}).text)
    assert items_named(page) == [kept.name]
    offered = read(page).all("option", value=str(antennas.id))
    assert any("selected" in choice.attrs for choice in offered)
    assert ("Clear", ITEMS) in links(page)
    # Kept by a search, a sort and the pages; the search box's suggestions
    # come from it too.
    found = admin_client.get(
        ITEMS, params={"category": str(antennas.id), "q": f"Filtered {mark}"}
    ).text
    assert [kept.name] == items_named(found)
    kept_in = addresses(found)
    dropped = f"sort=name&order=desc&category={antennas.id}"
    assert not [to for to in kept_in if dropped in to]
    assert [to for to in kept_in if f"category={antennas.id}&sort=name" in to]
    suggest = f"{ITEMS}/suggest?category={antennas.id}"
    assert read(found).all("input", data_suggest=suggest)
    offered = admin_client.get(
        f"{ITEMS}/suggest", params={"q": f"Filtered {mark}", "category": str(radios.id)}
    ).json()
    assert [each["name"] for each in offered] == [f"Filtered {mark} radio"]
    # None in it: it says so; one not a number is no filter.
    empty = add(session, Category(name=f"Empty {mark}"))
    page = text(admin_client.get(ITEMS, params={"category": str(empty.id)}))
    assert "None is in the filter." in page
    assert kept.name in text(
        admin_client.get(ITEMS, params={"category": "x", "per": "all", "q": kept.name})
    )


def test_a_search_sorted_by_a_heading_goes_back_to_best_match(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Best match first while searching; a heading sorts the matches, and
    Best match puts their order back (Ali, 2026-11-15, inventory-tng-qwb)."""
    mark = tag()
    # The closest last by name: a typo's one first.
    for name in ("LiteBeam", "Aaa LiteBeem"):
        new_item(session, radios, f"{name} {mark}")
    query = {"q": f"LiteBeam {mark}"}
    best = admin_client.get(ITEMS, params=query).text
    assert items_named(best)[0] == f"LiteBeam {mark}"
    assert "aria-sort" not in best
    assert read(best).one("a", "Best match", aria_current="page")
    by_name = read(admin_client.get(ITEMS, params=query | {"sort": "name"}).text)
    named = items_named(by_name)
    assert named == sorted(named, key=str.lower)
    assert named[0] == f"Aaa LiteBeem {mark}"
    assert ("Best match", f"{ITEMS}?q=LiteBeam+{mark}") in links(by_name)
    assert not by_name.all("a", "Best match", aria_current="page")
    # A new search from the box is best match first again: no sort sent.
    assert not read(by_name).one("form", role="search").all("*", name="sort")
    # Without a search, no Best match.
    assert "Best match" not in admin_client.get(ITEMS).text


def test_an_empty_list_says_what_to_do_next(admin_client: TestClient) -> None:
    page = text(admin_client.get(ITEMS, params={"q": "zzzqqqxxx"}))
    assert "Nothing matches. Check the spelling" in page
