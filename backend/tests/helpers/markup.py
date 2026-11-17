"""Readers of a page's HTML, so a test checks what the page offers (a link's
words and address, a button's class) and not how its tags are spelt. The
page is read by BeautifulSoup with lxml's parser. For the markup the
templates write it builds the tree a browser does, though not for all
markup: it keeps a form put straight in a table around its rows, and
closes an ordinary element written as `<span/>`."""

from __future__ import annotations

import re
from collections.abc import Iterator

from bs4 import BeautifulSoup, NavigableString, Tag
from lxml.html import defs

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


def links(page: str | Element) -> list[tuple[str, str]]:
    """Each link's words and address, in the page's order."""
    return [(a.text, a.attrs.get("href", "")) for a in read(page).all("a")]


def row_links(page: str | Element, path: str) -> list[str]:
    """The words of each link in a table's cells to one of the records at
    `path` (path/id), in the page's order: the names a list shows."""
    return [
        a.text
        for a in read(page).all("a")
        if a.find_parent("td")
        and re.fullmatch(rf"{re.escape(path)}/\d+", a.attrs.get("href", ""))
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
