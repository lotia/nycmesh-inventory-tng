"""mise-tasks/start-database: starts the shared database server, never
recreating its container, and stops when its settings differ from this
checkout's. Run with a stand-in for compose that logs what it was given."""

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from inventory_tng.local_env import CLONE

SCRIPT = CLONE / "mise-tasks" / "start-database"
RUNNING = "a1b2"

pytestmark = pytest.mark.clone

Run = Callable[..., subprocess.CompletedProcess[str]]


@pytest.fixture
def calls(tmp_path: Path) -> Path:
    return tmp_path / "calls"


@pytest.fixture
def run(tmp_path: Path, calls: Path) -> Run:
    """Runs the script in a clone root of its own (tmp_path), with compose a
    stand-in: `ps` prints `made`, the running container's hash and, after a
    `|`, where it was made; `config --hash db` prints `wanted`."""
    tools = tmp_path / "bin"
    tools.mkdir()
    # bash for the compose task's script, which this one runs.
    for tool in ("bash", "dirname"):
        found = shutil.which(tool)
        assert found is not None, tool
        (tools / tool).symlink_to(found)
    stand_in = tools / "compose"
    stand_in.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >>{calls}\n'
        'case "$*" in\n'
        '  ps*) printf %s "$MADE" ;;\n'
        '  *"config --hash db") echo "db $WANTED" ;;\n'
        "esac\n"
    )
    stand_in.chmod(0o755)

    def run_with(made: str, wanted: str = RUNNING) -> subprocess.CompletedProcess[str]:
        bash = shutil.which("bash")
        assert bash is not None
        return subprocess.run(
            [bash, str(SCRIPT)],
            env={
                "PATH": str(tools),
                "HOME": os.environ.get("HOME", str(tmp_path)),
                "MISE_PROJECT_ROOT": str(tmp_path),
                "INVENTORY_COMPOSE": str(stand_in),
                "MADE": made,
                "WANTED": wanted,
            },
            capture_output=True,
            text=True,
            check=False,
        )

    return run_with


def test_with_no_container_it_starts_one(run: Run, calls: Path) -> None:
    done = run(made="")
    assert done.returncode == 0
    assert calls.read_text().splitlines()[-1] == "up -d --wait --no-recreate db"
    assert "config --hash" not in calls.read_text()


def test_the_same_settings_start_it_as_it_is(run: Run, calls: Path) -> None:
    done = run(made=f"{RUNNING}|/made in/here")
    assert (done.returncode, done.stderr) == (0, "")
    assert calls.read_text().splitlines()[-1] == "up -d --wait --no-recreate db"


def test_settings_that_differ_stop_it(run: Run, calls: Path) -> None:
    done = run(made=f"{RUNNING}|/made/here", wanted="c3d4")
    assert done.returncode == 1
    assert done.stderr == (
        "The database container's settings differ from this checkout's "
        "compose.yaml. Run `mise run recreate-database-container`, which "
        "restarts it for every checkout's connections.\n"
    )
    assert " up " not in f" {calls.read_text()}"


def test_a_container_compose_did_not_label_stops_it(run: Run, calls: Path) -> None:
    """With no hash to compare, it is never taken as the same."""
    done = run(made="|/made/here")
    assert done.returncode == 1
    assert "recreate-database-container" in done.stderr
    assert "--project-directory /made/here " in calls.read_text()


def test_settings_are_hashed_as_if_from_the_checkout_that_made_it(
    run: Run, calls: Path, tmp_path: Path
) -> None:
    """A bind mount's path is in the hash, so another checkout's would differ
    for that alone. This checkout's .env, or none, never that one's."""
    run(made=f"{RUNNING}|/made in/there")
    (tmp_path / ".env").touch()
    run(made=f"{RUNNING}|/made in/there")
    hashes = [
        line for line in calls.read_text().splitlines() if "config --hash" in line
    ]
    files = f"--file {tmp_path}/compose.yaml --project-directory /made in/there"
    assert hashes == [
        f"{files} --env-file /dev/null config --hash db",
        f"{files} --env-file {tmp_path}/.env config --hash db",
    ]
