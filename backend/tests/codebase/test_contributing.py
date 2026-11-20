"""AGENTS.md rule 10: every development workflow is a mise task with a
description, so that `mise tasks` says what each does, every `mise run`
the docs give names one, and contributors run compose through
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
# A task script's description, in either header form mise reads.
DESCRIPTION = re.compile(
    r"^#(?:MISE| \[MISE\]) description\s*=\s*([\"'])\S.*\1\s*$", re.MULTILINE
)
# A task named as a command, in a doc, a comment or a script.
RUN = re.compile(r"\bmise run ([a-z][a-z0-9-]*)")
# Decision records keep the history, which may name a task since renamed.
DECISIONS = CLONE / "docs" / "decisions"
# Where running compose directly is right: the task that picks the runtime,
# and CI, which runs its own.
ALLOWED = {TASK_SCRIPTS / "compose", WORKFLOW}


def task_scripts() -> list[Path]:
    return [
        script
        for script in TASK_SCRIPTS.iterdir()
        if script.is_file() and os.access(script, os.X_OK)
    ]


def toml_tasks() -> dict[str, object]:
    return tomllib.loads((CLONE / "mise.toml").read_text())["tasks"]


def tasks() -> set[str]:
    return set(toml_tasks()) | {script.name for script in task_scripts()}


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


def test_every_mise_task_has_a_description() -> None:
    """So `mise tasks` lists each with what it does: CONTRIBUTING.md names
    only the everyday ones."""
    named = toml_tasks()
    # A task given as a bare command or a list of them has no description.
    undescribed = {
        name
        for name, task in named.items()
        if not (isinstance(task, dict) and task.get("description"))
    }
    undescribed |= {
        script.name
        for script in task_scripts()
        if not DESCRIPTION.search(script.read_text())
    }
    assert named
    assert undescribed == set()


def test_every_mise_run_names_a_task(clone_files: dict[Path, str]) -> None:
    """So a renamed task's old name does not linger where people read it."""
    known = tasks()
    unknown = {
        str(path.relative_to(CLONE)): names
        for path, text in clone_files.items()
        if not path.is_relative_to(DECISIONS)
        and (names := set(RUN.findall(text)) - known)
    }
    assert unknown == {}


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
    known = tasks()
    assert jobs
    assert jobs - known == set()
    assert {task for task in known if task.startswith("check-")} - jobs == set()
