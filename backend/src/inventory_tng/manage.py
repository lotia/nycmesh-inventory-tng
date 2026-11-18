"""The operator's commands (DEPLOYMENT.md, Operator commands;
docs/decisions/0018, 0023), run where the app runs, with its settings:

    python -m inventory_tng.manage                  # every command, a line on each
    python -m inventory_tng.manage <command> --help

Commands are listed here by name, never discovered, and only the one
chosen is imported. Each sets up the process itself (core/command.py,
docs/decisions/0007), as `totp-keys new` runs before settings can be read.
"""

import argparse
import importlib
import sys
from collections.abc import Sequence

PROG = "python -m inventory_tng.manage"
# Each command's name, its module, and a line on what it does.
COMMANDS = {
    "admins": (
        "inventory_tng.admins.admins_command",
        "lock every administrator out, let one back in, or invite one",
    ),
    "registration-codes": (
        "inventory_tng.devices.command",
        "make or revoke a registration code, or list or revoke its devices",
    ),
    "scan-settings": (
        "inventory_tng.site_settings.command",
        "show or change the scan page's settings",
    ),
    "totp-keys": (
        "inventory_tng.admins.totp_keys_command",
        "make a TOTP key, reseal with the newest, or retire authenticators",
    ),
    "unit-readings": (
        "inventory_tng.catalogue.command",
        "propose or load how units in the imported names are read",
    ),
}


def _parser() -> argparse.ArgumentParser:
    width = max(map(len, COMMANDS))
    listed = "\n".join(
        f"  {name:<{width}}  {line}" for name, (_, line) in COMMANDS.items()
    )
    parser = argparse.ArgumentParser(
        prog=PROG,
        usage=f"{PROG} [-h] command [argument ...]",
        description="The operator's commands (DEPLOYMENT.md, Operator commands).",
        epilog=f"commands:\n{listed}\n\nEach command's own -h says what it takes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "command",
        choices=COMMANDS,
        metavar="command",
        help="one of those below",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    parser = _parser()
    if not argv:
        parser.print_help()
        return 0
    # Only the name is read here: the rest is the command's.
    name = parser.parse_args(argv[:1]).command
    module = importlib.import_module(COMMANDS[name][0])
    return module.main(argv[1:], prog=f"{PROG} {name}")


if __name__ == "__main__":
    sys.exit(main())
