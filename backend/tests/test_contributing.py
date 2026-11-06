"""AGENTS.md rule 10: every development workflow is a mise task named in
CONTRIBUTING.md as `mise run <task>`, and contributors run compose through
`mise run compose`, which picks the container runtime."""

import os
import re
import tomllib
from pathlib import Path

from inventory_tng.local_env import CLONE

from helpers.clone import clone_only

pytestmark = clone_only

TASK_SCRIPTS = CLONE / "mise-tasks"
# A runtime's own compose command (a space, a hyphen or a line break between),
# but not a value, such as INVENTORY_COMPOSE=docker compose.
DIRECT = re.compile(r"(?<![=\"'])\b(?:docker|podman)(?:\s+|-)compose\b")
# Where running compose directly is right: the task that picks the runtime,
# and CI, which runs its own.
ALLOWED = {TASK_SCRIPTS / "compose", CLONE / ".github" / "workflows" / "ci.yml"}
NOT_OURS = {".git", ".jj", ".beads", ".venv", "node_modules", "dist", "__pycache__"}


def tasks() -> set[str]:
    named = set(tomllib.loads((CLONE / "mise.toml").read_text())["tasks"])
    return named | {
        script.name
        for script in TASK_SCRIPTS.iterdir()
        if script.is_file() and os.access(script, os.X_OK)
    }


def text_files() -> list[Path]:
    found = []
    for folder, folders, files in os.walk(CLONE):
        folders[:] = [name for name in folders if name not in NOT_OURS]
        for name in files:
            path = Path(folder, name)
            try:
                found.append((path, path.read_text()))
            except UnicodeDecodeError:
                continue
    return [path for path, _ in found]


def test_every_mise_task_is_in_contributing() -> None:
    text = (CLONE / "CONTRIBUTING.md").read_text()
    missing = {
        task
        for task in tasks()
        if not re.search(rf"mise run {re.escape(task)}(?=[\s`]|$)", text, re.MULTILINE)
    }
    assert missing == set()


def test_compose_runs_through_mise() -> None:
    direct = {
        str(path.relative_to(CLONE)): hits
        for path in text_files()
        if path not in ALLOWED and (hits := DIRECT.findall(path.read_text()))
    }
    assert direct == {}
