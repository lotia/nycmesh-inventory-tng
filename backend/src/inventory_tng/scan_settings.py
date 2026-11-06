"""How the scan page behaves, from the command line: the operator's fallback,
as administrators use the Settings page (admin/settings.py):

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
from inventory_tng.db import CLI_ACTOR, set_actor
from inventory_tng.models import INTEGER_MAX, ScanSettings

log = structlog.get_logger(__name__)


def whole(text: str) -> int:
    if not text.isdigit() or int(text) > INTEGER_MAX:
        raise argparse.ArgumentTypeError(
            f"must be a whole number from 0 to {INTEGER_MAX}, not {text}"
        )
    return int(text)


def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m inventory_tng.scan_settings")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show", help="the settings now")
    change = commands.add_parser("set", help="change one or more settings")
    change.add_argument(
        "--name-check-minutes",
        type=whole,
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
    current = session.get_one(ScanSettings, 1)
    if args.command == "set":
        set_actor(session, CLI_ACTOR)
        current.name_check_minutes = args.name_check_minutes
        session.flush()
        log.info("scan settings changed", name_check_minutes=args.name_check_minutes)
    print(f"name check: {describe(current.name_check_minutes)}")
    return 0


def describe(minutes: int) -> str:
    if minutes == 0:
        return "never asks"
    return f"asks after {minutes} minute{'s' if minutes != 1 else ''} unused"


if __name__ == "__main__":
    raise SystemExit(main())
