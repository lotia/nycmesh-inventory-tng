"""The operator's break-glass, for when the administrator pages cannot be
trusted or nobody can sign in to them (DEPLOYMENT.md, Break-glass;
docs/decisions/0018). Run where the app runs, with its settings:

    python -m inventory_tng.admins lockdown --expect 3
    python -m inventory_tng.admins let-in USERNAME
    python -m inventory_tng.admins invite USERNAME [--email ADDRESS]

`lockdown` retires every current administrator and revokes every
registration code; --expect is how many administrators you believe are
current, and it refuses if that is wrong. `let-in` brings one back, retired
or not, with everything they signed in with removed; `invite` makes a new
one. Both print a one-use link, lasting an hour, the only time it is shown:
pass it on yourself. Links start with --base-url if given, else with
SITE_URL. Every administrator with an address is told by mail.
"""

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from inventory_tng import accounts
from inventory_tng.command import run_command_telling
from inventory_tng.mail import Notifier
from inventory_tng.settings import Settings
from inventory_tng.sign_in import reset_url


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m inventory_tng.admins")
    commands = parser.add_subparsers(dest="command", required=True)
    lockdown = commands.add_parser(
        "lockdown", help="retire every administrator, revoke every code"
    )
    lockdown.add_argument(
        "--expect",
        type=int,
        required=True,
        help="how many administrators are current, to confirm",
    )
    let_in = commands.add_parser("let-in", help="let one administrator back in")
    let_in.add_argument("username")
    invite = commands.add_parser("invite", help="a new administrator")
    invite.add_argument("username")
    invite.add_argument("--email", help="for resets and notices")
    for command in (let_in, invite):
        command.add_argument("--base-url", help="the site, if not SITE_URL")
    return run_command_telling(run, parser.parse_args(argv))


def run(session: Session, args: argparse.Namespace, notify: Notifier) -> int:
    # Printed once committed (notify.tasks): a link printed before would not
    # exist if the commit failed.
    def done(line: str) -> None:
        notify.tasks.add_task(print, line)

    now = datetime.now(UTC)
    try:
        if args.command == "lockdown":
            retired = accounts.lock_down(session, args.expect, now, notify)
            done(f"Retired {len(retired)} administrators.")
            return 0
        mail = Settings.from_environ().mail
        site = args.base_url or (mail.site_url if mail else None)
        if not site:
            print("Give --base-url: there is no SITE_URL.", file=sys.stderr)
            return 1
        if args.command == "let-in":
            admin, token = accounts.let_in(session, args.username, now, notify)
        else:
            admin, token = accounts.invite_by_operator(
                session, args.username, args.email, now, notify
            )
    except accounts.OperatorRefused as refused:
        print(refused, file=sys.stderr)
        return 1
    done(f"A link for {admin.username}, once, within the hour:")
    done(reset_url(site, token))
    return 0


if __name__ == "__main__":
    sys.exit(main())
