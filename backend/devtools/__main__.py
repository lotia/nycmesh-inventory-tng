"""The developer's commands (CONTRIBUTING.md; docs/decisions/0023), run
from backend/, or with it on PYTHONPATH, as the task scripts do:

    python -m devtools                  # every command, a line on each
    python -m devtools <command> --help

Listed here by name, never discovered, and only the one chosen is
imported, as the operator's commands are (inventory_tng.core.entry_point).
"""

import sys
from collections.abc import Sequence

from inventory_tng.core.entry_point import Commands, dispatch

PROG = "python -m devtools"
COMMANDS: Commands = {
    "database": (
        "devtools.database",
        "make, seed or drop this checkout's development database",
    ),
    "setting": (
        "devtools.setting",
        "print a setting as the backend reads it",
    ),
    "stack": (
        "devtools.stack",
        "this checkout's compose stack: its .env, its ports, stale stacks",
    ),
    "test-report": (
        "devtools.suite_report",
        "measure the test suites: times, coverage, what each test adds",
    ),
    "write-api-schema": (
        "devtools.write_api_schema",
        "write frontend/src/api/openapi.json, the API's schema",
    ),
    "write-unit-table": (
        "devtools.write_unit_table",
        "write frontend/src/units.json, the scan page's unit table",
    ),
}


def main(argv: Sequence[str] | None = None) -> int:
    return dispatch(
        argv,
        prog=PROG,
        description="The developer's commands (CONTRIBUTING.md).",
        commands=COMMANDS,
    )


if __name__ == "__main__":
    sys.exit(main())
