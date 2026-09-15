"""Pages that have a size budget, held to it.

scripts/check-docs.budget is the table, and says why each page has a number
and why it is that number; check-docs.sh --budget is the entry point. This is
the reader.

TWO MEASURES. The whole page, in lines, which is what a reader scrolling
through it experiences. And, for a page that is meant to be an outline, each
H2 section -- the heading and everything under it to the next H2, subsections
included -- because a page inside its total with one section holding a whole
topic is the failure this exists to name, and the total alone would let it
through. What comes before the first H2 is measured too. A heading inside a
fenced block is code and does not start a section; review_cycle.py's `hidden`
is what tells the two apart.

LINES RATHER THAN WORDS, because lines are what the reader sees and what a
diff shows, and a budget in words invites the fix of joining lines. Blank
lines count: they are part of how long the page is.

Nothing outside the standard library, as with every reader beside this one;
check-config.py says why that is not a choice.
"""

import os
import pathlib
import re

from review_cycle import hidden

BUDGET = pathlib.Path(os.environ["BUDGET"])
H2 = re.compile(r"^##[ \t]+(.*?)[ \t]*#*[ \t]*$")


def sections(lines: list[str]) -> list[tuple[str, int]]:
    """Each H2 section of a page as (its heading, how many lines it holds)."""
    out: list[tuple[str, int]] = []
    title, count = "before the first section", 0
    shown = hidden(lines)
    for number, line in enumerate(lines):
        heading = None if number in shown else H2.match(line)
        if heading:
            out.append((title, count))
            title, count = heading.group(1), 0
        count += 1
    out.append((title, count))
    return out


def rows(text: str) -> list[tuple[str, int, int | None]]:
    """The table: a path, a page budget, and a section budget or none."""
    out: list[tuple[str, int, int | None]] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split()
        if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts[1:]):
            # A failure and not a note: a row this cannot read is a page this
            # is not holding, and a run that said "within budget" over it
            # would be green because the guard was off, which is the same
            # silence relay guards against one level up.
            print("fail a budget row is not in the documented form: " + line[:60])
            print("note   a path, the page's line budget, and optionally a section's")
            continue
        out.append((parts[0], int(parts[1]), int(parts[2]) if len(parts) == 3 else None))
    return out


for name, page_budget, section_budget in rows(BUDGET.read_text()):
    page = pathlib.Path(name)
    if not page.is_file():
        print(f"fail {name} has a budget and is not there")
        continue
    lines = page.read_text(encoding="utf-8").splitlines()
    if len(lines) > page_budget:
        print(f"fail {name} runs to {len(lines)} lines; its budget is {page_budget}")
    if section_budget is None:
        continue
    for title, count in sections(lines):
        if count > section_budget:
            print(f'fail {name}: "{title}" runs to {count} lines; a section there may hold {section_budget}')
