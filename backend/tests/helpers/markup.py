"""Readers of a page's HTML, so a test checks what the page offers (a link's
words and address, a button's class) and not how its tags are spelt. The
page is read by BeautifulSoup with lxml's parser. For the markup the
templates write it builds the tree a browser does, though not for all
markup: it keeps a form put straight in a table around its rows, and
closes an ordinary element written as `<span/>`."""

from __future__ import annotations

import re
from collections.abc import Iterator
from html import unescape
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup, NavigableString, Tag
from lxml.html import defs

if TYPE_CHECKING:
    from httpx2 import Response

# Elements that start a line, so their words never run into their
# neighbours': lxml's block elements but del and ins, which a browser shows
# in the line, HTML5's sections, and a line break.
_BREAKS = defs.block_tags - {"del", "ins"} | {
    "article",
    "aside",
    "br",
    "details",
    "figcaption",
    "figure",
    "footer",
    "header",
    "main",
    "nav",
    "section",
    "summary",
}
# Spaces and line breaks HTML runs together; a no-break space is not one.
_SPACES = re.compile(r"[ \t\n\r\f]+")


class Unescaped(str):
    """A page with its escaping undone, for finding its words. Read as HTML,
    its escaping would be undone twice: a quote in a value would end it."""


def text(response: Response) -> Unescaped:
    """The page as read, with Jinja's escaping undone."""
    return Unescaped(unescape(response.text))


def _words(tag: Tag, *, spoken: bool) -> Iterator[str]:
    """Its words, leaving out script, style, comments and what is hidden;
    spoken, also what is hidden from a screen reader (aria-hidden)."""
    for child in tag.contents:
        if isinstance(child, Tag):
            if "hidden" in child.attrs or (
                spoken and child.get("aria-hidden") == "true"
            ):
                continue
            gap = " " if child.name in _BREAKS else ""
            yield gap
            yield from _words(child, spoken=spoken)
            yield gap
        elif (
            isinstance(child, NavigableString)
            and type(child) in Tag.MAIN_CONTENT_STRING_TYPES
        ):
            yield child


class Element(Tag):
    """An element of a read page. Its attributes are plain strings, a class
    too (read as words with `classes`). Its `text` is its own, not bs4's
    get_text."""

    # Read with multi_valued_attributes=None, so never a list of values.
    attrs: dict[str, str]  # ty: ignore[invalid-attribute-override]

    @property
    def text(self) -> str:
        """Its words as a browser lays them out, screen-reader-only ones
        included and hidden ones not: spaces run together, no-break spaces
        kept."""
        return _SPACES.sub(" ", "".join(_words(self, spoken=False))).strip()

    @property
    def spoken(self) -> str:
        """Its words as a screen reader reads them, without what is hidden
        from it, such as an arrow."""
        return _SPACES.sub(" ", "".join(_words(self, spoken=True))).strip()

    @property
    def classes(self) -> list[str]:
        return self.attrs.get("class", "").split()

    def all(self, tag: str, text: str | None = None, **attrs: str) -> list[Element]:
        """Each element of the tag (any, for "*") within this one, in the
        page's order, with these words and attributes. `class_` holds those
        classes; in other names `_` stands for `-` (`aria_sort` means
        aria-sort)."""
        wanted = {
            name.rstrip("_").replace("_", "-"): value for name, value in attrs.items()
        }
        classes = set(wanted.pop("class", "").split())
        return [
            found
            for found in self.find_all(None if tag == "*" else tag)
            if isinstance(found, Element)
            and classes <= set(found.classes)
            and wanted.items() <= found.attrs.items()
            and (text is None or found.text == text)
        ]

    def one(self, tag: str, text: str | None = None, **attrs: str) -> Element:
        """The only element `all` finds."""
        found = self.all(tag, text, **attrs)
        assert len(found) == 1, f"{len(found)} <{tag}> with {text=} {attrs}"
        return found[0]


def read(page: str | Element) -> Element:
    """The page as elements, with its character references read; a part of
    one already read as it is."""
    if isinstance(page, Element):
        return page
    if isinstance(page, Unescaped):
        raise TypeError("read the response's text, not text(response)")
    html = BeautifulSoup(
        page, "lxml", element_classes={Tag: Element}, multi_valued_attributes=None
    ).html
    assert isinstance(html, Element), "not a page"
    return html


def words_on(page: str | Element) -> str:
    """The words a person reads on the page, or in a part of one already
    read, as `Element.text` lays them out: character references read,
    spaces run together, what is hidden left out."""
    return read(page).text


def links(page: str | Element) -> list[tuple[str, str]]:
    """Each link's words and address, in the page's order."""
    return [(a.text, a.attrs.get("href", "")) for a in read(page).all("a")]


def addresses(page: str | Element) -> list[str]:
    """Each link's address, in the page's order."""
    return [address for _, address in links(page)]


def address(link: str) -> tuple[str, dict[str, list[str]]]:
    """A link's path and query, its names in any order."""
    split = urlsplit(link)
    return split.path, parse_qs(split.query)


def row_links(page: str | Element, path: str) -> list[str]:
    """The words of each link in a table's cells to one of the records at
    `path` (path/id), in the page's order: the names a list shows."""
    return [
        a.text
        for a in read(page).all("a")
        if a.find_parent("td")
        and re.fullmatch(rf"{re.escape(path)}/\d+", a.attrs.get("href", ""))
    ]


def table_rows(page: str | Element) -> list[list[str]]:
    """The words of each cell of each table row with cells, in the page's
    order: a row of headings only is left out."""
    return [
        cells
        for row in read(page).all("tr")
        if (cells := [cell.text for cell in row.all("td")])
    ]


def forms(page: str | Element) -> list[tuple[str, str]]:
    """Each form's method and address, in the page's order."""
    return [
        (f.attrs.get("method", ""), f.attrs.get("action", ""))
        for f in read(page).all("form")
    ]


def hidden_fields(page: str | Element) -> list[tuple[str, str]]:
    """Each hidden field's name and value, in the page's order."""
    return [
        (i.attrs.get("name", ""), i.attrs.get("value", ""))
        for i in read(page).all("input", type="hidden")
    ]


def box_values(page: str | Element, name: str) -> list[str | None]:
    """The value of each box named `name`, in the page's order: None for
    one that has none."""
    return [box.attrs.get("value") for box in read(page).all("input", name=name)]


def checked(page: str | Element, name: str) -> list[str | None]:
    """The value of each box named `name` that is checked, in the page's
    order: None for one that has none."""
    return [
        box.attrs.get("value")
        for box in read(page).all("input", name=name)
        if "checked" in box.attrs
    ]


def problem(page: str | Element, field: str) -> str:
    """The words of the problem said under the field named `field`."""
    return read(page).one("span", class_="problem", id=f"{field}-problem").text


def said_beside(page: str | Element, box: Element) -> tuple[str, list[str]]:
    """What an amount's box says beside it (decision 0019, Amounts): the
    words right after it, in its .with-unit wrapper, and the words of each
    element describing it (aria-describedby), in order."""
    after = box.parent
    assert isinstance(after, Element)
    assert "with-unit" in after.classes
    ids = box.attrs.get("aria-describedby", "").split()
    whole = read(page)
    return after.text, [whole.one("*", id=each).text for each in ids]


def unit_beside(page: str | Element, box: Element) -> tuple[str, list[str], str]:
    """said_beside, and the spoken words of the label around the box,
    which name it."""
    label = box.find_parent("label")
    assert isinstance(label, Element)
    return *said_beside(page, box), label.spoken


def described_and_invalid(box: Element) -> tuple[str | None, str | None]:
    """A box's aria-describedby and aria-invalid, None where unset."""
    return box.attrs.get("aria-describedby"), box.attrs.get("aria-invalid")


def marked(boxes: dict[str, Element]) -> tuple[dict[str, str | None], list[str]]:
    """Each box's aria-describedby (None where unset), and which boxes are
    marked invalid, in the order given."""
    return (
        {which: box.attrs.get("aria-describedby") for which, box in boxes.items()},
        [which for which, box in boxes.items() if box.attrs.get("aria-invalid")],
    )


def alerts_on(page: str | Element) -> list[str]:
    """The words of each alert paragraph on the page, in order."""
    return [each.text for each in read(page).all("p", role="alert")]


def units_offered(page: str | Element, name: str) -> dict[str, str | None]:
    """The unit each choice of the list named `name` carries (data-unit),
    by its value: None for one that carries none."""
    return {
        option.attrs["value"]: option.attrs.get("data-unit")
        for option in read(page).one("select", name=name).all("option")
    }


def keypads_offered(page: str | Element, name: str) -> dict[str, str | None]:
    """The keypad each choice of the list named `name` gives the box its
    unit follows (data-inputmode), by its value: None for one that gives
    none."""
    return {
        option.attrs["value"]: option.attrs.get("data-inputmode")
        for option in read(page).one("select", name=name).all("option")
    }


def sorted_columns(page: str | Element) -> list[tuple[str, str, str]]:
    """Each heading a list is sorted by (one a table): which way (its
    aria-sort), its link's words as spoken, and where that link goes."""
    return [
        (th.attrs["aria-sort"], link.spoken, link.attrs.get("href", ""))
        for th in read(page).all("th")
        if "aria-sort" in th.attrs
        for link in [th.one("a")]
    ]


def button(page: str | Element, words: str) -> Element:
    """The only button with these words."""
    return read(page).one("button", words)


def options(page: str | Element) -> list[tuple[str | None, str, bool]]:
    """Each choice in a list: its value, words, and whether it is chosen."""
    return [
        (o.attrs.get("value"), o.text, "selected" in o.attrs)
        for o in read(page).all("option")
    ]
