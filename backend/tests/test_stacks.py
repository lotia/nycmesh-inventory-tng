"""Each checkout's compose stack (stacks.py): the project, database and
free ports a new .env gets, the check before a start that its ports are
free, the free ports an existing .env is given again, and the stacks whose
checkout is gone, never the shared one nor this checkout's."""

import errno
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

from inventory_tng import local_env, stacks
from inventory_tng.local_env import CLONE
from inventory_tng.stacks import NEW_PORTS, PORTS, PUBLISHED, SHARED

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


def test_a_port_something_listens_on_on_localhost_s_ipv6_is_in_use() -> None:
    """As Vite does on localhost."""
    try:
        server = socket.socket(socket.AF_INET6)
        server.bind(("::1", 0))
    except OSError:
        pytest.skip("no IPv6 here")
    with server:
        server.listen()
        assert stacks.in_use(server.getsockname()[1])


def test_a_port_a_stopped_server_left_connections_on_is_free() -> None:
    """Its closed connections wait out their time on the port, which the
    next server, reusing the address, binds all the same."""
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port)) as client:
            accepted, _ = server.accept()
            accepted.close()
            client.recv(1)
    assert not stacks.in_use(port)


def test_without_ipv6_only_127_0_0_1_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    real = socket.socket

    def no_ipv6(family: int = socket.AF_INET) -> socket.socket:
        if family == socket.AF_INET6:
            raise OSError(errno.EAFNOSUPPORT, "no IPv6")
        return real(family)

    monkeypatch.setattr(socket, "socket", no_ipv6)
    with real() as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    assert not stacks.in_use(port)


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
def test_each_port_s_default_is_the_same_everywhere() -> None:
    """.env.example gives each port its default, and Vite's config falls
    back to the same one."""
    example = local_env.local_env_values(CLONE / ".env.example")
    assert {key: int(example[key]) for key in PORTS} == PORTS
    vite = (CLONE / "frontend" / "vite.config.ts").read_text()
    assert f'setting("INVENTORY_VITE_PORT") || "{PORTS["INVENTORY_VITE_PORT"]}"' in vite


@pytest.mark.clone
def test_compose_publishes_each_port_as_env_names_it() -> None:
    """Every port compose.yaml publishes on the host is a setting new_env
    picks, with the same default."""
    compose = (CLONE / "compose.yaml").read_text()
    published = re.findall(
        r'^ +- "127\.0\.0\.1:(\$\{[^}]*\}|\d+):', compose, re.MULTILINE
    )
    assert sorted(published) == sorted(
        f"${{{key}:-{default}}}" for key, default in PUBLISHED.items()
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


def test_it_prints_stale_stacks_but_this_checkouts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    gone = tmp_path / "gone"
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", f"{SHARED}-mine_1")
    projects = [project(f"{SHARED}-mine_1", gone), project(f"{SHARED}-old_2", gone)]
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(projects)))
    stacks.main(["stale"])
    assert capsys.readouterr().out == f"{SHARED}-old_2 {gone}\n"


@pytest.mark.clone
@pytest.mark.usefixtures("nothing_listens")
def test_it_prints_a_new_env(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = compose_answers(monkeypatch)
    stacks.main(["new-env"])
    printed = capsys.readouterr().out
    assert f"\nCOMPOSE_PROJECT_NAME={SHARED}-" in printed
    assert "\nINVENTORY_DB_PORT=5433\n" in printed
    assert calls == [("ls", "--all")]


def test_it_runs_as_a_module(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """As the task scripts run it: python -m inventory_tng.stacks."""
    monkeypatch.setattr(sys, "argv", ["stacks", "stale"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("[]"))
    with pytest.warns(RuntimeWarning, match="found in sys.modules"):
        runpy.run_module("inventory_tng.stacks", run_name="__main__")
    assert capsys.readouterr().out == ""


@pytest.fixture
def env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """This checkout's .env, in a folder of its own, with no setting of it
    in the shell."""
    path = tmp_path / ".env"
    path.write_text("")
    monkeypatch.setattr(local_env, "ENV_FILE", path)
    monkeypatch.setattr(stacks, "ENV_FILE", path)
    for key in [*PORTS, "COMPOSE_PROJECT_NAME"]:
        monkeypatch.delenv(key, raising=False)
    return path


def compose_answers(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ps: list[dict] | None = None,
    ls: list[stacks.Project] | None = None,
) -> list[tuple[str, ...]]:
    """compose's answers to ps and ls, and the calls made, in order."""
    calls: list[tuple[str, ...]] = []

    def answer(*arguments: str) -> list[dict]:
        calls.append(arguments)
        return (ps if arguments[0] == "ps" else ls) or []

    monkeypatch.setattr(stacks, "compose_json", answer)
    return calls


def publishing(service: str, *ports: int) -> dict:
    """A container in `compose ps --format json`."""
    return {
        "Service": service,
        "Publishers": [
            {
                "URL": "127.0.0.1",
                "TargetPort": 1,
                "PublishedPort": port,
                "Protocol": "tcp",
            }
            for port in ports
        ],
    }


def test_a_port_is_the_shell_s_else_env_s_else_its_default(
    monkeypatch: pytest.MonkeyPatch, env_file: Path
) -> None:
    env_file.write_text("INVENTORY_DB_PORT=5434\nINVENTORY_APP_PORT=app\n")
    assert stacks.port("INVENTORY_DB_PORT") == 5434
    assert stacks.port("INVENTORY_VITE_PORT") == 5173
    with pytest.raises(SystemExit, match="INVENTORY_APP_PORT must be a port number"):
        stacks.port("INVENTORY_APP_PORT")
    monkeypatch.setenv("INVENTORY_DB_PORT", "6000")
    assert stacks.port("INVENTORY_DB_PORT") == 6000


@pytest.mark.usefixtures("env_file", "nothing_listens")
def test_free_ports_ask_compose_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = compose_answers(monkeypatch)
    assert stacks.ports_free(PORTS, here=False) == []
    assert calls == []


def test_a_port_this_checkout_s_stack_holds_is_free_for_it(
    monkeypatch: pytest.MonkeyPatch, env_file: Path, listening: int
) -> None:
    env_file.write_text(f"INVENTORY_DB_PORT={listening}\n")
    calls = compose_answers(monkeypatch, ps=[publishing("db", listening)])
    assert stacks.ports_free(["INVENTORY_DB_PORT"], here=False) == []
    assert calls == [("ps",)]


def test_a_server_here_needs_it_free_of_this_checkout_s_stack_too(
    monkeypatch: pytest.MonkeyPatch, env_file: Path, listening: int
) -> None:
    """As mise run dev's backend does, which takes the app container's port."""
    env_file.write_text(f"INVENTORY_APP_PORT={listening}\n")
    compose_answers(monkeypatch, ps=[publishing("app", listening)])
    assert stacks.ports_free(["INVENTORY_APP_PORT"], here=True) == [
        (
            f"Port {listening} (INVENTORY_APP_PORT in .env) is in use, by this "
            "checkout's app container: mise run compose -- stop app frees it."
        )
    ]


def test_a_port_another_checkout_claims_names_it_and_what_to_do(
    monkeypatch: pytest.MonkeyPatch, env_file: Path, listening: int, tmp_path: Path
) -> None:
    env_file.write_text(f"COMPOSE_PROJECT_NAME={SHARED}-mine_1\n")
    monkeypatch.setenv("INVENTORY_DB_PORT", str(listening))
    other = tmp_path / "other"
    other.mkdir()
    (other / ".env").write_text(
        f"COMPOSE_PROJECT_NAME={SHARED}-other_2\nINVENTORY_DB_PORT={listening}\n"
    )
    calls = compose_answers(
        monkeypatch,
        ls=[
            project(f"{SHARED}-mine_1", tmp_path),
            project(f"{SHARED}-other_2", other),
        ],
    )
    assert stacks.ports_free(["INVENTORY_DB_PORT"], here=False) == [
        (
            f"Port {listening} (INVENTORY_DB_PORT, set in your shell) is in use, by "
            f"the checkout at {other} (its stack: {SHARED}-other_2)."
        ),
        f"Give this checkout free ports: {NEW_PORTS}",
    ]
    assert calls == [("ps",), ("ls",)]


def test_a_port_nothing_of_ours_claims_is_held_by_something_else(
    monkeypatch: pytest.MonkeyPatch, env_file: Path, listening: int, tmp_path: Path
) -> None:
    """Its own stack, though it claims the port, is not what holds it."""
    monkeypatch.setattr(stacks, "PORTS", {**PORTS, "INVENTORY_VITE_PORT": listening})
    compose_answers(monkeypatch, ls=[project(SHARED, env_file.parent)])
    assert stacks.ports_free(["INVENTORY_VITE_PORT"], here=True) == [
        (
            f"Port {listening} (INVENTORY_VITE_PORT, unset in .env, so its default) "
            "is in use, by something else."
        ),
        f"Give this checkout free ports: {NEW_PORTS}",
    ]


def test_new_ports_change_only_the_ports_that_need_it(
    nothing_listens: None, tmp_path: Path
) -> None:
    """One this checkout's stack holds stays, though another claims it; one
    still free stays, though not the first free one; one another claims
    moves; one missing is added only if its default is taken; every other
    line stays as it was."""
    other = tmp_path / "other"
    other.mkdir()
    theirs = {key: default + 100 for key, default in PORTS.items()}
    theirs |= {
        "INVENTORY_DB_PORT": 5434,
        "INVENTORY_APP_PORT": 8001,
        "INVENTORY_SMTP_PORT": 1025,
    }
    (other / ".env").write_text(
        f"COMPOSE_PROJECT_NAME={SHARED}-other_2\n"
        + "".join(f"{key}={port}\n" for key, port in theirs.items())
    )
    projects = [project(f"{SHARED}-other_2", other)]
    current = {
        "INVENTORY_DB_PORT": "5434",
        "INVENTORY_APP_PORT": "8001",
        "INVENTORY_WEBKIT_PORT": "3005",
    }
    env = "".join(f"{key}={value}\n" for key, value in current.items())
    env = f"# a comment\n{env}OTHER=kept\n# INVENTORY_VITE_PORT=1\n"
    made, changed = stacks.new_ports(env, current, projects, held=[5434])
    assert changed == {
        "INVENTORY_APP_PORT": ("8001", 8000),
        "INVENTORY_SMTP_PORT": ("", 1026),
    }
    assert made.splitlines() == [
        "# a comment",
        "INVENTORY_DB_PORT=5434",
        "INVENTORY_APP_PORT=8000",
        "INVENTORY_WEBKIT_PORT=3005",
        "OTHER=kept",
        "# INVENTORY_VITE_PORT=1",
        "INVENTORY_SMTP_PORT=1026",
    ]


def test_new_ports_leave_a_shared_stack_s_env_as_it_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A .env made before names no port, and its checkout's stack, the shared
    one, holds the defaults it uses."""
    monkeypatch.setattr(stacks, "in_use", lambda port: port in {5433, 8000})
    env = "APP_ENV=development\n"
    assert stacks.new_ports(env, {}, [], held=[5433, 8000]) == (env, {})
    monkeypatch.setattr(stacks, "in_use", lambda port: port == 5433)
    assert stacks.new_ports(env, {}, [], held=[]) == (
        env + "INVENTORY_DB_PORT=5434\n",
        {"INVENTORY_DB_PORT": ("", 5434)},
    )


def test_compose_answers_in_lines_or_a_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """compose ps prints an object a line, compose ls a list; with no
    runtime, compose fails and there is nothing."""
    script = tmp_path / "compose"
    monkeypatch.setattr(stacks, "COMPOSE", script)
    script.write_text('#!/bin/sh\nprintf \'{"a": 1}\\n{"a": 2}\\n\'\n')
    script.chmod(0o755)
    assert stacks.compose_json("ps") == [{"a": 1}, {"a": 2}]
    script.write_text('#!/bin/sh\necho "$*" >&2\necho \'[{"a": 3}]\'\n')
    assert stacks.compose_json("ls") == [{"a": 3}]
    script.write_text("#!/bin/sh\necho 'not JSON'\n")
    assert stacks.compose_json("ps") == []
    script.write_text("#!/bin/sh\nexit 1\n")
    assert stacks.compose_json("ls") == []
    script.unlink()
    assert stacks.compose_json("ls") == []


def test_own_ports_are_those_this_stack_publishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_answers(
        monkeypatch,
        ps=[publishing("db", 5433), publishing("app", 8000, 0), {"Service": "x"}],
    )
    assert stacks.own_ports() == {5433: "db", 8000: "app"}


def test_it_stops_a_start_when_a_port_is_taken(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    env_file: Path,
    listening: int,
) -> None:
    env_file.write_text(f"INVENTORY_DB_PORT={listening}\n")
    compose_answers(monkeypatch)
    with pytest.raises(SystemExit) as stopped:
        stacks.main(["ports-free", "INVENTORY_DB_PORT"])
    assert stopped.value.code == 1
    assert capsys.readouterr().err == (
        f"Port {listening} (INVENTORY_DB_PORT in .env) is in use, by something else.\n"
        f"Give this checkout free ports: {NEW_PORTS}\n"
    )


@pytest.mark.usefixtures("env_file", "nothing_listens")
def test_it_lets_a_start_go_on_when_its_ports_are_free(
    capsys: pytest.CaptureFixture[str],
) -> None:
    stacks.main(["ports-free", "--here", "INVENTORY_APP_PORT"])
    assert capsys.readouterr() == ("", "")


@pytest.mark.usefixtures("nothing_listens")
def test_it_prints_a_port(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], env_file: Path
) -> None:
    env_file.write_text("INVENTORY_APP_PORT=8002\n")
    stacks.main(["port", "INVENTORY_APP_PORT"])
    assert capsys.readouterr().out == "8002\n"
    stacks.main(["port", "INVENTORY_APP_PORT", "INVENTORY_VITE_PORT"])
    assert capsys.readouterr().out == "8002 5173\n"


@pytest.mark.usefixtures("nothing_listens")
def test_it_gives_env_new_ports_and_says_which(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], env_file: Path
) -> None:
    lines = [f"COMPOSE_PROJECT_NAME={SHARED}-mine_1", "SETUP_TOKEN=kept"]
    lines += [f"{key}={default}" for key, default in PORTS.items()]
    env_file.write_text("\n".join(lines) + "\n")
    env_file.chmod(0o600)
    calls = compose_answers(
        monkeypatch, ls=[project(f"{SHARED}-mine_1", env_file.parent)]
    )
    stacks.main(["new-ports"])
    assert capsys.readouterr().out == "Each port in .env was free already.\n"
    assert calls == [("ls", "--all"), ("ps",)]
    monkeypatch.setattr(stacks, "in_use", lambda port: port == 5433)
    stacks.main(["new-ports"])
    assert capsys.readouterr().out == "INVENTORY_DB_PORT: 5433 -> 5434\n"
    assert env_file.read_text() == "\n".join(lines).replace("5433", "5434") + "\n"
    assert env_file.stat().st_mode & 0o777 == 0o600
