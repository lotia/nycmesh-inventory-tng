"""mise-tasks/phone-lan: Caddy publishes the app on the local address only
while the task runs, and goes on Ctrl-C or a failure. Run in a copy of the
script beside a compose that records what it was asked, with a uv that
answers for devtools and a sleep that presses Ctrl-C."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from inventory_tng.core.local_env import CLONE

SCRIPT = CLONE / "mise-tasks" / "phone-lan"

pytestmark = pytest.mark.clone

# devtools' answers: the address, then each port asked for.
UV = """#!/bin/sh
case "$*" in
  *lan-address*) echo 192.168.1.20 ;;
  *INVENTORY_APP_PORT*) echo 8002 ;;
  *INVENTORY_LAN_PORT*) echo 8443 ;;
esac
"""
# Each call, with the settings compose would publish by; FAIL makes one
# such call fail.
COMPOSE = """#!/bin/sh
echo "$INVENTORY_LAN_ADDRESS:$INVENTORY_LAN_PORT $INVENTORY_APP_PORT $*" >> "$LOG"
case "$*" in *"$FAIL"*) [ -n "$FAIL" ] && exit 1 ;; esac
exit 0
"""
# Ctrl-C, as the terminal sends it to the task.
SLEEP = '#!/bin/sh\nkill -INT "$PPID"\n'


def run(tmp_path: Path, fail: str = "") -> tuple[subprocess.CompletedProcess, list]:
    tasks = tmp_path / "mise-tasks"
    tools = tmp_path / "bin"
    tasks.mkdir()
    tools.mkdir()
    shutil.copy(SCRIPT, tasks / "phone-lan")
    for folder, name, text in (
        (tasks, "compose", COMPOSE),
        (tools, "uv", UV),
        (tools, "sleep", SLEEP),
    ):
        (folder / name).write_text(text)
        (folder / name).chmod(0o755)
    dirname = shutil.which("dirname")
    assert dirname is not None
    (tools / "dirname").symlink_to(dirname)
    bash = shutil.which("bash")
    assert bash is not None
    log = tmp_path / "compose.log"
    done = subprocess.run(
        [bash, str(tasks / "phone-lan")],
        env={
            "PATH": str(tools),
            "HOME": os.environ.get("HOME", str(tmp_path)),
            "LOG": str(log),
            "FAIL": fail,
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return done, log.read_text().splitlines()


def test_it_serves_on_the_local_address_until_ctrl_c(tmp_path: Path) -> None:
    done, calls = run(tmp_path)
    assert done.returncode == 130
    assert "On a phone on this network: https://192.168.1.20:8443" in done.stdout
    settings = "192.168.1.20:8443 8002 --profile lan"
    code = (
        "python -m inventory_tng.manage registration-codes create --uses 1 "
        "--days 1 --label phone --base-url https://192.168.1.20:8443"
    )
    assert calls == [
        f"{settings} up -d --build --wait app",
        f"{settings} up -d --force-recreate --no-deps caddy",
        f"{settings} exec -T app {code}",
        f"{settings} rm -sf caddy",
    ]


def test_a_failure_removes_caddy_too(tmp_path: Path) -> None:
    done, calls = run(tmp_path, fail="exec")
    assert done.returncode == 1
    assert calls[-1].endswith("--profile lan rm -sf caddy")
    assert "Ctrl-C" not in done.stdout
