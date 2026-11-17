"""tests/helpers/markup.py reads a page as a browser lays out its words, so
tests built on it can be trusted."""

import pytest

from helpers.markup import Unescaped, read

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
    page = read("<p>  5&nbsp;m of <a href='/x'>cable</a>,\n left </p>")
    assert page.one("p").text == "5\N{NO-BREAK SPACE}m of cable, left"


@pytest.mark.parametrize("hide", ['aria-hidden="true"', "hidden"])
def test_spoken_leaves_out_what_a_screen_reader_skips(hide: str) -> None:
    page = read(f'<a>Name <span {hide}>▲</span><span class="sr-only">up</span></a>')
    assert (page.one("a").text, page.one("a").spoken) == ("Name ▲up", "Name up")


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
