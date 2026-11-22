"""A message names a place without a preposition chosen by its kind, and
tells a translator the place can be any kind, on either side
(CONTRIBUTING.md, Phrases); and the scan page joins a place's path as
the server does."""

import re
from collections.abc import Iterable
from functools import cache
from pathlib import Path

import pytest
from babel.messages.catalog import Message

from inventory_tng.core.local_env import CLONE
from inventory_tng.core.phrases import ENGLISH_PO, PATH_BETWEEN, read

pytestmark = pytest.mark.clone

# Each side's catalogue, how it writes a value, and the values that are
# always a place's name.
SIDES = {
    "server": (
        ENGLISH_PO,
        r"%\((\w+)\)s",
        {"place", "places", "there", "from_place", "to_place", "parent"},
    ),
    "scan page": (
        CLONE / "frontend/src/locales/en.po",
        r"\{(\w+)[,}]",
        {"place", "from", "to"},
    ),
}

# The prepositions English chooses by a place's kind.
BY_KIND = r"\b(?:in|on|into|onto|inside)\s+"


def placed(messages: Iterable[Message], value: str, places: set[str]) -> list[Message]:
    """The messages that say a place's name."""
    return [
        message
        for message in messages
        if message.id and places & set(re.findall(value, str(message.id)))
    ]


def by_kind(message: Message, value: str, places: set[str]) -> bool:
    """Whether a preposition chosen by the place's kind comes before one."""
    return not places.isdisjoint(
        re.findall(BY_KIND + value, str(message.id), re.IGNORECASE)
    )


@cache
def side_placed(side: str) -> list[Message]:
    catalogue, value, places = SIDES[side]
    return placed(read(catalogue), value, places)


def told(message: Message) -> bool:
    return "of any kind" in " ".join(message.auto_comments)


def test_a_place_is_found_and_judged(tmp_path: Path) -> None:
    po = tmp_path / "en.po"
    po.write_text(
        '#. Translators: %(place)s is a place of any kind.\nmsgid "At %(place)s"\n'
        'msgstr ""\n\nmsgid "On %(place)s"\nmsgstr ""\n\n'
        'msgid "In %(unit)s"\nmsgstr ""\n'
    )
    value, places = SIDES["server"][1:]
    at, on = placed(read(po), value, places)
    assert (told(at), by_kind(at, value, places)) == (True, False)
    assert (told(on), by_kind(on, value, places)) == (False, True)


@pytest.mark.parametrize("side", SIDES)
def test_no_place_follows_a_preposition_chosen_by_its_kind(side: str) -> None:
    _, value, places = SIDES[side]
    messages = side_placed(side)
    assert messages
    assert [str(m.id) for m in messages if by_kind(m, value, places)] == []


@pytest.mark.parametrize("side", SIDES)
def test_a_translator_is_told_a_place_can_be_any_kind(side: str) -> None:
    assert [m.id for m in side_placed(side) if not told(m)] == []


def test_the_scan_page_joins_a_path_as_the_server_does() -> None:
    # Exact markup on purpose: the scan page's source, which a test of the
    # server cannot run.
    search = (CLONE / "frontend/src/scan/search.ts").read_text()
    assert f'.join("{PATH_BETWEEN}")' in search
