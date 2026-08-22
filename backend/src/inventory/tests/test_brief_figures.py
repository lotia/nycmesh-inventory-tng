"""The brief and the command must agree about what the report holds.

`docs/briefs/sheet-classifiers.md` quotes a fenced block per rule, and each
one is pasted from `profile_sheet`. Nothing compared them, so changing one
token in a classifier's vocabulary moved six printed figures and the 17.6%
headline with every check green -- which is the failure the whole brief exists
to stop, reintroduced by the work meant to stop it. One review already caught
the Population block hand-typed with a wider label column than the command
emits.

**Labels and layout, not numbers.** The numbers come from the real export,
which is gitignored and is not ours to publish, so no test can assert them. A
block that has gained, lost or renamed a line is what makes a pasted block
stale, and that needs no workbook: a section over a sheet the test builds
emits the same labels it emits over the real one. The layout is checked by
feeding the brief's own numbers back through `profile_sheet.render`, so that
changing the command's indent or its number column fails here rather than
staleifying every block in the brief silently.
"""

from pathlib import Path

import pytest
from django.conf import settings

from inventory.management.commands.profile_sheet import SECTIONS, Section, render
from inventory.sheet.workbook import CHECKING_IN
from inventory.tests.sheets import notes, sheet_of, submission

BRIEF = Path(settings.REPO_ROOT) / "docs" / "briefs" / "sheet-classifiers.md"

# One submission of each shape the rules read, so every section emits every
# line it can. A sheet that triggered no branch would let a block keep a label
# no code produces any more.
SHEET = sheet_of(
    [
        submission(item="LiteBeam", note="fixing inventory"),
        submission(item="omnitik", note="mesh room 131 broome"),
        submission(item="mast", note="install at NN217"),
        submission(item="NYCM-ER-SXTSQ", note="blue stockings + mesh room"),
        submission(item="LiteBeam", note="returning spare", direction=CHECKING_IN),
        submission(item="", note=""),
    ],
)


def blocks() -> dict[str, list[str]]:
    """Every report block in the brief, by its heading, as its raw lines.

    Walked line by line rather than matched with one expression, because a
    lazy pattern over the whole document pairs a block's closing fence with
    the next block's opening one and reads the prose between them as a block.
    The `bash` block is skipped by its info string: it is the command, not its
    output.
    """
    found: dict[str, list[str]] = {}
    # Keyed on the heading, so a second block under a heading already seen is
    # a fault rather than a silent overwrite: two sections cannot share one,
    # and a brief quoting the same block twice is one of the two going stale
    # unnoticed. Eight sections make that a real possibility.
    seen: list[str] = []
    inside = False
    block: list[str] | None = None
    for line in BRIEF.read_text().split("\n"):
        if line.startswith("```"):
            if inside:
                if block:
                    seen.append(block[0])
                    found[block[0]] = block[1:]
                inside, block = False, None
            else:
                # `inside` is tracked apart from `block`, so that the closing
                # fence of a skipped block is read as a close rather than as
                # the opening of the prose that follows it.
                inside = True
                block = [] if line == "```" else None
        elif inside and block is not None:
            block.append(line)
    assert len(seen) == len(set(seen)), f"the brief quotes two blocks under one heading: {seen}"
    return found


def counts_in(lines: list[str]) -> list[tuple[str, int]]:
    """A quoted block read back as the labels and numbers that made it.

    The right-aligned number comes off the end and the label keeps its indent,
    because the indent is what says a line is a subset rather than a share --
    a review found one of those wrong too.
    """
    read = []
    for line in lines:
        label, _, count = line.rpartition("  ")
        read.append((label.rstrip()[2:], int(count)))
    return read


@pytest.mark.parametrize("section", SECTIONS, ids=lambda s: s(SHEET)[0])
def test_the_brief_quotes_the_lines_this_section_emits(section: Section) -> None:
    heading, counted = section(SHEET)
    quoted = blocks().get(heading)

    assert quoted is not None, f"the brief quotes no block headed {heading!r}"
    assert [label for label, _ in counts_in(quoted)] == [label for label, _ in counted]


@pytest.mark.parametrize("section", SECTIONS, ids=lambda s: s(SHEET)[0])
def test_the_brief_is_laid_out_the_way_the_command_lays_it_out(section: Section) -> None:
    """The brief's own numbers, fed back through the command's renderer, have
    to come out as the brief has them. Rebuilding the layout here instead
    would agree with itself while `render` changed underneath both, and every
    pasted block would go stale with this file green.
    """
    heading = section(SHEET)[0]
    quoted = blocks()[heading]

    assert render(heading, counts_in(quoted)) == [heading, *quoted]


def test_the_brief_quotes_no_block_no_section_produces() -> None:
    """The other direction: a section deleted or renamed leaves its block
    behind, and a block nothing produces is a hand count wearing the clothes
    of a reproducible one.
    """
    assert set(blocks()) == {section(SHEET)[0] for section in SECTIONS}


def test_every_section_reaches_the_sheet_this_checks_against() -> None:
    """Each section counts something over the fixture, so none of them is
    being compared against a block it never looked at the data to produce.

    Not stronger than that on purpose: two of the lines are self-checks whose
    whole point is to read zero, so "every line is exercised" is not a
    property this fixture can have.
    """
    assert all(any(count for _, count in section(SHEET)[1]) for section in SECTIONS)


def test_it_is_the_committed_brief_being_read() -> None:
    """A path that quietly resolved to nothing would pass every test above by
    finding no blocks to disagree with.
    """
    assert BRIEF.is_file()
    assert "## 1. Item string to item" in BRIEF.read_text()


def test_a_section_over_no_submissions_still_names_its_lines() -> None:
    """The labels are the contract, so they cannot depend on the data. A
    section that dropped a line when a count was zero would make the brief
    correct only for the export that produced it.
    """
    for section in SECTIONS:
        empty = [label for label, _ in section(notes())[1]]
        full = [label for label, _ in section(SHEET)[1]]
        assert empty == full


@pytest.mark.parametrize("section", SECTIONS, ids=lambda s: s(SHEET)[0])
def test_every_indent_is_one_the_contract_allows(section: Section) -> None:
    """Three sections got this wrong in review, and the block-matching tests
    above cannot see it, so the rule on `Report` is checked here rather than
    described there: a line sits at 0, or
    beside the line above it, or two past an ancestor as a share of it, or one
    past the line above it as a subset of that line.
    """
    ancestors = [0]
    previous = 0
    for label, _ in section(SHEET)[1]:
        indent = len(label) - len(label.lstrip())
        allowed = {0, previous, previous + 1} | {depth + 2 for depth in ancestors}
        assert indent in allowed, f"{label!r} is indented {indent}, and {sorted(allowed)} were the options"
        ancestors = [depth for depth in ancestors if depth < indent] + [indent]
        previous = indent
