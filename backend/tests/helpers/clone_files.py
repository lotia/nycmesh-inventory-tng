"""The clone's own text files, read by tests of what is in a clone
(test_contributing.py, test_artifact_links.py)."""

import os
from functools import cache
from pathlib import Path

from pathspec import GitIgnoreSpec

from inventory_tng.local_env import CLONE

# Version control's own stores, which no .gitignore can name.
NOT_OURS = {".git", ".jj"}


@cache
def _ignores(folder: Path) -> GitIgnoreSpec:
    """What folder's own .gitignore leaves out: nothing if it has none."""
    try:
        lines = (folder / ".gitignore").read_text().splitlines()
    except OSError:
        lines = []
    return GitIgnoreSpec.from_lines(lines)


def _ignored(path: Path, *, folder: bool) -> bool:
    """Whether a .gitignore from the clone down to path leaves it out: what
    tools write (caches, coverage, browser test output), secrets (.env,
    .local) and other checkouts (.claude/worktrees), which may hold
    anything."""
    suffix = "/" if folder else ""
    return any(
        _ignores(base).match_file(path.relative_to(base).as_posix() + suffix)
        for base in path.parents
        if base.is_relative_to(CLONE)
    )


def text_files() -> dict[Path, str]:
    """Each text file in the clone that no .gitignore leaves out, read once.
    What a running tool writes and removes as this walks, such as each test
    process's coverage data, is not the project's: a file gone before it is
    read, or one that can't be read, is skipped."""
    found = {}
    for folder, folders, files in os.walk(CLONE):
        here = Path(folder)
        folders[:] = [
            name
            for name in folders
            if name not in NOT_OURS and not _ignored(here / name, folder=True)
        ]
        for name in files:
            path = here / name
            if _ignored(path, folder=False):
                continue
            try:
                found[path] = path.read_text()
            except UnicodeDecodeError, OSError:
                continue
    return found
