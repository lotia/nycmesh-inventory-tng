"""AGENTS.md rule 11: no file in the clone links to a claude.ai artifact.
Artifacts are tools for the work, deleted when it is done; what was decided
is written down in words instead. Beads are not files here: the rule
covers them."""

import pytest

from inventory_tng.local_env import CLONE

from helpers.clone_files import text_files

pytestmark = pytest.mark.clone

# Built from parts, so this file does not hold what it looks for.
LINK = "claude.ai" + "/artifact"


def test_no_file_links_to_an_artifact() -> None:
    linking = sorted(
        str(path.relative_to(CLONE))
        for path, text in text_files().items()
        if LINK in text
    )
    assert linking == []
