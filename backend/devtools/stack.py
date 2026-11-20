"""Each checkout's compose stack (CONTRIBUTING.md, Local settings): the
project name, database and free ports `mise run setup` writes into a new
`.env`, and the stacks of checkouts that no longer exist, which `mise run
remove-stale-stacks` removes. The tasks that start something first check
that the ports they need are free, or this checkout's stack's, and `mise run
setup -- --new-ports` gives an existing `.env` free ones. `mise run phone`
and `mise run phone-lan` instead publish on another port when theirs is
taken, as by `mise run dev`'s backend. Each command but list-stale asks
compose itself, through mise-tasks/compose:

    uv run python -m devtools stack new-env
    uv run python -m devtools stack ports-free [--here] KEY...
    uv run python -m devtools stack new-ports
    uv run python -m devtools stack port KEY...
    uv run python -m devtools stack free-port [--address ADDRESS] KEY

list-stale reads compose's list of projects from standard input, so that
compose failing fails the list (mise-tasks/list-stale-stacks):

    mise run compose -- ls --all --format json |
      uv run python -m devtools stack list-stale
"""

import argparse
import errno
import json
import os
import re
import socket
import subprocess
import sys
import zlib
from collections.abc import Callable, Iterable, Sequence
from functools import cache
from pathlib import Path

from inventory_tng.core.local_env import CLONE, ENV_FILE, local_env_values, setting

# compose.yaml's name: the project of every checkout whose .env names none,
# as each did before it had a stack of its own. Never stale: more than one
# checkout may use it.
SHARED = "inventory-tng"
# Each port compose.yaml publishes, and its default there.
PUBLISHED = {
    "INVENTORY_DB_PORT": 5433,
    "INVENTORY_APP_PORT": 8000,
    "INVENTORY_WEBKIT_PORT": 3000,
    "INVENTORY_DASHBOARD_PORT": 18888,
    "INVENTORY_OTLP_PORT": 4318,
    "INVENTORY_MAILPIT_PORT": 8025,
    "INVENTORY_SMTP_PORT": 1025,
    # On the machine's local address, not 127.0.0.1 (mise run phone-lan).
    "INVENTORY_LAN_PORT": 8443,
}
# Each port a checkout's .env gives: those, and Vite's dev server's, which
# `mise run dev` runs here (the backend there takes INVENTORY_APP_PORT).
PORTS = {**PUBLISHED, "INVENTORY_VITE_PORT": 5173}
# The ports `mise run phone` and `mise run phone-lan` move when taken, each
# with the service that publishes it.
MOVABLE = {"INVENTORY_APP_PORT": "app", "INVENTORY_LAN_PORT": "caddy"}
COMPOSE = CLONE / "mise-tasks" / "compose"
NEW_PORTS = "mise run setup -- --new-ports"

# One project in `compose ls --format json`.
Project = dict[str, str]


def ours(projects: Iterable[Project]) -> list[Project]:
    """This repository's stacks: the shared one and each checkout's."""
    return [
        project
        for project in projects
        if project["Name"] == SHARED or project["Name"].startswith(f"{SHARED}-")
    ]


def config_files(project: Project) -> list[Path]:
    return [Path(file) for file in project.get("ConfigFiles", "").split(",") if file]


def stack_ports(project: Project) -> set[int]:
    """The ports a stack of this repository publishes, or would once
    started, and its checkout's dev servers: from its checkout's .env while
    it names that stack, else the defaults."""
    claimed = set()
    for file in config_files(project):
        values = local_env_values(file.with_name(".env"))
        if values.get("COMPOSE_PROJECT_NAME", SHARED) != project["Name"]:
            values = {}
        for key, default in PORTS.items():
            value = values.get(key, str(default))
            if value.isdigit():
                claimed.add(int(value))
    return claimed


def claimed_ports(projects: Iterable[Project]) -> set[int]:
    """The ports this repository's stacks claim."""
    return {port for project in ours(projects) for port in stack_ports(project)}


def bind_error(address: str, port: int) -> OSError | None:
    """Why binding the port at this address fails, if it does. The probe
    reuses the address, as uvicorn and Vite do, so a server's closed
    connections waiting out their time do not count, only a listener."""
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    try:
        with socket.socket(family) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((address, port))
    except OSError as error:
        return error
    return None


def listens_on(address: str, port: int) -> bool:
    """Whether something listens on the port at this address."""
    error = bind_error(address, port)
    return error is not None and error.errno == errno.EADDRINUSE


def in_use(port: int) -> bool:
    """Whether something listens on the port: on 127.0.0.1, where compose
    publishes, or on ::1, where a server on localhost may, as Vite's does.
    A machine without IPv6 has nothing on ::1."""
    return bind_error("127.0.0.1", port) is not None or listens_on("::1", port)


def free_port(
    preferred: int, taken: set[int], busy: Callable[[int], bool] | None = None
) -> int:
    """The preferred port, else the next one up that no stack claims and
    nothing listens on, or, given busy, that it says is not busy."""
    busy = busy or in_use
    port = preferred
    while port in taken or busy(port):
        port += 1
        if port > 65535:
            raise SystemExit(f"No port from {preferred} up is free")
    return port


def checkout_name(checkout: Path) -> str:
    """The checkout's directory, told apart from another of that name by a
    checksum of where it is: in its database's name and its project's."""
    directory = re.sub(r"[^a-z0-9]", "_", checkout.name.lower())[:40]
    return f"{directory}_{zlib.crc32(str(checkout).encode())}"


def replace_setting(text: str, key: str, value: str) -> tuple[str, int]:
    """The text with each line setting the key set to the value, and how
    many there were."""
    return re.subn(
        rf"^{key}=.*$",
        lambda _: f"{key}={value}",
        text,
        flags=re.MULTILINE,
    )


def new_env(example: str, checkout: Path, projects: Iterable[Project]) -> str:
    """.env.example, with a project, database and ports of the checkout's
    own."""
    name = checkout_name(checkout)
    values = {
        "COMPOSE_PROJECT_NAME": f"{SHARED}-{name}",
        "INVENTORY_DB_NAME": f"inventory_{name}",
    }
    taken = claimed_ports(projects)
    for key, default in PORTS.items():
        port = free_port(default, taken)
        taken.add(port)
        values[key] = str(port)
    for key, value in values.items():
        example, found = replace_setting(example, key, value)
        if found != 1:
            raise ValueError(f".env.example names {key} {found} times, not once")
    return example


def new_ports(
    env: str,
    current: dict[str, str],
    projects: Iterable[Project],
    held: Iterable[int],
) -> tuple[str, dict[str, tuple[str, int]]]:
    """An existing .env's text with free ports, and each port changed, from
    what to what. Each port, its default where the file sets none, stays
    while this checkout's stack holds it now, or no other stack claims it
    and it is free; each other is chosen as for a new .env. A setting the
    file lacks is added at the end only when it changes; every other line
    stays as it was."""
    held = set(held)
    taken = claimed_ports(projects)
    changed = {}
    for key, default in PORTS.items():
        was = current.get(key, "")
        port = int(was) if was.isdigit() else default
        if port not in held and (port in taken or in_use(port)):
            port = free_port(default, taken | held)
        taken.add(port)
        if was != str(port) and (was or port != default):
            changed[key] = (was, port)
            env, found = replace_setting(env, key, str(port))
            if not found:
                env = env.rstrip("\n") + f"\n{key}={port}\n"
    return env, changed


def compose_json(*arguments: str) -> list[dict]:
    """What compose prints with --format json, one object a line or a list
    of them; nothing if compose cannot answer, as with no runtime
    running."""
    try:
        printed = subprocess.run(
            [COMPOSE, *arguments, "--format", "json"],
            cwd=CLONE,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if printed.startswith("["):
            return json.loads(printed)
        return [json.loads(line) for line in printed.splitlines() if line.strip()]
    except OSError, subprocess.CalledProcessError, ValueError:
        return []


def own_ports() -> dict[int, str]:
    """The ports this checkout's stack publishes now, each with its
    service."""
    return {
        publisher["PublishedPort"]: container["Service"]
        for container in compose_json("ps")
        for publisher in container.get("Publishers") or []
        if publisher.get("PublishedPort")
    }


def this_stack() -> str:
    """This checkout's project."""
    return setting("COMPOSE_PROJECT_NAME") or SHARED


def other_stacks(*arguments: str) -> list[Project]:
    """This repository's stacks compose lists, but this checkout's."""
    current = this_stack()
    return [p for p in ours(compose_json("ls", *arguments)) if p["Name"] != current]


def port(key: str) -> int:
    """This checkout's port for the setting: the shell's, else .env's, else
    its default."""
    value = setting(key) or str(PORTS[key])
    if not value.isdigit():
        raise SystemExit(f"{key} must be a port number, not {value!r}")
    return int(value)


def free_port_for(key: str, address: str | None = None) -> int:
    """The setting's port while it is free; else the next one up that this
    checkout's container for it holds (a run before may have moved it
    there), or that is free, and none of this checkout's other settings
    names. With an address, a listener there counts too."""

    def taken(candidate: int) -> bool:
        return in_use(candidate) or (
            address is not None and listens_on(address, candidate)
        )

    preferred = port(key)
    if not taken(preferred):
        return preferred
    own = {held for held, service in own_ports().items() if service == MOVABLE[key]}
    claimed = claimed_ports(other_stacks("--all")) - own
    mine = {port(other) for other in PORTS if other != key}
    return free_port(
        preferred,
        mine | claimed,
        lambda candidate: candidate not in own and taken(candidate),
    )


def where_set(key: str) -> str:
    if key in os.environ:
        return f"{key}, set in your shell"
    if key in local_env_values():
        return f"{key} in .env"
    return f"{key}, unset in .env, so its default"


def ports_free(keys: Iterable[str], *, here: bool) -> list[str]:
    """What holds each of the settings' ports that is not free, and what to
    do: nothing if all are. One this checkout's stack holds counts as free,
    unless a server here is to bind it (here)."""
    problems = []
    taken = False
    # Compose only once a port is in use, so a start whose ports are free
    # asks it nothing; then once each.
    own, others = cache(own_ports), cache(other_stacks)
    for key in keys:
        number = port(key)
        if not in_use(number):
            continue
        said = f"Port {number} ({where_set(key)}) is in use"
        if number in own():
            if not here:
                continue
            service = own()[number]
            problems.append(
                f"{said}, by this checkout's {service} container: "
                f"mise run compose -- stop {service} frees it."
            )
            continue
        holder = next(
            (project for project in others() if number in stack_ports(project)), None
        )
        taken = True
        if holder is None:
            problems.append(f"{said}, by something else.")
        else:
            checkout = config_files(holder)[0].parent
            problems.append(
                f"{said}, by the checkout at {checkout} (its stack: {holder['Name']})."
            )
    if taken:
        problems.append(f"Give this checkout free ports: {NEW_PORTS}")
    return problems


def stale(projects: Iterable[Project], current: str) -> list[tuple[str, Path]]:
    """Each checkout's stack whose compose.yaml is gone, with where it was;
    never the shared one, nor this checkout's."""
    return [
        (project["Name"], files[0].parent)
        for project in ours(projects)
        if project["Name"] not in {SHARED, current}
        and (files := config_files(project))
        and not any(file.exists() for file in files)
    ]


def main(argv: Sequence[str], prog: str) -> int:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Each checkout's compose stack, for mise-tasks/.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("new-env", help="print a new .env for this checkout")
    commands.add_parser(
        "list-stale", help="print each stack whose checkout is gone, and where it was"
    )
    free = commands.add_parser(
        "ports-free",
        help="stop, saying what holds it, unless each port is free or this "
        "checkout's stack's",
    )
    free.add_argument(
        "keys", nargs="+", metavar="KEY", help="such as INVENTORY_DB_PORT"
    )
    free.add_argument(
        "--here",
        action="store_true",
        help="a server here binds them, so this checkout's stack must not hold them",
    )
    commands.add_parser(
        "new-ports", help="give .env free ports, changing only its port settings"
    )
    instead = commands.add_parser(
        "free-port",
        help="print the setting's port, or, if it is taken, another free one",
    )
    instead.add_argument("key", metavar="KEY", choices=MOVABLE)
    instead.add_argument(
        "--address", help="where it is published, if not 127.0.0.1, to look there too"
    )
    one = commands.add_parser("port", help="print the port for each setting")
    one.add_argument("keys", nargs="+", metavar="KEY", choices=PORTS)
    args = parser.parse_args(argv)
    if args.command == "ports-free":
        problems = ports_free(args.keys, here=args.here)
        if problems:
            print(*problems, sep="\n", file=sys.stderr)
            return 1
    elif args.command == "free-port":
        number = free_port_for(args.key, args.address)
        if number != port(args.key):
            print(
                f"Port {port(args.key)} ({where_set(args.key)}) is in use: "
                f"using {number} instead.",
                file=sys.stderr,
            )
        print(number)
    elif args.command == "port":
        print(*map(port, args.keys))
    elif args.command == "new-ports":
        env, changed = new_ports(
            ENV_FILE.read_text(), local_env_values(), other_stacks("--all"), own_ports()
        )
        ENV_FILE.write_text(env)
        for key, (was, now) in changed.items():
            print(f"{key}: {was or 'unset'} -> {now}")
        if not changed:
            print("Each port in .env was free already.")
    elif args.command == "new-env":
        example = (CLONE / ".env.example").read_text()
        # With no runtime running, compose knows of no other stack.
        print(new_env(example, CLONE, compose_json("ls", "--all")), end="")
    else:
        for name, checkout in stale(json.load(sys.stdin), this_stack()):
            print(name, checkout)
    return 0
