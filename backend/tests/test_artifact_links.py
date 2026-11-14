"""AGENTS.md rule 11: no file in the clone links to a claude.ai artifact.
Artifacts are tools for the work, deleted when it is done; what was decided
is written down in words instead. Beads are not files here: the rule
covers them."""

import re

import pytest

from inventory_tng.local_env import CLONE

from helpers.clone_files import text_files

pytestmark = pytest.mark.clone

# An artifact's link, as claude.ai gives it, or as Claude Code does (under
# /code). The escaped dot keeps this file from holding what it looks for.
LINK = re.compile(r"claude\.ai/(?:code/)?artifact/")


def test_no_file_links_to_an_artifact() -> None:
    linking = sorted(
        str(path.relative_to(CLONE))
        for path, text in text_files().items()
        if LINK.search(text)
    )
    assert linking == []


@pytest.mark.parametrize(
    "text",
    [
        "https://claude.ai" + "/artifact/0f3c",
        "https://claude.ai" + "/code/artifact/5e1d-77",
    ],
)
def test_each_form_of_link_is_found(text: str) -> None:
    assert LINK.search(text)
