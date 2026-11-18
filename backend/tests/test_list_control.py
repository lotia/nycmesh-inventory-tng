"""The list control (web/listing.py View, Ali, 2026-11-15,
inventory-tng-qwb): a view read forgivingly from the query string, its
links, and its order and page applied to a select or to rows built in
Python."""

from operator import itemgetter

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql

from inventory_tng.models import Item
from inventory_tng.web.listing import (
    PER_PAGE,
    Asked,
    Sort,
    Sorting,
    View,
    arranged,
    ordered,
)

SQL = Sorting(
    "name",
    {
        "name": Sort.of(func.lower(Item.name)),
        "unit": Sort.of(Item.unit),
        "made": Sort.of(Item.created_at, first="desc"),
    },
)
ROWS = Sorting(
    "name",
    {"name": Sort.keyed(itemgetter(0)), "size": Sort.keyed(itemgetter(1), "desc")},
)


def read(sort: str = "", order: str = "", per: str = "", page: str = "") -> View:
    return View.read("/list", SQL, Asked(sort, order, per, page))


def test_nothing_asked_is_the_lists_own_order_and_fifty() -> None:
    view = read()
    assert (view.by, view.desc, view.per_page, view.page) == ("name", False, 50, 1)
    assert view.link() == "/list"
    assert view.aria_sort("name") == "ascending"
    assert view.aria_sort("unit") is None


@pytest.mark.parametrize(
    "query",
    [
        {"sort": "nonsense"},
        {"sort": "name; drop table item"},
        {"order": "sideways"},
        {"per": "7"},
        {"per": "-25"},
        {"per": "ALL"},
        {"page": "x"},
        {"page": "0"},
        {"page": "99999999999999999999"},
    ],
)
def test_anything_odd_is_the_default(query: dict[str, str]) -> None:
    view = read(**query)
    assert (view.sort, view.by, view.desc, view.per_page, view.page) == (
        None,
        "name",
        False,
        PER_PAGE,
        1,
    )


def test_each_column_sorts_first_its_own_way() -> None:
    assert (read(sort="unit").by, read(sort="unit").desc) == ("unit", False)
    assert (read(sort="made").by, read(sort="made").desc) == ("made", True)
    assert read(sort="made", order="asc").desc is False
    assert read(sort="made", order="sideways").desc is True
    # The way it sorts first is not said in its address.
    assert read(sort="made").link() == "/list?sort=made"
    assert read(sort="made", order="asc").link() == "/list?sort=made&order=asc"


@pytest.mark.parametrize(
    ("per", "size"), [("25", 25), ("50", 50), ("100", 100), ("all", None)]
)
def test_sizes_offered(per: str, size: int | None) -> None:
    view = read(per=per)
    assert view.per_page == size
    assert view.link() == ("/list" if size == PER_PAGE else f"/list?per={per}")


def test_a_heading_pressed_again_sorts_the_other_way() -> None:
    view = read()
    assert view.sort_link("name") == "/list?sort=name&order=desc"
    assert view.sort_link("unit") == "/list?sort=unit"
    assert view.sort_link("made") == "/list?sort=made"
    made = read(sort="made")
    assert made.sort_link("made") == "/list?sort=made&order=asc"
    assert read(sort="made", order="asc").sort_link("made") == "/list?sort=made"
    assert made.aria_sort("made") == "descending"


def test_links_keep_the_rest_and_start_again_at_page_one() -> None:
    view = View.read(
        "/list",
        SQL,
        Asked(sort="unit", per="25", page="3"),
        filters={"category": "4", "place": ""},
        kept={"q": "cable", "show": ""},
    ).counted(100)
    # A filter left blank is not said.
    assert view.filters == (("category", "4"),)
    assert view.page == 3
    assert view.link() == "/list?q=cable&category=4&sort=unit&per=25&page=3"
    assert view.size_link(None) == "/list?q=cable&category=4&sort=unit&per=all"
    assert view.sort_link("name") == "/list?q=cable&category=4&sort=name&per=25"
    assert view.unfiltered_link() == "/list?q=cable&sort=unit&per=25"
    assert view.unfiltered_fields() == [("q", "cable"), ("sort", "unit"), ("per", "25")]
    assert view.filtered("category") == "4"
    assert view.filtered("place") == ""
    assert view.query_string == "?q=cable&category=4&sort=unit&per=25&page=3"


def test_searching_is_best_match_first_until_a_heading_sorts() -> None:
    view = View.read("/list", SQL, searching=True)
    assert view.by is None
    assert view.aria_sort("name") is None
    assert view.sort_link("name") == "/list?sort=name"
    sorted_ = View.read("/list", SQL, Asked(sort="unit"), searching=True)
    assert sorted_.by == "unit"
    assert sorted_.best_match_link() == "/list"


def test_a_page_past_the_last_is_the_last() -> None:
    assert read(page="9").counted(120).page == 3
    assert read(page="9", per="all").counted(120).page == 1
    assert read(page="2").counted(0).page == 1
    assert read(per="25").counted(51).pages == 3


def sql(view: View) -> str:
    query = ordered(select(Item.id), view, Item.id)
    return str(
        query.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def test_a_select_is_ordered_then_paged_with_id_last() -> None:
    said = sql(read().counted(500))
    assert "ORDER BY lower(item.name) ASC, item.id ASC" in said
    assert "LIMIT 50 OFFSET 0" in said
    said = sql(read(sort="made", per="25", page="3").counted(500))
    assert "ORDER BY item.created_at DESC, lower(item.name) ASC, item.id DESC" in said
    assert "LIMIT 25 OFFSET 50" in said
    assert "LIMIT ALL OFFSET 0" in sql(read(per="all").counted(500))


def test_rows_built_in_python_are_sorted_and_paged() -> None:
    rows = [("b", 2), ("a", 2), ("c", 1), ("d", 3)]

    def arrange(
        sort: str = "", order: str = "", per: str = "", page: str = ""
    ) -> tuple[list[tuple[str, int]], View]:
        view = View.read("/list", ROWS, Asked(sort, order, per, page))
        return arranged(rows, view)

    shown, view = arrange()
    assert [name for name, _ in shown] == ["a", "b", "c", "d"]
    assert view.found == 4
    # Biggest first, then by name.
    shown, _ = arrange(sort="size")
    assert [name for name, _ in shown] == ["d", "a", "b", "c"]
    shown, _ = arrange(sort="size", order="asc")
    assert [name for name, _ in shown] == ["c", "a", "b", "d"]
    shown, view = arrange(per="25", page="5")
    assert (len(shown), view.page) == (4, 1)
    # Searching, unsorted: as they came, best match first.
    shown, _ = arranged(rows, View.read("/list", ROWS, searching=True))
    assert shown == rows


def test_rows_built_in_python_in_pages() -> None:
    rows = [(f"{n:03}", n) for n in range(60)]
    shown, view = arranged(rows, View.read("/list", ROWS, Asked(per="25", page="3")))
    assert [name for name, _ in shown] == [f"{n:03}" for n in range(50, 60)]
    assert (view.page, view.pages) == (3, 3)
    shown, _ = arranged(rows, View.read("/list", ROWS, Asked(per="all")))
    assert len(shown) == 60
