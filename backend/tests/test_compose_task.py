"""mise-tasks/compose: which container runtime `mise run compose` uses.
Run with stand-ins for podman and docker that print what they were given,
and a PATH holding only what the script needs."""

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from inventory_tng.local_env import CLONE

SCRIPT = CLONE / "mise-tasks" / "compose"

pytestmark = pytest.mark.clone

Run = Callable[..., subprocess.CompletedProcess[str]]


@pytest.fixture
def run(tmp_path: Path) -> Run:
    """Runs the script in a clone root of its own (tmp_path), with only the
    given runtimes installed."""
    tools = tmp_path / "bin"
    tools.mkdir()
    for tool in ("uv", "dirname"):
        found = shutil.which(tool)
        assert found is not None, tool
        (tools / tool).symlink_to(found)

    def run_with(
        *runtimes: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        for runtime in runtimes:
            stand_in = tools / runtime
            stand_in.write_text(f'#!/bin/sh\necho {runtime} "$@"\n')
            stand_in.chmod(0o755)
        bash = shutil.which("bash")
        assert bash is not None
        return subprocess.run(
            [bash, str(SCRIPT), "up", "-d", "two words"],
            env={
                "PATH": str(tools),
                "HOME": os.environ.get("HOME", str(tmp_path)),
                "MISE_PROJECT_ROOT": str(tmp_path),
                **(env or {}),
            },
            capture_output=True,
            text=True,
            check=False,
        )

    return run_with


def test_podman_when_both_are_installed(run: Run) -> None:
    assert run("podman", "docker").stdout == "podman compose up -d two words\n"


def test_docker_without_podman(run: Run) -> None:
    assert run("docker").stdout == "docker compose up -d two words\n"


def test_neither_says_what_to_do(run: Run) -> None:
    done = run()
    assert done.returncode == 1
    assert "set INVENTORY_COMPOSE in .env" in done.stderr


def test_dot_env_chooses(run: Run, tmp_path: Path) -> None:
    # Written as an editor on Windows might: quoted, commented, CRLF.
    (tmp_path / ".env").write_text('INVENTORY_COMPOSE="docker compose"  # mine\r\n')
    assert run("podman", "docker").stdout == "docker compose up -d two words\n"


def test_the_environment_wins_over_dot_env(run: Run, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("INVENTORY_COMPOSE=docker compose\n")
    done = run("podman", "docker", env={"INVENTORY_COMPOSE": "podman compose"})
    assert done.stdout == "podman compose up -d two words\n"
