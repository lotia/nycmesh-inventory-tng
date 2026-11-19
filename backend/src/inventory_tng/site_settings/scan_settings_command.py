"""The scan page's settings from the command line, the operator's fallback
(DEPLOYMENT.md, Scan page settings), as administrators use the Site
settings page:

    python -m inventory_tng.manage scan-settings show
    python -m inventory_tng.manage scan-settings set --name-check-minutes 20

Changes are history, as the command line (system:cli).
"""

import argparse
from collections.abc import Sequence

import structlog
from sqlalchemy.orm import Session

from inventory_tng.core.db_sessions import CLI_ACTOR, set_actor
from inventory_tng.core.operator_commands import run_command
from inventory_tng.models.base import whole
from inventory_tng.models.site_settings import NAME_CHECK_MINUTES, ScanSettings
from inventory_tng.site_settings import scan_settings

log = structlog.get_logger(__name__)


def minutes(text: str) -> int:
    """A number of minutes in the range ScanSettings holds."""
    if (number := whole(text, *NAME_CHECK_MINUTES)) is None:
        low, high = NAME_CHECK_MINUTES
        raise argparse.ArgumentTypeError(
            f"must be a whole number from {low} to {high}, not {text}"
        )
    return number


def parse(argv: Sequence[str], prog: str | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=prog)
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


def main(argv: Sequence[str], prog: str | None = None) -> int:
    args = parse(argv, prog)
    return run_command(run, args)


def run(session: Session, args: argparse.Namespace) -> int:
    if args.command == "set":
        set_actor(session, CLI_ACTOR)
        # No form: what the operator types is meant to replace what is there.
        current = scan_settings.change(session, args.name_check_minutes, seen=None)
        session.flush()
        log.info("scan settings changed", name_check_minutes=args.name_check_minutes)
    else:
        current = session.get_one(ScanSettings, 1)
    print(f"name check: {scan_settings.describe(current.name_check_minutes)}")
    return 0
