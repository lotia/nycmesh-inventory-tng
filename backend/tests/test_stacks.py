"""Each checkout's compose stack (stacks.py): the project, database and
free ports a new .env gets, and the stacks whose checkout is gone, never
the shared one nor this checkout's."""

import io
import json
import re
import runpy
import socket
import sys
import zlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from inventory_tng import stacks
from inventory_tng.local_env import CLONE
from inventory_tng.stacks import PORTS, SHARED

EXAMPLE = "\n".join(
    ["COMPOSE_PROJECT_NAME=inventory-tng", "INVENTORY_DB_NAME=inventory"]
    + [f"{key}={default}" for key, default in PORTS.items()]
    + ["OTHER=kept", ""]
)


def project(name: str, checkout: Path) -> stacks.Project:
    return {
        "Name": name,
        "Status": "exited(1)",
        "ConfigFiles": f"{checkout}/compose.yaml",
    }


@pytest.fixture
def listening() -> Iterator[int]:
    """A port something listens on, where compose publishes."""
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        yield server.getsockname()[1]


@pytest.fixture
def nothing_listens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stacks, "in_use", lambda _: False)


def test_a_port_something_listens_on_is_in_use(listening: int) -> None:
    assert stacks.in_use(listening)
    assert stacks.free_port(listening, set()) > listening


@pytest.mark.usefixtures("nothing_listens")
def test_a_free_port_skips_those_claimed() -> None:
    assert stacks.free_port(5433, {5433, 5434}) == 5435
    assert stacks.free_port(5433, set()) == 5433
    with pytest.raises(SystemExit, match="No port from 65535 up is free"):
        stacks.free_port(65535, {65535})


@pytest.mark.usefixtures("nothing_listens")
def test_a_new_env_has_a_stack_database_and_ports_of_its_own(tmp_path: Path) -> None:
    checkout = tmp_path / "My Work-tree"
    where = zlib.crc32(str(checkout).encode())
    made = stacks.new_env(EXAMPLE, checkout, [])
    assert made.splitlines() == [
        f"COMPOSE_PROJECT_NAME=inventory-tng-my_work_tree_{where}",
        f"INVENTORY_DB_NAME=inventory_my_work_tree_{where}",
        *(f"{key}={default}" for key, default in PORTS.items()),
        "OTHER=kept",
    ]


@pytest.mark.usefixtures("nothing_listens")
def test_ports_another_stack_claims_are_skipped(tmp_path: Path) -> None:
    """From its checkout's .env, else compose.yaml's defaults; only this
    repository's stacks."""
    named = tmp_path / "named"
    named.mkdir()
    (named / ".env").write_text(
        f"COMPOSE_PROJECT_NAME={SHARED}-named_1\nINVENTORY_DB_PORT=5434\n"
        "INVENTORY_APP_PORT=${X}\nINVENTORY_SMTP_PORT=smtp\n"
    )
    projects = [
        project(f"{SHARED}-named_1", named),
        # Made from that checkout before its .env named a stack of its own:
        # its .env's ports are not this one's.
        project(SHARED, named),
        project(SHARED, tmp_path / "gone"),
        project("someone-else", tmp_path / "theirs"),
    ]
    # One not a number is left out, as compose would refuse it.
    assert stacks.claimed_ports(projects) == set(PORTS.values()) | {5434}
    made = stacks.new_env(EXAMPLE, tmp_path / "new", projects)
    ports = dict(re.findall(r"^(INVENTORY_\w+_PORT)=(\d+)$", made, re.MULTILINE))
    assert ports["INVENTORY_DB_PORT"] == "5435"
    assert ports["INVENTORY_APP_PORT"] == "8001"


def test_a_long_name_fits_postgresql(tmp_path: Path) -> None:
    name = stacks.checkout_name(tmp_path / ("x" * 100))
    assert len(f"inventory_{name}") <= 63


@pytest.mark.clone
@pytest.mark.usefixtures("nothing_listens")
def test_the_example_names_each_setting_once() -> None:
    example = (CLONE / ".env.example").read_text()
    stacks.new_env(example, CLONE, [])
    with pytest.raises(ValueError, match="COMPOSE_PROJECT_NAME 0 times"):
        stacks.new_env("INVENTORY_DB_NAME=x\n", CLONE, [])


@pytest.mark.clone
def test_compose_publishes_each_port_as_env_names_it() -> None:
    """Every port compose.yaml publishes on the host is a setting new_env
    picks, with the same default."""
    compose = (CLONE / "compose.yaml").read_text()
    published = re.findall(
        r'^ +- "127\.0\.0\.1:(\$\{[^}]*\}|\d+):', compose, re.MULTILINE
    )
    assert sorted(published) == sorted(
        f"${{{key}:-{default}}}" for key, default in PORTS.items()
    )


def test_stale_stacks_are_those_whose_checkout_is_gone(tmp_path: Path) -> None:
    here = tmp_path / "here"
    here.mkdir()
    (here / "compose.yaml").touch()
    gone = tmp_path / "gone"
    projects = [
        project(f"{SHARED}-here_1", here),
        project(f"{SHARED}-gone_2", gone),
        project(f"{SHARED}-current_3", gone),
        project(SHARED, gone),
        project("someone-else", gone),
        {"Name": f"{SHARED}-unknown_4", "Status": "exited(1)", "ConfigFiles": ""},
    ]
    assert stacks.stale(projects, current=f"{SHARED}-current_3") == [
        (f"{SHARED}-gone_2", gone)
    ]


def run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *argv: str,
    projects: list[stacks.Project],
) -> str:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(projects)))
    stacks.main(argv)
    return capsys.readouterr().out


def test_it_prints_stale_stacks_but_this_checkouts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    gone = tmp_path / "gone"
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", f"{SHARED}-mine_1")
    projects = [project(f"{SHARED}-mine_1", gone), project(f"{SHARED}-old_2", gone)]
    printed = run(monkeypatch, capsys, "stale", projects=projects)
    assert printed == f"{SHARED}-old_2 {gone}\n"


@pytest.mark.clone
@pytest.mark.usefixtures("nothing_listens")
def test_it_prints_a_new_env(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    printed = run(monkeypatch, capsys, "new-env", projects=[])
    assert f"\nCOMPOSE_PROJECT_NAME={SHARED}-" in printed
    assert "\nINVENTORY_DB_PORT=5433\n" in printed


def test_it_runs_as_a_module(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """As the task scripts run it: python -m inventory_tng.stacks."""
    monkeypatch.setattr(sys, "argv", ["stacks", "stale"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("[]"))
    with pytest.warns(RuntimeWarning, match="found in sys.modules"):
        runpy.run_module("inventory_tng.stacks", run_name="__main__")
    assert capsys.readouterr().out == ""
