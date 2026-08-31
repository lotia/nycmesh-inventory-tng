"""Put a small, made-up scene into a development database, so there is something to look at.

`migrate` leaves every table empty, and an empty database draws as an empty
catalogue, an empty pick-list and a scanner that resolves nothing -- so the
first thing a new contributor sees says almost nothing about what this is. This
writes a few dozen rows instead: a catalogue in three groups, a warehouse with
a shelf in it and a hub, two volunteers, two printed labels, and enough ledger
history that the counts beside the items are not all zero.

It creates no login, deliberately. `seed_integration_data` creates one and is
gated for it; this is meant to be run by hand on whatever development database
you have, so the account stays yours to make with `createsuperuser`.

Every name in here is invented. Nothing pretends to be a real volunteer, a real
site or a real count.

## Development servers only, or an acknowledgement typed on the spot

`settings.DEBUG` off is a refusal, for the reason `seed_integration_data`
gives: this command ships inside the backend image, so it is one wrong
`kubectl` context away from a production pod, and the half of it that is not
guarded by `ledger_is_ours()` -- three categories, six invented items, three
places, two volunteers and two labels -- writes into whatever database
`DATABASE_URL` names.

Its sibling asks for an acknowledgement flag as well, and for a long time this
one did not: a flag has to be typed every time, and this command exists to be
the first thing a new contributor runs -- reached through
`scripts/bootstrap-dev.sh` and one copy-pasted line in README -- so putting a
refusal in front of it would obstruct exactly the path it exists to smooth.

That reasoning still holds, and it is why the flag is not required. `DEBUG` on
its own is enough on a development machine and nothing about that path changed.

What changed is that DEBUG was the ONLY way in, and a demo deployment cannot
have it: a cluster requires `DEBUG` off, so the two rules met and a deployment
somebody wanted to show people had no way to be given anything to look at.
`inventory-tng-w91j.1`. So the flag is the other door, and it is a flag rather
than a variable for the reason `_seeding` gives: a variable is ambient and
would switch this off for every invocation in a cluster from then on, where a
flag is spent per invocation and shows up in the command that ran.

## Running it twice adds nothing

Each catalogue row is fetched or created, and the two transactions are found by
their idempotency key, so a second run finds all of them where the first left
them. The key alone, not the key and the actor: the unique constraint is scoped
to the pair, but a demo volunteer that has since been merged or retired is
replaced by a fresh row on the next run -- which is the first thing
`guides/administrator.md` teaches -- and a lookup carrying that new actor would
match nothing and post the delivery a second time.

## Why the ledger is the one part it will decline to write

A catalogue row somebody did not want can be undone. A movement cannot: the
two ledger tables refuse UPDATE and DELETE outright, which is what decision
0016 settles, so invented stock posted into a database holding real stock could
never be taken back out. This therefore writes the catalogue always and the
ledger only while nothing but its own transactions are in there, and says which
of the two happened.

UNDONE RATHER THAN DELETED, and the distinction is not pedantry. This mints
labels, and `Label.item` is PROTECT, so an invented item cannot be deleted
while its sticker exists -- delete the label first, or mark the row inactive,
which is what an administrator does with a real one anyway. Saying "deleted"
here promised something the guard took away, and on the declined-ledger path,
where nothing else refers to these rows, the label was the only thing standing
in the way.
"""

from collections import Counter
from decimal import Decimal
from typing import Any

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction

from inventory.management.commands import _report, _roster, _seeding, _telemetry
from inventory.models import (
    Category,
    Item,
    Label,
    Location,
    StockMovement,
    StockTransaction,
    Volunteer,
)

# What this command's own transactions are recognised by, both when it is asked
# to run twice and when it is deciding whether the ledger is anybody else's.
# Distinct from StockTransaction.FROM_THE_SHEET, which names imported rows, and
# reserved beside it on the model so that the serializer can refuse a client
# writing one -- without which `ledger_is_ours()` below can be made to lie.
DEMO_KEY_PREFIX = StockTransaction.FROM_THE_DEMO_SEED

CATEGORIES = ("Radios", "Antennas", "Cables and connectors")

# name, category, unit of measure.
ITEMS: tuple[tuple[str, str, str], ...] = (
    ("LiteBeam AC Gen2", "Radios", Item.UnitOfMeasure.EACH),
    ("NanoStation 5AC Loco", "Radios", Item.UnitOfMeasure.EACH),
    ("OmniTik 5 PoE ac", "Radios", Item.UnitOfMeasure.EACH),
    ("Sector antenna, 120 degrees, 5 GHz", "Antennas", Item.UnitOfMeasure.EACH),
    ("Cat6 outdoor cable", "Cables and connectors", Item.UnitOfMeasure.METRE),
    ("RJ45 shielded connector", "Cables and connectors", Item.UnitOfMeasure.EACH),
)

STORE = "Demo store"
SHELF = "Shelf B2"
HUB = "Demo hub"

# name, kind, parent. Nested on purpose: one place with something inside it is
# what shows that a location is a tree rather than a list.
LOCATIONS: tuple[tuple[str, str, str | None], ...] = (
    (STORE, Location.Kind.WAREHOUSE, None),
    (SHELF, Location.Kind.SHELF, STORE),
    (HUB, Location.Kind.HUB, None),
)

# Two, because one volunteer cannot show that a transaction is attributed to
# somebody: with a single row the pick-list has nothing to pick between.
PACKER = "Demo Volunteer"
INSTALLER = "Demo Installer"
VOLUNTEERS = (PACKER, INSTALLER)

# code, item, location, quantity. Fixed rather than minted, so a second run
# finds them and so the two codes can be written down in the guide as
# something to scan. Both are Crockford Base32 as the column requires -- there
# is no letter O in either, only the digit.
LABELS: tuple[tuple[str, str | None, str | None, Decimal | None], ...] = (
    ("DEM0000001", "LiteBeam AC Gen2", None, Decimal(1)),
    ("DEM0000002", None, SHELF, None),
)

# A delivery, arriving from outside the system onto the shelf.
RECEIVED: tuple[tuple[str, Decimal], ...] = (
    ("LiteBeam AC Gen2", Decimal(12)),
    ("NanoStation 5AC Loco", Decimal(6)),
    ("Cat6 outdoor cable", Decimal(305)),
    ("RJ45 shielded connector", Decimal(100)),
)

# And somebody taking a little of it away again, so the counts on the item list
# are not simply the delivery read back.
CHECKED_OUT: tuple[tuple[str, Decimal], ...] = (
    ("LiteBeam AC Gen2", Decimal(2)),
    ("RJ45 shielded connector", Decimal(8)),
)


def categories(added: Counter[str]) -> dict[str, Category]:
    """The groups the items hang under, by name."""
    made = {}
    for name in CATEGORIES:
        # parent=None belongs in the lookup rather than the defaults, for the
        # reason `seed_integration_data` gives about the same call.
        made[name], created = Category.objects.get_or_create(name=name, parent=None)
        added["categories"] += created
    return made


def items(groups: dict[str, Category], added: Counter[str]) -> dict[str, Item]:
    """The catalogue, by name."""
    made = {}
    for name, group, unit in ITEMS:
        made[name], created = Item.objects.get_or_create(
            name=name,
            defaults={"category": groups[group], "unit_of_measure": unit},
        )
        added["items"] += created
        _seeding.revived(made[name])
    return made


def locations(added: Counter[str]) -> dict[str, Location]:
    """The places, by name. Parents first, which is the order LOCATIONS is in."""
    made: dict[str, Location] = {}
    for name, kind, parent in LOCATIONS:
        made[name], created = Location.objects.get_or_create(
            name=name,
            parent=None if parent is None else made[parent],
            defaults={"kind": kind},
        )
        added["locations"] += created
        _seeding.revived(made[name])
    return made


def volunteers(added: Counter[str], *, with_roster: bool = False) -> dict[str, Volunteer]:
    """The pick-list, by display name. `_seeding` says why this is not a get_or_create."""
    made = {}
    for name in VOLUNTEERS:
        made[name], created = _seeding.selectable_volunteer(name)
        added["volunteers"] += created
    if with_roster:
        made.update(demonstration_roster(added))
    return made


def demonstration_roster(added: Counter[str]) -> dict[str, Volunteer]:
    """The invented crowd behind the two, when asked for.

    NOT `selectable_volunteer`, and finding that out is what this docstring is
    for. That helper finds a selectable row of the same display name and
    returns it, which is right everywhere else and wrong here: the two people
    sharing a name are the entire point, and looked up that way the second
    collapses into the first. Seeded through it, the roster came out with
    eighty-six rows, no collisions, and a demonstration with nothing to
    demonstrate.

    So the count is what is made idempotent rather than the name. For each
    display name this counts the selectable rows already wearing it and adds
    only the shortfall, so a second run changes nothing and a name meant to be
    worn twice ends up worn twice.

    An address is written only where the person has one, so the roughly half
    with none are stored as null rather than as an empty string -- which is
    what the pick-list renders as no second line, and is the case the run of
    show has to be honest about.
    """
    made: dict[str, Volunteer] = {}
    wanted: dict[str, list[_roster.Person]] = {}
    for person in _roster.roster():
        wanted.setdefault(person.display_name, []).append(person)

    for name, people in wanted.items():
        existing = list(Volunteer.objects.selectable().filter(display_name=name))
        for person in people[len(existing) :]:
            row = Volunteer.objects.create(display_name=name, email=person.email or None)
            existing.append(row)
            added["volunteers"] += 1
        # By name, so the two sharing one collapse to a single entry here. That
        # is fine: nothing seeds a transaction against them, and the collision
        # is meant to be seen in the pick-list rather than used from here.
        made[name] = existing[0]
    return made


def labels(catalogue: dict[str, Item], places: dict[str, Location], added: Counter[str]) -> None:
    """The two printed stickers: one naming an item, one naming a place."""
    for code, item, place, quantity in LABELS:
        _, created = Label.objects.get_or_create(
            code=code,
            defaults={
                "item": None if item is None else catalogue[item],
                "location": None if place is None else places[place],
                "quantity": quantity,
            },
        )
        added["labels"] += created


def ledger_is_ours() -> bool:
    """Whether everything the ledger holds was put there by this command.

    A transaction with no idempotency key counts as somebody else's, which is
    what a batch submitted through the app looks like.
    """
    return not StockTransaction.objects.exclude(idempotency_key__startswith=DEMO_KEY_PREFIX).exists()


def post(
    key: str,
    kind: str,
    actor: Volunteer,
    lines: tuple[tuple[str, Decimal], ...],
    catalogue: dict[str, Item],
    shelf: Location,
    added: Counter[str],
) -> None:
    """One transaction and the movements under it, or nothing if it is already there.

    Which side of a movement each kind requires is the rule the
    `stock_movement_matches_kind` trigger enforces: a receipt arrives from
    outside and so has only a destination, a check out leaves and so has only a
    source.

    Found by the key alone and not by `get_or_create(actor=..., key=...)`, for
    the reason the module docstring gives: the actor is not stable across runs
    and the ledger cannot be corrected. `filter().first()` rather than `get()`,
    because a database seeded before that was true may already hold the pair
    this would otherwise raise on.
    """
    into = kind == StockTransaction.Kind.RECEIPT
    full_key = f"{DEMO_KEY_PREFIX}{key}"
    if StockTransaction.objects.filter(idempotency_key=full_key).exists():
        return
    recorded = StockTransaction.objects.create(
        actor=actor,
        idempotency_key=full_key,
        kind=kind,
        reason="Demo data",
    )
    added["transactions"] += 1
    for name, quantity in lines:
        StockMovement.objects.create(
            transaction=recorded,
            item=catalogue[name],
            quantity=quantity,
            from_location=None if into else shelf,
            to_location=shelf if into else None,
        )
        added["movements"] += 1


def seed(added: Counter[str], *, with_roster: bool = False) -> bool:
    """Write the scene, returning whether the ledger half of it was written."""
    catalogue = items(categories(added), added)
    places = locations(added)
    people = volunteers(added, with_roster=with_roster)
    labels(catalogue, places, added)

    if not ledger_is_ours():
        return False
    post(
        "receipt",
        StockTransaction.Kind.RECEIPT,
        people[PACKER],
        RECEIVED,
        catalogue,
        places[SHELF],
        added,
    )
    post(
        "checkout",
        StockTransaction.Kind.CHECKOUT,
        people[INSTALLER],
        CHECKED_OUT,
        catalogue,
        places[SHELF],
        added,
    )
    return True


ROWS = ("categories", "items", "locations", "volunteers", "labels", "transactions", "movements")

# What the section of counts is headed, and so what a reader of the output
# finds it by -- the tests included.
HEADING = "Added by this run"


ACKNOWLEDGEMENT = "--i-know-this-writes-invented-data"

# The flag that adds the demonstration pick-list. Off by default, because the
# path this command exists to smooth is a new contributor's and they do not
# need eighty-eight invented people to see what the app is. It is on for the
# consultation, whose every figure is measured against a roster that size --
# `_roster` says which figures and why they cannot be shown against two rows.
ROSTER_FLAG = "--with-demo-roster"


class Command(BaseCommand):
    help = "Create a small invented catalogue, two places, two volunteers, two labels and some stock."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            ACKNOWLEDGEMENT,
            # Named rather than left to be re-derived. argparse turns the flag
            # into `i_know_this_writes_invented_data` on its own, and computing
            # that string a second time to read it back is a copy of a rule
            # argparse already owns.
            dest="acknowledged",
            action="store_true",
            help=(
                "Admits this on a server where DEBUG is off -- a demo deployment. "
                "Confirms you meant to write invented data into whatever database DATABASE_URL names."
            ),
        )
        parser.add_argument(
            ROSTER_FLAG,
            dest="roster",
            action="store_true",
            help=(
                "Also write the invented pick-list the access consultation is demonstrated against: "
                f"{_roster.NAMES} made-up people, two names worn by two of them, and the measured "
                "proportion carrying no address. Everyone in it is fiction."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        # DEBUG alone on a development machine, which is the path README hands
        # a newcomer and which gains no extra words. The flag is the other way
        # in and exists for one case: a demo deployment, where DEBUG is off
        # because a cluster requires it and there is still nothing to look at.
        # See "Development servers only" above for why the guard is shaped this
        # way rather than as a second variable.
        _seeding.refuse_unless_a_development_server(
            f"This command writes an invented catalogue into whatever database DATABASE_URL names. "
            f"If that is what you want on this server, pass {ACKNOWLEDGEMENT}.",
            acknowledged=options.get("acknowledged", False),
        )
        added: Counter[str] = Counter()
        with _telemetry.running(_telemetry.named(self)) as counted:
            # One transaction: a run that fails half way through would otherwise
            # leave a catalogue with no stock in it and no sign of why.
            with transaction.atomic():
                posted = seed(added, with_roster=options.get("roster", False))
            # Built once and used twice, the way every sibling command does it:
            # what is recorded and what is printed are the same figures, so
            # producing them twice is two chances for them to differ.
            section = (HEADING, [(row, added[row]) for row in ROWS])
            counted.update(_telemetry.figures(section))

        for line in _report.render(*section):
            self.stdout.write(line)
        self.stdout.write("")
        if not posted:
            self.stdout.write(
                "The ledger already holds transactions this command did not write, so no stock was "
                "invented on top of them. The catalogue above is there either way.",
            )
        self.stdout.write(f"Labels to scan or type in: {', '.join(code for code, *_ in LABELS)}")
