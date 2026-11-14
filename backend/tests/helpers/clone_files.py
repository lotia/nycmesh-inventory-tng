"""The clone's own text files, read by tests of what is in a clone
(test_contributing.py, test_artifact_links.py)."""

import os
from pathlib import Path

from inventory_tng.local_env import CLONE

# Folders that tools write, not the project.
NOT_OURS = {".git", ".jj", ".beads", ".venv", "node_modules", "dist", "__pycache__"}


def text_files() -> dict[Path, str]:
    """Each text file in the clone, read once. What a running tool writes
    and removes as this walks, such as each test process's coverage data,
    is not the project's: a file gone before it is read, or one that can't
    be read, is skipped."""
    found = {}
    for folder, folders, files in os.walk(CLONE):
        folders[:] = [name for name in folders if name not in NOT_OURS]
        for name in files:
            path = Path(folder, name)
            try:
                found[path] = path.read_text()
            except UnicodeDecodeError, OSError:
                continue
    return found
