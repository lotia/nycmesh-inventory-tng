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

from inventory_tng.core.local_env import CLONE

from helpers.clone_files import text_files

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


def tasks() -> set[str]:
    named = set(tomllib.loads((CLONE / "mise.toml").read_text())["tasks"])
    return named | {
        script.name
        for script in TASK_SCRIPTS.iterdir()
        if script.is_file() and os.access(script, os.X_OK)
    }


def test_a_file_gone_before_it_is_read_is_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """As another test process's coverage data is, in a parallel run."""
    (tmp_path / "kept.md").write_text("text")
    walked = [(str(tmp_path), [], ["kept.md", "gone"])]
    monkeypatch.setattr(os, "walk", lambda _: iter(walked))
    assert text_files() == {tmp_path / "kept.md": "text"}


def test_what_a_gitignore_leaves_out_is_not_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tools' output, such as pytest's cache, which holds test ids with
    whatever the tests are given (a run of test_artifact_links once failed
    the next on its own links); secrets, such as .env; and other checkouts,
    such as one an agent works apart in, which may hold anything: by every
    .gitignore in the clone, the root's and those below it."""
    top = [".git", ".local", "backend", "frontend"]
    backend = [".pytest_cache", "src"]
    frontend = ["coverage", "node_modules", "test-results", "src"]
    claude = ["worktrees", "skills"]
    beads = ["dolt", "backup"]
    walked = [
        (str(CLONE), top, [".env", "README.md"]),
        (str(CLONE / "backend"), backend, []),
        (str(CLONE / "frontend"), frontend, []),
        (str(CLONE / ".claude"), claude, []),
        (str(CLONE / ".beads"), beads, ["dolt-server.pid", "config.yaml"]),
    ]
    monkeypatch.setattr(os, "walk", lambda _: iter(walked))
    assert list(text_files()) == [CLONE / "README.md", CLONE / ".beads/config.yaml"]
    assert top == ["backend", "frontend"]
    assert (backend, frontend, claude, beads) == (["src"], ["src"], ["skills"], [])


def test_every_mise_task_is_in_contributing() -> None:
    text = (CLONE / "CONTRIBUTING.md").read_text()
    missing = {
        task
        for task in tasks()
        if not re.search(rf"mise run {re.escape(task)}(?=[\s`]|$)", text, re.MULTILINE)
    }
    assert missing == set()


def test_compose_runs_through_mise(clone_files: dict[Path, str]) -> None:
    direct = {
        str(path.relative_to(CLONE)): hits
        for path, text in clone_files.items()
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
