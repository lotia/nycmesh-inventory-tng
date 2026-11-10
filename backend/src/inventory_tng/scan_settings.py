"""How the scan page behaves: every write to the scan_settings table, by
the Site settings page (admin/settings.py) and from the command line, the
operator's fallback:

    uv run python -m inventory_tng.scan_settings show
    uv run python -m inventory_tng.scan_settings set --name-check-minutes 20

The page reads them with the catalogue, which an open page fetches again
every ten minutes. Changes are history, as the command line (system:cli).
"""

import argparse
from collections.abc import Sequence

import structlog
from sqlalchemy.orm import Session

from inventory_tng.command import run_command
from inventory_tng.db import CLI_ACTOR, Seen, set_actor, settings_row
from inventory_tng.models import NAME_CHECK_MINUTES, ScanSettings, whole

log = structlog.get_logger(__name__)


def minutes(text: str) -> int:
    """A number of minutes in the range ScanSettings holds."""
    if (number := whole(text, *NAME_CHECK_MINUTES)) is None:
        low, high = NAME_CHECK_MINUTES
        raise argparse.ArgumentTypeError(
            f"must be a whole number from {low} to {high}, not {text}"
        )
    return number


def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m inventory_tng.scan_settings")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show", help="the settings now")
    change = commands.add_parser("set", help="change one or more settings")
    change.add_argument(
        "--name-check-minutes",
        type=minutes,
        help="unused this long, the page asks whose name it is; 0 never asks",
    )
    args = parser.parse_args(argv)
    if args.command == "set" and args.name_check_minutes is None:
        parser.error("set needs a setting to change, such as --name-check-minutes")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse(argv)
    return run_command(run, args)


def run(session: Session, args: argparse.Namespace) -> int:
    if args.command == "set":
        set_actor(session, CLI_ACTOR)
        # No form: what the operator types is meant to replace what is there.
        current = change(session, args.name_check_minutes, seen=None)
        session.flush()
        log.info("scan settings changed", name_check_minutes=args.name_check_minutes)
    else:
        current = session.get_one(ScanSettings, 1)
    print(f"name check: {describe(current.name_check_minutes)}")
    return 0


def change(session: Session, name_check_minutes: int, *, seen: Seen) -> ScanSettings:
    """The settings, changed, in the range its check constraint holds
    (NAME_CHECK_MINUTES), which the page and the command check first."""
    current = settings_row(session, ScanSettings, seen=seen)
    current.name_check_minutes = name_check_minutes
    return current


def describe(minutes: int) -> str:
    if minutes == 0:
        return "never asks"
    return f"asks after {minutes} minute{'s' if minutes != 1 else ''} unused"


if __name__ == "__main__":
    raise SystemExit(main())
