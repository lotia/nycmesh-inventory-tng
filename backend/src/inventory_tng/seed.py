"""A development database to work in: an administrator, some catalogue rows
and stock. How it behaves: CONTRIBUTING.md, Some data to work with.

    uv run python -m inventory_tng.seed [--password PASSWORD]
"""

import argparse
import secrets
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import accounts
from inventory_tng.command import run_command
from inventory_tng.db import CLI_ACTOR, Base, exists_where, set_actor
from inventory_tng.ledger import Batch, record
from inventory_tng.models import (
    Admin,
    Category,
    Item,
    ItemPackage,
    Label,
    Location,
    Volunteer,
)

log = structlog.get_logger(__name__)

USERNAME = "dev"
CATEGORIES = ("Radios", "Cables", "Mounts")
# Name, category, unit, manufacturer, part number.
# The item the seed labels, which the browser tests' camera reads.
LABELLED = "LiteBeam 5AC Gen2"
ITEMS = (
    (LABELLED, "Radios", "each", "Ubiquiti", "LBE-5AC-Gen2"),
    ("NanoStation 5AC Loco", "Radios", "each", "Ubiquiti", "Loco5AC"),
    ("Outdoor Cat6 cable", "Cables", "m", None, None),
    ("Pole mount", "Mounts", "each", None, None),
)
# The cable comes in boxes.
PACKAGE = ("Outdoor Cat6 cable", "Box of 305 m", Decimal(305))
# Name, kind, the place it is in.
LOCATIONS = (
    ("Supernode 1", "store", None),
    ("Shelf A", "area", "Supernode 1"),
    ("Van", "vehicle", None),
)
VOLUNTEERS = ("Sam Example", "Riley Example")
# What was received onto Shelf A, by item.
STOCK = {
    "LiteBeam 5AC Gen2": "10",
    "NanoStation 5AC Loco": "6",
    "Outdoor Cat6 cable": "305",
    "Pole mount": "4",
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m inventory_tng.seed")
    parser.add_argument("--password", help="the administrator's; else one is made")
    args = parser.parse_args(argv)
    return run_command(run, args, development_only=True)


def run(session: Session, args: argparse.Namespace) -> int:
    set_actor(session, CLI_ACTOR)
    given = args.password
    if given and (problem := accounts.password_problem(given, given)):
        print(problem, file=sys.stderr)
        return 1
    admin = session.scalar(select(Admin).where(Admin.username == USERNAME))
    seeding = admin is None
    if seeding:
        # Someone works in it already: not the seed's to fill.
        if exists_where(session, Admin.id.is_not(None)):
            log.warning("seed refused")
            print(
                "This database has administrators already, and was never seeded.",
                file=sys.stderr,
            )
            return 1
        admin = Admin(username=USERNAME)
        session.add(admin)
        session.flush()
        accounts.sign_in_settings(session).totp_policy = "optional"
        _catalogue(session)
        log.info("seeded", admin_id=admin.id)
    elif given is None and accounts.has_password(session, admin.id):
        print(f"Seeded already. Sign in at /admin as {USERNAME}, or set a password")
        print("with --password.")
        return 0
    password = given or secrets.token_urlsafe(16)
    accounts.set_password(session, admin.id, password, datetime.now(UTC))
    print(f"Sign in at /admin as {USERNAME}, password {password}")
    if seeding:
        print("Sign-in codes are optional here: turn them on under Settings.")
    return 0


def _catalogue(session: Session) -> None:
    categories = {name: _add(session, Category(name=name)) for name in CATEGORIES}
    items = {
        name: _add(
            session,
            Item(
                name=name,
                category_id=categories[category].id,
                unit=unit,
                manufacturer=maker,
                part_number=part,
            ),
        )
        for name, category, unit, maker, part in ITEMS
    }
    item, package, size = PACKAGE
    _add(session, ItemPackage(item_id=items[item].id, name=package, size=size))
    places: dict[str, Location] = {}
    for name, kind, parent in LOCATIONS:
        parent_id = places[parent].id if parent else None
        places[name] = _add(
            session, Location(name=name, kind=kind, parent_id=parent_id)
        )
    volunteers = [_add(session, Volunteer(name=name)) for name in VOLUNTEERS]
    _add(session, Label(item_id=items[LABELLED].id))
    _add(session, Label(location_id=places["Shelf A"].id))
    received = Batch(
        key=uuid.uuid4(),
        kind="receipt",
        volunteer_id=volunteers[0].id,
        to_location_id=places["Shelf A"].id,
        lines=[
            {"item_id": items[name].id, "quantity": quantity}
            for name, quantity in STOCK.items()
        ],
    )
    record(session, received, device_id=None)


def _add[M: Base](session: Session, row: M) -> M:
    session.add(row)
    session.flush()
    return row


if __name__ == "__main__":
    sys.exit(main())
