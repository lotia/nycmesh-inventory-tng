"""Registration codes and devices, from the command line, until administrator
pages exist (inventory-tng-8i4.14):

    uv run python -m inventory_tng.registration_codes create --uses 1 --days 1
    uv run python -m inventory_tng.registration_codes revoke CODE_ID
    uv run python -m inventory_tng.registration_codes devices CODE_ID
    uv run python -m inventory_tng.registration_codes revoke-device DEVICE_ID

`create` prints the phrase, the only time it is shown. With --base-url it also
prints the link and its QR code, for a phone's camera.
"""

import argparse
import sys
from collections.abc import Callable, Sequence
from datetime import timedelta
from urllib.parse import urlencode

import segno
from sqlalchemy.orm import Session

from inventory_tng import devices
from inventory_tng.command import run_command
from inventory_tng.db import CLI_ACTOR, set_actor

# Where the QR code's link goes: the registration page (inventory-tng-8i4.13).
REGISTER_PATH = "/register"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m inventory_tng.registration_codes")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="a new code, printed once")
    create.add_argument(
        "--uses", type=positive(int), default=1, help="devices it may register"
    )
    create.add_argument(
        "--days", type=positive(float), default=1, help="how long it lasts"
    )
    create.add_argument("--label", help="what it is for, such as an event")
    create.add_argument("--base-url", help="the site, to print a link and QR code")
    commands.add_parser("revoke", help="stop a code registering").add_argument(
        "code_id", type=int
    )
    commands.add_parser("devices", help="a code's devices").add_argument(
        "code_id", type=int
    )
    commands.add_parser("revoke-device", help="refuse a device").add_argument(
        "device_id", type=int
    )
    args = parser.parse_args(argv)

    return run_command(run, args)


def positive[T: (int, float)](kind: type[T]) -> Callable[[str], T]:
    def parse(text: str) -> T:
        value = kind(text)
        if value <= 0:
            raise argparse.ArgumentTypeError(f"must be more than 0, not {text}")
        return value

    return parse


def run(session: Session, args: argparse.Namespace) -> int:
    # In the history as the command line: there is no administrator id yet.
    set_actor(session, CLI_ACTOR)
    match args.command:
        case "create":
            code, phrase = devices.create_code(
                session,
                max_uses=args.uses,
                lifetime=timedelta(days=args.days),
                label=args.label,
            )
            print(f"Code {code.id}, until {code.expires_at:%Y-%m-%d %H:%M %Z}")
            print(f"Phrase: {phrase}")
            if args.base_url:
                link = args.base_url.rstrip("/") + REGISTER_PATH
                link += "?" + urlencode({"code": phrase})
                print(f"Link: {link}")
                segno.make(link).terminal(compact=True)
            return 0
        case "revoke":
            return _done(devices.revoke_code(session, args.code_id), "code")
        case "revoke-device":
            return _done(devices.revoke_device(session, args.device_id), "device")
        case _:
            listed = devices.devices_registered_with(session, args.code_id)
            if listed is None:
                return _done(False, "code")
            for device, before in listed:
                when = f"{before} before revocation" if before is not None else ""
                state = "revoked" if device.revoked_at else "current"
                print(
                    f"{device.id}\t{device.created_at:%Y-%m-%d %H:%M}\t{state}\t"
                    f"{device.first_seen_from or ''}\t{when}"
                )
            return 0


def _done(changed: bool, what: str) -> int:
    if not changed:
        print(f"No current {what} with that id.", file=sys.stderr)
    return 0 if changed else 1


if __name__ == "__main__":
    sys.exit(main())
