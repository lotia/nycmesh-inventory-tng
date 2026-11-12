"""Each checkout's compose stack (CONTRIBUTING.md, Local settings): the
project name, database and free ports `mise run setup` writes into a new
`.env`, and the stacks of checkouts that no longer exist, which `mise run
remove-stale-stacks` removes. Each reads compose's list of projects from
standard input:

    mise run compose -- ls --all --format json |
      uv run python -m inventory_tng.stacks new-env
    mise run compose -- ls --all --format json |
      uv run python -m inventory_tng.stacks stale
"""

import argparse
import json
import re
import socket
import sys
import zlib
from collections.abc import Iterable, Sequence
from pathlib import Path

from inventory_tng.local_env import CLONE, local_env_values, setting

# compose.yaml's name: the project of every checkout whose .env names none,
# as each did before it had a stack of its own. Never stale: more than one
# checkout may use it.
SHARED = "inventory-tng"
# Each port compose.yaml publishes, and its default there.
PORTS = {
    "INVENTORY_DB_PORT": 5433,
    "INVENTORY_APP_PORT": 8000,
    "INVENTORY_WEBKIT_PORT": 3000,
    "INVENTORY_DASHBOARD_PORT": 18888,
    "INVENTORY_OTLP_PORT": 4318,
    "INVENTORY_MAILPIT_PORT": 8025,
    "INVENTORY_SMTP_PORT": 1025,
}

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


def claimed_ports(projects: Iterable[Project]) -> set[int]:
    """The ports this repository's stacks publish, or would once started:
    from each one's checkout's .env while it names that stack, else
    compose.yaml's defaults."""
    claimed = set()
    for project in ours(projects):
        for file in config_files(project):
            values = local_env_values(file.with_name(".env"))
            if values.get("COMPOSE_PROJECT_NAME", SHARED) != project["Name"]:
                values = {}
            for key, default in PORTS.items():
                value = values.get(key, str(default))
                if value.isdigit():
                    claimed.add(int(value))
    return claimed


def in_use(port: int) -> bool:
    """Whether something listens on the port, where compose publishes."""
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


def free_port(preferred: int, taken: set[int]) -> int:
    """The preferred port, else the next one up that no stack claims and
    nothing listens on."""
    port = preferred
    while port in taken or in_use(port):
        port += 1
        if port > 65535:
            raise SystemExit(f"No port from {preferred} up is free")
    return port


def checkout_name(checkout: Path) -> str:
    """The checkout's directory, told apart from another of that name by a
    checksum of where it is: in its database's name and its project's."""
    directory = re.sub(r"[^a-z0-9]", "_", checkout.name.lower())[:40]
    return f"{directory}_{zlib.crc32(str(checkout).encode())}"


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
        example, found = re.subn(
            rf"^{key}=.*$",
            lambda _, line=f"{key}={value}": line,
            example,
            flags=re.MULTILINE,
        )
        if found != 1:
            raise ValueError(f".env.example names {key} {found} times, not once")
    return example


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


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m inventory_tng.stacks",
        description="Read `compose ls --all --format json` from standard input.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("new-env", help="print a new .env for this checkout")
    commands.add_parser(
        "stale", help="print each stack whose checkout is gone, and where it was"
    )
    args = parser.parse_args(argv)
    projects = json.load(sys.stdin)
    if args.command == "new-env":
        example = (CLONE / ".env.example").read_text()
        print(new_env(example, CLONE, projects), end="")
    else:
        current = setting("COMPOSE_PROJECT_NAME") or SHARED
        for name, checkout in stale(projects, current):
            print(name, checkout)


if __name__ == "__main__":
    main()
