"""TOTP keys from the command line (admins/sealed_totp_keys.py;
DEPLOYMENT.md, TOTP keys):

    python -m inventory_tng.manage sealed-totp-keys new     # print a new key
    python -m inventory_tng.manage sealed-totp-keys reseal  # seal all with the newest
    python -m inventory_tng.manage sealed-totp-keys retire-authenticators --unreadable
    python -m inventory_tng.manage sealed-totp-keys retire-authenticators --all
"""

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from inventory_tng.admins import accounts
from inventory_tng.admins.sealed_totp_keys import (
    TotpKeys,
    new_key,
    reseal_authenticators,
)
from inventory_tng.core.mail import Notifier
from inventory_tng.core.operator_commands import run_command, run_command_telling


def parse(argv: Sequence[str], prog: str | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="TOTP keys sealed at rest (DEPLOYMENT.md, TOTP keys).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("new", help="print a new key, for a new version's file")
    commands.add_parser(
        "reseal", help="seal every current authenticator's key with the newest"
    )
    retire = commands.add_parser(
        "retire-authenticators",
        help="retire authenticators, for a lost or leaked key: each is set up again",
    ).add_mutually_exclusive_group(required=True)
    retire.add_argument(
        "--unreadable",
        action="store_true",
        help="those that do not open: their key lost or replaced",
    )
    retire.add_argument("--all", action="store_true", help="every one: a leaked key")
    return parser.parse_args(argv)


def main(argv: Sequence[str], prog: str | None = None) -> int:
    args = parse(argv, prog)
    if args.command == "new":
        print(new_key())
        return 0
    if args.command == "reseal":
        return run_command(_reseal, args)
    return run_command_telling(_retire, args)


def _reseal(session: Session, _args: argparse.Namespace) -> int:
    resealed, unreadable = reseal_authenticators(session, TotpKeys.from_environ())
    print(f"Resealed {resealed}; {unreadable} could not be read.")
    return 1 if unreadable else 0


def _retire(session: Session, args: argparse.Namespace, notify: Notifier) -> int:
    retired, stranded = accounts.retire_authenticators(
        session,
        TotpKeys.from_environ(),
        datetime.now(UTC),
        every=args.all,
        notify=notify,
    )
    # Printed once committed.
    notify.tasks.add_task(print, f"Retired {retired} authenticators.")
    if stranded:
        names = ", ".join(stranded)
        notify.tasks.add_task(print, f"No second factor left, so let them in: {names}.")
    return 0
