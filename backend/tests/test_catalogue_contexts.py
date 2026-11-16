"""A message has a context only from the fixed list in CONTRIBUTING.md
(Phrases), on either side (docs/decisions/0022)."""

import re
from pathlib import Path

import pytest
from babel.messages.pofile import read_po

from inventory_tng.local_env import CLONE

pytestmark = pytest.mark.clone

CATALOGUES = [
    CLONE / "backend/src/inventory_tng/locales/en/LC_MESSAGES/messages.po",
    CLONE / "frontend/src/locales/en.po",
]


def listed() -> set[str]:
    """The contexts in the table under Phrases: each row's first cell."""
    phrases = (CLONE / "CONTRIBUTING.md").read_text().split("\n## Phrases\n")[1]
    section = phrases.split("\n## ")[0]
    return set(re.findall(r"^\| `([^`]+)` \|", section, re.MULTILINE))


def contexts(catalogue: Path) -> set[str]:
    with catalogue.open("rb") as po:
        return {message.context for message in read_po(po) if message.context}


def test_the_list_is_there() -> None:
    assert {"place", "mail header", "action", "state"} <= listed()


def test_a_catalogues_contexts_are_read(tmp_path: Path) -> None:
    po = tmp_path / "en.po"
    po.write_text('msgctxt "place"\nmsgid "To"\nmsgstr ""\n\nmsgid "Save"\nmsgstr ""\n')
    assert contexts(po) == {"place"}


@pytest.mark.parametrize("catalogue", CATALOGUES, ids=["server", "scan page"])
def test_every_context_is_from_the_list(catalogue: Path) -> None:
    assert contexts(catalogue) - listed() == set()
