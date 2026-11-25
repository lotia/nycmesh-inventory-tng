"""A development database to work in: an administrator, some catalogue rows
and stock. How it behaves: CONTRIBUTING.md, Some data to work with.
Run by `python -m devtools database seed` (database.py).
"""

import argparse
import secrets
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.admins import accounts
from inventory_tng.catalogue import categories, items
from inventory_tng.core.db_locking import settings_row
from inventory_tng.core.db_sessions import CLI_ACTOR, exists_where, set_actor
from inventory_tng.labels import labels
from inventory_tng.models.admins import Admin
from inventory_tng.models.places import Location
from inventory_tng.models.site_settings import SignInSettings
from inventory_tng.places import locations
from inventory_tng.scan.batch import record
from inventory_tng.scan.batch_schemas import Batch
from inventory_tng.volunteers import volunteers

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
        settings_row(session, SignInSettings, seen=None).totp_policy = "optional"
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
        print("Sign-in codes are optional here: turn them on under Site settings.")
    return 0


def _catalogue(session: Session) -> None:
    """Through the domain functions, as an administrator's pages would."""
    kinds = {name: categories.add(session, name) for name in CATEGORIES}
    things = {
        name: items.add(
            session,
            items.Fields.new(
                name=name,
                category_id=kinds[category].id,
                unit=unit,
                manufacturer=maker,
                part_number=part,
                # The cable's opened boxes are followed (docs/decisions/0020).
                track_opened=name == PACKAGE[0],
            ),
        )
        for name, category, unit, maker, part in ITEMS
    }
    item, package, size = PACKAGE
    box = items.add_package(session, things[item], package, size, shown_unit=None)
    places: dict[str, Location] = {}
    for name, kind, parent in LOCATIONS:
        parent_id = places[parent].id if parent else None
        places[name] = locations.add(session, name, kind, parent_id)
    people = [volunteers.add(session, name, None) for name in VOLUNTEERS]
    labels.make_for_item(session, things[LABELLED].id)
    labels.make_for_place(session, places["Shelf A"].id)
    received = Batch(
        key=uuid.uuid4(),
        kind="receipt",
        volunteer_id=people[0].id,
        to_location_id=places["Shelf A"].id,
        # The cable arrives as a sealed box (docs/decisions/0020).
        lines=[
            {
                "item_id": things[name].id,
                "quantity": quantity,
                "unit": things[name].unit,
                **(
                    {"package_id": box.id, "package_size": size} if name == item else {}
                ),
            }
            for name, quantity in STOCK.items()
        ],
    )
    record(session, received, device_id=None)
