"""A box whose problem a template shows is marked invalid when refused
(decision 0019, _described_by.html; inventory-tng-gjuq): every box in a
label that holds a field's problem calls described_by, directly or by a
variable set from it, and a box that names a problem in an
aria-describedby written by hand is aria-invalid with it.

tests/across/test_refused_boxes.py sends each form wrong and reads the
page; this rule holds for a template no test sends yet, such as a new
page's. It cannot see a box whose problem is shown outside its label
and which names no problem at all: a problem beside no box (a section's)
looks the same. Such a box is the route test's to catch."""

import re
from pathlib import Path

from helpers.source import TEMPLATES

# Exact markup on purpose: the rule is about how the templates write
# their boxes, which a rendered page cannot show for every branch.
LABEL = re.compile(r"<label\b.*?</label>", re.DOTALL)
# Exact markup on purpose: as above.
BOX = re.compile(r"<(?:input|select|textarea)\b")
# Exact markup on purpose: as above.
HIDDEN = re.compile(r'\btype="hidden"')
# Exact markup on purpose: as above.
PROBLEM_SHOWN = re.compile(r'_field_problem\.html|class="problem"')
# Exact markup on purpose: as above.
DESCRIBES_PROBLEM = re.compile(r'aria-describedby="[^"]*-problem\b')
# A variable set from described_by, as {% set refused = described_by(...) %}.
SET_FROM = re.compile(r"{%-?\s*set\s+(\w+)\s*=\s*described_by\(")
COMMENT = re.compile(r"{#.*?#}", re.DOTALL)


def tag_at(source: str, start: int) -> str:
    """The tag starting at `start`, to its closing `>`, past any `>` in a
    Jinja expression or statement inside it."""
    at = start
    while at < len(source):
        if source.startswith(("{{", "{%"), at):
            closing = "}}" if source[at + 1] == "{" else "%}"
            at = source.index(closing, at) + 2
        elif source[at] == ">":
            return source[start : at + 1]
        else:
            at += 1
    raise ValueError(f"no end to the tag at {start}")


def marks(tag: str, variables: set[str]) -> bool:
    """Whether the box calls described_by, or prints a variable set from it."""
    return "described_by(" in tag or any(
        re.search(rf"{{{{-?\s*{name}\s*-?}}}}", tag) for name in variables
    )


def unmarked(path: Path) -> list[str]:
    """Each box in `path` refused unmarked: one in a label showing a
    problem that never calls described_by, or one describing a problem by
    hand without aria-invalid."""
    source = COMMENT.sub(lambda found: "\n" * found[0].count("\n"), path.read_text())
    variables = set(SET_FROM.findall(source))
    found: list[str] = []

    def where(at: int) -> str:
        line = source.count("\n", 0, at) + 1
        return f"{path.relative_to(TEMPLATES)}:{line}"

    for label in LABEL.finditer(source):
        if not PROBLEM_SHOWN.search(label[0]):
            continue
        for box in BOX.finditer(source, label.start(), label.end()):
            tag = tag_at(source, box.start())
            if not HIDDEN.search(tag) and not marks(tag, variables):
                found.append(f"{where(box.start())}: no described_by")
    for box in BOX.finditer(source):
        tag = tag_at(source, box.start())
        if DESCRIBES_PROBLEM.search(tag) and "aria-invalid" not in tag:
            found.append(f"{where(box.start())}: describes a problem, not invalid")
    return found


def test_every_box_whose_problem_is_shown_is_marked_when_refused() -> None:
    templates = sorted(TEMPLATES.rglob("*.html"))
    assert templates
    assert [line for path in templates for line in unmarked(path)] == []
