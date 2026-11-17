"""Readers of a page's HTML, so a test checks what the page offers (a link's
words and address, a button's class) and not how its tags are spelt."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

# Elements that never have an end tag.
_VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta"}
    | {"source", "track", "wbr"}
)
# An element of the key ends one still open of these, as a browser reads it.
_ENDS = {
    "li": {"li"},
    "option": {"option"},
    "p": {"p"},
    "td": {"td", "th"},
    "th": {"td", "th"},
    "tr": {"tr", "td", "th"},
}
# Spaces and line breaks HTML runs together; a no-break space is not one.
_SPACES = re.compile(r"[ \t\n\r\f]+")


class Unescaped(str):
    """A page with its escaping undone, for finding its words. Read as HTML,
    its escaping would be undone twice: a quote in a value would end it."""


@dataclass
class Element:
    tag: str
    attrs: dict[str, str | None]
    children: list[Element | str] = field(default_factory=list)

    @property
    def text(self) -> str:
        """Its words as shown, spaces run together and no-break spaces kept."""
        return _SPACES.sub(" ", "".join(self._words(spoken=False))).strip()

    @property
    def spoken(self) -> str:
        """Its words as a screen reader reads them, without what is hidden
        from it (aria-hidden), such as an arrow."""
        return _SPACES.sub(" ", "".join(self._words(spoken=True))).strip()

    def _words(self, *, spoken: bool) -> list[str]:
        return [
            part
            for child in self.children
            if isinstance(child, str)
            or not (spoken and child.attrs.get("aria-hidden") == "true")
            for part in (
                [child] if isinstance(child, str) else child._words(spoken=spoken)
            )
        ]

    @property
    def classes(self) -> list[str]:
        return str(self.attrs.get("class") or "").split()

    def all(self, tag: str, text: str | None = None, **attrs: str) -> list[Element]:
        """Each element of the tag (any, for "*") within this one, in the
        page's order, with these words and attributes: `class_` holds those
        classes, and `aria_sort` means aria-sort."""
        wanted = {
            name.rstrip("_").replace("_", "-"): value for name, value in attrs.items()
        }
        found = []
        for child in self.children:
            if isinstance(child, str):
                continue
            if (
                tag in ("*", child.tag)
                and (text is None or child.text == text)
                and all(
                    set(value.split()) <= set(child.classes)
                    if name == "class"
                    else child.attrs.get(name) == value
                    for name, value in wanted.items()
                )
            ):
                found.append(child)
            found.extend(child.all(tag, text, **attrs))
        return found

    def one(self, tag: str, text: str | None = None, **attrs: str) -> Element:
        """The only element `all` finds."""
        found = self.all(tag, text, **attrs)
        assert len(found) == 1, f"{len(found)} <{tag}> with {text=} {attrs}"
        return found[0]


class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page = Element("page", {})
        self.open = [self.page]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.open[-1].tag in _ENDS.get(tag, ()):
            self.open.pop()
        element = Element(tag, dict(attrs))
        self.open[-1].children.append(element)
        if tag not in _VOID:
            self.open.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.open[-1].children.append(Element(tag, dict(attrs)))

    def handle_endtag(self, tag: str) -> None:
        for depth in range(len(self.open) - 1, 0, -1):
            if self.open[depth].tag == tag:
                del self.open[depth:]
                return

    def handle_data(self, data: str) -> None:
        self.open[-1].children.append(data)


def read(page: str | Element) -> Element:
    """The page as elements, with its character references read; a part of
    one already read as it is."""
    if isinstance(page, Element):
        return page
    if isinstance(page, Unescaped):
        raise TypeError("read the response's text, not text(response)")
    reader = _Reader()
    reader.feed(page)
    reader.close()
    return reader.page


def links(page: str | Element) -> list[tuple[str, str]]:
    """Each link's words and address, in the page's order."""
    return [(a.text, a.attrs.get("href") or "") for a in read(page).all("a")]


def forms(page: str | Element) -> list[tuple[str, str]]:
    """Each form's method and address, in the page's order."""
    return [
        (f.attrs.get("method") or "", f.attrs.get("action") or "")
        for f in read(page).all("form")
    ]


def hidden(page: str | Element) -> list[tuple[str, str]]:
    """Each hidden field's name and value, in the page's order."""
    return [
        (i.attrs.get("name") or "", i.attrs.get("value") or "")
        for i in read(page).all("input", type="hidden")
    ]


def sorted_columns(page: str | Element) -> list[tuple[str | None, str, str]]:
    """Each heading a list is sorted by (one a table): which way (its
    aria-sort), its link's words as spoken, and where that link goes."""
    return [
        (th.attrs["aria-sort"], link.spoken, link.attrs.get("href") or "")
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
