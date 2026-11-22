"""Every box a person types into says which keyboard a phone shows for it,
so a box that takes only a number brings up the number keypad by itself
(CONTRIBUTING.md, Styles; inventory-tng-krkc).

The rule, for the server's templates and the scan and register pages: an
input with no type, type text or type number has an inputmode, one of
`numeric` (whole numbers), `decimal` (numbers with a decimal point) or
`text` (anything with letters), or a value worked out as the page renders.
A type number box keeps its type for the browser's own checks of its
limits, and has inputmode numeric too, as iOS shows a keypad only for that.
A box whose type picks its keyboard, such as search or password, is left
out. Stating `text` is the point: a new box makes its author choose, so a
number-only one cannot be missed."""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from inventory_tng.core.local_env import CLONE

from helpers.source import TEMPLATES

pytestmark = pytest.mark.clone

FRONTEND = CLONE / "frontend/src"
# The types of box a person types into whose keyboard the box must name.
TYPED = {None, "text", "number"}
KEYBOARDS = {"numeric", "decimal", "text"}
# A JSX input, from its tag to the end of its self-closing tag, and its
# two attributes, `{` for a mode worked out as it renders.
# Exact markup on purpose: the rule is about how the source writes its
# inputs, which a rendered page cannot show for every branch.
JSX_INPUT = re.compile(r"<input\b(.*?)/>", re.DOTALL)
# Exact markup on purpose: as above.
JSX_TYPE = re.compile(r'\btype="(\w+)"')
# Exact markup on purpose: as above.
JSX_MODE = re.compile(r'\binputMode="?(\w+|\{)')


class Inputs(HTMLParser):
    """The line and attributes of each input in a template."""

    def __init__(self) -> None:
        super().__init__()
        self.found: list[tuple[int, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "input":
            self.found.append((self.getpos()[0], dict(attrs)))


def wrong(kind: str | None, mode: str | None) -> str | None:
    """What is wrong with a box of this type and inputmode, a
    `{{ ... }}`-style mode being one worked out as it renders; None if
    nothing is."""
    if kind not in TYPED:
        return None
    if mode is None:
        return "no inputmode"
    if kind == "number" and mode != "numeric":
        return "type number without inputmode numeric"
    if mode not in KEYBOARDS and "{" not in mode:
        return f"inputmode {mode!r} is none of {sorted(KEYBOARDS)}"
    return None


def template_problems(path: Path) -> list[str]:
    parser = Inputs()
    parser.feed(path.read_text())
    return [
        f"{path.relative_to(CLONE)}:{line}: {problem}"
        for line, attrs in parser.found
        if (problem := wrong(attrs.get("type"), attrs.get("inputmode")))
    ]


def frontend_problems(path: Path) -> list[str]:
    source = path.read_text()
    found = []
    for match in JSX_INPUT.finditer(source):
        kind = JSX_TYPE.search(match.group(1))
        mode = JSX_MODE.search(match.group(1))
        problem = wrong(
            kind.group(1) if kind else None,
            mode.group(1) if mode else None,
        )
        if problem:
            line = source.count("\n", 0, match.start()) + 1
            found.append(f"{path.relative_to(CLONE)}:{line}: {problem}")
    return found


def test_every_template_box_says_its_keyboard() -> None:
    templates = sorted(TEMPLATES.rglob("*.html"))
    assert templates
    assert [p for path in templates for p in template_problems(path)] == []


def test_every_scan_and_register_page_box_says_its_keyboard() -> None:
    sources = sorted(
        path for path in FRONTEND.rglob("*.tsx") if not path.name.endswith(".test.tsx")
    )
    assert sources
    assert [p for path in sources for p in frontend_problems(path)] == []


@pytest.mark.parametrize(
    ("kind", "mode", "problem"),
    [
        (None, None, "no inputmode"),
        ("text", None, "no inputmode"),
        ("number", None, "no inputmode"),
        ("number", "decimal", "type number without inputmode numeric"),
        (
            None,
            "numbers",
            "inputmode 'numbers' is none of ['decimal', 'numeric', 'text']",
        ),
        (None, "numeric", None),
        (None, "{{ mode }}", None),
        ("number", "numeric", None),
        ("search", None, None),
        ("checkbox", None, None),
    ],
)
def test_a_box_is_wrong_only_without_a_keyboard(
    kind: str | None, mode: str | None, problem: str | None
) -> None:
    assert wrong(kind, mode) == problem
