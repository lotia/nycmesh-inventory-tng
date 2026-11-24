"""tests/helpers/markup.py reads a page as a browser lays out its words, so
tests built on it can be trusted."""

import pytest

from helpers.markup import (
    Unescaped,
    addresses,
    checked,
    problem,
    read,
    row_links,
    table_rows,
    words_on,
)

pytestmark = pytest.mark.helper


def test_text_leaves_out_script_style_and_comments() -> None:
    page = read(
        "<p>Ready<script>let shown = 0</script><style>p {}</style><!-- note --></p>"
    )
    assert page.one("p").text == "Ready"


@pytest.mark.parametrize(
    "markup",
    [
        "<div><p>one</p><p>two</p></div>",
        "<div>one<br>two</div>",
        "<div><table><tr><td>one</td><td>two</td></tr></table></div>",
        "<div><ul><li>one</li><li>two</li></ul></div>",
        "<div><section>one</section>two</div>",
    ],
    ids=["paragraphs", "line break", "cells", "list items", "section"],
)
def test_text_puts_a_space_between_blocks(markup: str) -> None:
    assert read(markup).one("div").text == "one two"


def test_text_runs_inline_words_together_and_keeps_no_break_spaces() -> None:
    page = read(
        "<p>  5&nbsp;m of <a href='/x'>cable</a>,\n left<del>s</del><ins>!</ins></p>"
    )
    assert page.one("p").text == "5\N{NO-BREAK SPACE}m of cable, lefts!"


@pytest.mark.parametrize(
    ("hide", "shown"),
    [('aria-hidden="true"', "Name ▲up"), ("hidden", "Name up")],
)
def test_text_leaves_out_what_is_hidden_and_spoken_also_what_is_not_read(
    hide: str, shown: str
) -> None:
    page = read(f'<a>Name <span {hide}>▲</span><span class="sr-only">up</span></a>')
    assert (page.one("a").text, page.one("a").spoken) == (shown, "Name up")


def test_row_links_counts_each_link_in_a_cell_once() -> None:
    page = (
        '<a href="/r/1">Out</a><table><tr><td><table><tr><td>'
        '<a href="/r/2">In</a> <a href="/r/2/edit">Edit</a>'
        "</td></tr></table></td></tr></table>"
    )
    assert row_links(page, "/r") == ["In"]


def test_table_rows_reads_each_rows_cells_and_leaves_out_headings() -> None:
    page = (
        "<table><tr><th>Item</th><th>Count</th></tr>"
        "<tr><td>Cable</td><td>2&nbsp;m</td></tr><tr><td>Radio</td></tr></table>"
    )
    assert table_rows(page) == [["Cable", "2\N{NO-BREAK SPACE}m"], ["Radio"]]


def test_one_refuses_none_or_several() -> None:
    page = read("<p>a</p><p>b</p>")
    for tag in ("p", "li"):
        with pytest.raises(AssertionError):
            page.one(tag)


def test_all_matches_a_subset_of_classes_and_exact_attributes() -> None:
    page = read('<b class="tag block wide" id="x">a</b><b class="tag" id="xy">b</b>')
    assert [b.text for b in page.all("b", class_="block tag")] == ["a"]
    assert [b.text for b in page.all("*", id="x")] == ["a"]


def test_read_refuses_a_page_unescaped_already() -> None:
    with pytest.raises(TypeError):
        read(Unescaped("<p>12&quot; mast</p>"))


def test_words_on_reads_a_pages_words_with_its_references_read() -> None:
    page = "<html><body><h1>Ada &amp; Ben</h1><p hidden>gone</p>\n<p>12&quot;  mast</p>"
    assert words_on(page) == 'Ada & Ben 12" mast'
    assert words_on(read(page).one("h1")) == "Ada & Ben"


def test_addresses_reads_each_links_address_with_its_references_read() -> None:
    page = '<a href="/a?x=1&amp;y=2">A</a><link href="/s.css"><a>none</a>'
    assert addresses(page) == ["/a?x=1&y=2", ""]


def test_checked_reads_only_the_checked_boxes_of_that_name() -> None:
    page = (
        '<input type="radio" name="u" value="a">'
        '<input checked value="b" type="radio" name="u">'
        '<input type="checkbox" name="u" checked><input name="v" value="c" checked>'
        '<select name="u"><option value="d" selected>d</option></select>'
    )
    assert checked(page, "u") == ["b", None]


def test_problem_reads_the_words_said_under_one_field() -> None:
    page = (
        '<span class="problem" id="name-problem">Taken &amp; <b>kept</b></span>'
        '<span class="problem" id="size-problem">Too big</span>'
    )
    assert problem(page, "name") == "Taken & kept"
    with pytest.raises(AssertionError):
        problem(page, "unit")
