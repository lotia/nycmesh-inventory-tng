"""AGENTS.md rule 10: every development workflow is a mise task named in
CONTRIBUTING.md as `mise run <task>`, and contributors run compose through
`mise run compose`, which picks the container runtime. CI runs each check
directly, not through mise, as a job named for the task that runs it
locally."""

import os
import re
import tomllib
from pathlib import Path

import pytest

from inventory_tng.local_env import CLONE

pytestmark = pytest.mark.clone

TASK_SCRIPTS = CLONE / "mise-tasks"
WORKFLOW = CLONE / ".github" / "workflows" / "ci.yml"
# A job: a key two spaces in, under `jobs:`, whatever characters it uses.
JOB = re.compile(r"^  ([^\s:#]+):", re.MULTILINE)
# A runtime's own compose command (a space, a hyphen or a line break between),
# but not a value, such as INVENTORY_COMPOSE=docker compose.
DIRECT = re.compile(r"(?<![=\"'])\b(?:docker|podman)(?:\s+|-)compose\b")
# Where running compose directly is right: the task that picks the runtime,
# and CI, which runs its own.
ALLOWED = {TASK_SCRIPTS / "compose", WORKFLOW}
NOT_OURS = {".git", ".jj", ".beads", ".venv", "node_modules", "dist", "__pycache__"}


def tasks() -> set[str]:
    named = set(tomllib.loads((CLONE / "mise.toml").read_text())["tasks"])
    return named | {
        script.name
        for script in TASK_SCRIPTS.iterdir()
        if script.is_file() and os.access(script, os.X_OK)
    }


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


def test_a_file_gone_before_it_is_read_is_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """As another test process's coverage data is, in a parallel run."""
    (tmp_path / "kept.md").write_text("text")
    walked = [(str(tmp_path), [], ["kept.md", "gone"])]
    monkeypatch.setattr(os, "walk", lambda _: iter(walked))
    assert text_files() == {tmp_path / "kept.md": "text"}


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
        for path, text in text_files().items()
        if path not in ALLOWED and (hits := DIRECT.findall(text))
    }
    assert direct == {}


def test_ci_runs_every_check_as_a_job_named_for_its_task() -> None:
    """So a failed job names the command that runs it locally, and no check
    runs only on a developer's machine. `check` runs them all, locally."""
    jobs = set(JOB.findall(WORKFLOW.read_text().split("\njobs:\n", 1)[1]))
    assert jobs
    assert jobs - tasks() == set()
    assert {task for task in tasks() if task.startswith("check-")} - jobs == set()
