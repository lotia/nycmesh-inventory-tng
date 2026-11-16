"""AGENTS.md rule 11: no file in the clone links to a claude.ai artifact,
or cites a decision by a choice's letter or a question's code. Artifacts
are tools for the work, deleted when it is done; what was decided is
written down in words instead, with who decided and when. Beads are not
files here: the rule covers them."""

import re
from pathlib import Path

import pytest

from inventory_tng.local_env import CLONE

from helpers.clone_files import text_files

pytestmark = pytest.mark.clone

# An artifact's link, as claude.ai gives it, or as Claude Code does (under
# /code). The escaped dot keeps this file from holding what it looks for.
LINK = re.compile(r"claude\.ai/(?:code/)?artifact/")

# A decision cited by a choice's letter, or by a question's code. A capital
# letter after "option" is a choice; a small one only where punctuation
# ends it, as a word goes on ("an option a person sees"). A letter in
# brackets is a choice only after "choice", "option" or "answer", so a
# copyright sign or a list item is left. A question's code ends in its
# choice's letter or a closing bracket, and starts no word, path or hash,
# so a quarter, a test id or base64 is left. The examples below show each.
CITATION = re.compile(
    r"\b[Oo]ption [A-H]\b"
    r"|\b[Oo]ption [a-h](?=[),.;:]|$)"
    r"|\b(?:[Cc]hoice|[Oo]ption|[Aa]nswer)s? \([a-hA-H]\)"
    r"|(?<![\w/+=])Q\d{1,2}(?:[- ][A-H]\b|\))",
    re.MULTILINE,
)


@pytest.fixture(scope="module")
def files() -> dict[Path, str]:
    """The clone's text files, walked once for both rules."""
    return text_files()


def _holding(files: dict[Path, str], pattern: re.Pattern[str]) -> list[str]:
    return sorted(
        str(path.relative_to(CLONE))
        for path, text in files.items()
        if pattern.search(text)
    )


def test_no_file_links_to_an_artifact(files: dict[Path, str]) -> None:
    assert _holding(files, LINK) == []


def test_no_file_cites_a_decision_by_letter(files: dict[Path, str]) -> None:
    assert _holding(files, CITATION) == []


@pytest.mark.parametrize(
    "text",
    [
        "https://claude.ai" + "/artifact/0f3c",
        "https://claude.ai" + "/code/artifact/5e1d-77",
    ],
)
def test_each_form_of_link_is_found(text: str) -> None:
    assert LINK.search(text)


# Each split, so this file holds no citation itself.
@pytest.mark.parametrize(
    "text",
    [
        "(Ali, 2026-11-15, option" + " D)",
        "Option" + " B (Ali, 2026-11-15): saved as typed",
        "as option" + " A's mockups do",
        "(option" + " a)",
        "we took option" + " c.",
        "Ali's choice" + " (c)",
        "answer" + " (B), then",
        "(Ali, 2026-11-12, Q5" + "-A, inventory-tng-c0c.5)",
        "Ali, 2026-11-15 (inventory-tng-ist.1, Q2" + " C): volts",
        "(Ali, 2026-11-15, Q15" + ").",
        "an alias (Q18" + ").",
        "Q10" + " C: what search keeps",
    ],
)
def test_each_lettered_citation_is_found(text: str) -> None:
    assert CITATION.search(text)


@pytest.mark.parametrize(
    "text",
    [
        "an option a person sees",
        "options are listed",
        "the --option flag",
        "Copyright (c) 2026",
        "(a) the first step, (b) the second",
        "const [r, g, b] = (colour.match(/[\\d.]+/g) ?? [])",
        "rank(a) - rank(b)",
        "sha512-WJ7JBp/Q4+z/ImydtI7jS6NVco5",
        "0x1f, #c0ffee, [A-H], [a-h]",
        "SELECT q.id FROM queue q WHERE Q1 = 1",
        "test_Q1_A, test_q2",
        "Q3 2027",
        "4 K, 10G, 40 G, 10Gbps",
        "inventory-tng-c0c.3, Ali, 2026-11-15",
    ],
)
def test_ordinary_text_is_left(text: str) -> None:
    assert not CITATION.search(text)
