"""The operator's commands (DEPLOYMENT.md, Operator commands;
docs/decisions/0018, 0023), run where the app runs, with its settings:

    python -m inventory_tng.manage                  # every command, a line on each
    python -m inventory_tng.manage <command> --help

Dispatched by core/entry_point.py, which the developer's tools share.
Each command sets up the process itself (core/command.py,
docs/decisions/0007), as `totp-keys new` runs before settings can be read.
"""

import sys
from collections.abc import Sequence

from inventory_tng.core.entry_point import Commands, dispatch

PROG = "python -m inventory_tng.manage"
COMMANDS: Commands = {
    "admins": (
        "inventory_tng.admins.admins_command",
        "lock every administrator out, let one back in, or invite one",
    ),
    "registration-codes": (
        "inventory_tng.devices.registration_codes_command",
        "make or revoke a registration code, or list or revoke its devices",
    ),
    "scan-settings": (
        "inventory_tng.site_settings.scan_settings_command",
        "show or change the scan page's settings",
    ),
    "totp-keys": (
        "inventory_tng.admins.totp_keys_command",
        "make a TOTP key, reseal with the newest, or retire authenticators",
    ),
    "unit-readings": (
        "inventory_tng.catalogue.unit_readings_command",
        "propose or load how units in the imported names are read",
    ),
}


def main(argv: Sequence[str] | None = None) -> int:
    return dispatch(
        argv,
        prog=PROG,
        description="The operator's commands (DEPLOYMENT.md, Operator commands).",
        commands=COMMANDS,
    )


if __name__ == "__main__":
    sys.exit(main())
