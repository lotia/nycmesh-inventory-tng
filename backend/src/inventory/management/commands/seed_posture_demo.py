"""Put the scene the access-posture demo is performed against into a development database.

PROVISIONAL, like everything else under `inventory_tng.postures`.
`inventory-tng-81f7.3` is the demo this seeds and `inventory-tng-81f7.4` is
what deletes both once `inventory-tng-81f7` has been settled.

## Everybody in here is invented, and that is the one instruction that cannot
## be waived

The demo puts the entire pick-list on a projector, in a room that may hold the
people on it and that may be recorded. A privacy consultation that leaks the
data it was convened to protect has caused the harm it exists to prevent, and
there is no taking it back afterwards. So the names below are generated from
word lists and the addresses are all under RFC 2606's reserved names, which
resolve nowhere and belong to nobody.

Item names and places are real, and deliberately: the friction argument only
lands if the screen looks like the shelf. A place is not a person.

## What the scene has to contain, and why each part of it is load-bearing

ABOUT EIGHTY VOLUNTEERS, because act one's whole effect is a terminal scrolling
past a roster. Two dozen reads as test data.

ROUGHLY 45% WITH NO ADDRESS AT ALL, matching the proportion
`Volunteer.NULL_WHEN_BLANK` records of the historical rows. Act three's honest
drawback -- that identifying yourself by typing your address serves only the
people who ever gave one -- depends on this being true on screen rather than
asserted from a slide.

TWO PAIRS SHARING A DISPLAY NAME, of which exactly ONE collides under the mask.
Act two searches the colliding pair and lets the room look at two identical
strings before anybody says anything; act three searches the separated pair and
types one of their addresses in full, getting exactly one row back. Both pairs
are named below, and a test asserts the collision rather than trusting that the
addresses were chosen carefully.

## Running it twice adds nothing, and --undo takes it back out

Each person is found by their address where they gave one and by their name
where they did not, so a second run finds everybody the first run left. `--undo`
removes them again.

`--with-stock` is separate, and asks for something that CANNOT be undone: the
two ledger tables refuse UPDATE and DELETE outright (decision 0016), so
invented movements are in that database for good. It exists for the reserve act
that joins the roster against the locations against the catalogue, and the
command says what it is about to do. Without it nothing here writes a ledger
row, and `--undo` is a clean removal.
"""

import random
from collections import Counter
from decimal import Decimal
from typing import Any

from django.core.management.base import BaseCommand, CommandParser
from django.db import transaction

from inventory.management.commands import _report, _seeding, _telemetry, seed_demo_data
from inventory.models import Location, StockMovement, StockTransaction, Volunteer
from inventory.sheet import Report

# Fixed, so every run of this command and every run of its tests produces the
# same eighty people. The presenter has to be able to say "search for Sean"
# from a runbook written days earlier, and a roster that was different each
# morning would make the run of show unrehearsable.
SEED = 81_073

# Reserved by RFC 2606 and RFC 6761: nothing under any of these resolves, and
# none of them can ever be registered by anybody. Several of them, with
# different initials and different last labels, because the mask is built from
# exactly those two characters -- a scene where every address was
# `example.com` would collapse under masking for a reason that is an artefact
# of the seed rather than a property of masking.
DOMAINS = (
    "quartzmail.example",
    "heronpost.test",
    "bodegamail.invalid",
    "tidewater.example",
    "northmail.test",
)

# Invented, both lists. Ordinary enough that a projector full of them reads as
# a volunteer roster and not as a word game.
FIRST_NAMES = (
    "Amara",
    "Beatriz",
    "Caleb",
    "Dilnoza",
    "Esi",
    "Farrukh",
    "Gita",
    "Hana",
    "Ibrahim",
    "Jonah",
    "Kwame",
    "Lucia",
    "Mira",
    "Nadia",
    "Omar",
    "Petra",
    "Quique",
    "Rosa",
    "Simeon",
    "Tariq",
    "Ulla",
    "Vikram",
    "Wren",
    "Xiomara",
    "Yusuf",
    "Zofia",
    "Anton",
    "Bea",
    "Cyrus",
    "Delia",
    "Eli",
    "Fatou",
    "Gabor",
    "Hedy",
    "Ines",
    "Joaquin",
    "Kiran",
    "Leena",
    "Marek",
    "Noor",
)
SURNAMES = (
    "Achebe",
    "Bergstrom",
    "Calloway",
    "Duarte",
    "Enriquez",
    "Fitzhugh",
    "Grenfell",
    "Halloran",
    "Ibarra",
    "Jarvis",
    "Kaminski",
    "Lindqvist",
    "Mbeki",
    "Novotny",
    "Okafor",
    "Pemberton",
    "Quintero",
    "Rasmussen",
    "Sandoval",
    "Tremblay",
    "Ustinov",
    "Vasquez",
    "Whitfield",
    "Xu",
    "Yamamoto",
    "Zaragoza",
    "Barros",
    "Chaudhry",
    "Delacroix",
    "Eskildsen",
)

# How many people the roster carries, and how many of them never gave an
# address. The second is the figure the demo's third act is honest about, so it
# is a proportion of the first rather than a number typed twice.
ROSTER = 80
WITHOUT_AN_ADDRESS = round(ROSTER * 0.45)

# THE TWO PAIRS, written out rather than generated, because the demo depends on
# exactly what they render as and a generator would be one refactor away from
# separating them. Both people in the first pair mask to the same string --
# same first letter of the local part, same mail provider, same last label --
# and a test asserts it. The second pair is two addresses that stay apart, so
# act three can type one in full and get one row.
COLLIDING_NAME = "Sean Whitfield"
COLLIDING = (
    (COLLIDING_NAME, "sean@quartzmail.example"),
    (COLLIDING_NAME, "s.whitfield@quartzmail.example"),
)
SEPARATED_NAME = "Priya Raman"
SEPARATED = (
    (SEPARATED_NAME, "priya@heronpost.test"),
    (SEPARATED_NAME, "raman@tidewater.example"),
)

# Who holds a custody location, named by ADDRESS rather than by display name.
# The addresses are unique and the names deliberately are not -- both of these
# are shared by two people, which is the point of them -- so a lookup by name
# is a lookup that can find the wrong row, or none: a database already holding
# somebody at `sean@quartzmail.example` under a different spelling made the
# search raise `StopIteration` inside the transaction, with no sentence saying
# what had clashed.
CUSTODY_HOLDERS = (COLLIDING[0][1], SEPARATED[0][1])

# What each of those volunteers is holding at home, for the same act. Small
# quantities of real hardware, and drawn from what `seed_demo_data`'s own
# delivery put on the shelf -- see `stock`, which posts that delivery first if
# nobody has.
HELD_AT_HOME: tuple[tuple[str, Decimal], ...] = (
    ("LiteBeam AC Gen2", Decimal(2)),
    ("RJ45 shielded connector", Decimal(25)),
)


def people() -> list[tuple[str, str | None]]:
    """The roster, as (display name, address) pairs, the same every run.

    The four deliberate rows are placed first and the rest are generated around
    them, so a change to how names are drawn cannot quietly move the pair the
    runbook tells a presenter to search for.
    """
    drawn = random.Random(SEED)
    roster: list[tuple[str, str | None]] = [*COLLIDING, *SEPARATED]
    taken = {name for name, _ in roster}
    used = {email for _, email in roster}
    while len(roster) < ROSTER:
        first, last = drawn.choice(FIRST_NAMES), drawn.choice(SURNAMES)
        name = f"{first} {last}"
        if name in taken:
            continue
        taken.add(name)
        # The people with no address are drawn from the same stream rather than
        # sliced off the end, so they are scattered through the pick-list the
        # way they are in the real rows -- a block of nameless-looking entries
        # at the bottom of the list would read as a seeding artefact.
        if len(roster) >= ROSTER - WITHOUT_AN_ADDRESS:
            roster.append((name, None))
            continue
        # The bare first name where it is still free and `first.last`
        # otherwise, because two people called Amara on one mail provider is
        # ONE address and the second of them would silently not be created --
        # a roster three people short of the number the runbook promises, for
        # a reason nobody would look for. Both shapes are ordinary, and the
        # mix is what the measurement found in the real rows: the first name
        # is inside the local part either way.
        domain = drawn.choice(DOMAINS)
        address = f"{first.lower()}@{domain}"
        if address in used:
            address = f"{first.lower()}.{last.lower()}@{domain}"
        used.add(address)
        roster.append((name, address))
    return roster


def found(display_name: str, email: str | None) -> Volunteer | None:
    """Whoever a previous run of this left, if it left one.

    By the address where there is one, because that column is unique and is
    what makes a second run a no-op. By the name where there is none, which is
    weaker -- a display name is deliberately not unique -- and is the best
    available for the 45%; it is narrowed to rows carrying no address and no
    sheet key, so it cannot adopt somebody the workbook import wrote.
    """
    if email is not None:
        return Volunteer.objects.filter(email=email).first()
    return Volunteer.objects.filter(display_name=display_name, email__isnull=True, sheet_key__isnull=True).first()


def roster(added: Counter[str]) -> dict[str, Volunteer]:
    """Everybody, created or found. Keyed by address, or by name where there is none.

    A row a previous run left RETIRED is brought back rather than stepped over,
    which is `_seeding.revived`'s argument about items and locations arriving
    for people: `--undo` retires whoever the ledger refers to, so without this
    a second run reported nothing added while the pick-list was short of the
    number the runbook promises -- and a custody location cannot be revived
    while its holder is not selectable, so the person has to come back first.
    """
    seeded: dict[str, Volunteer] = {}
    for display_name, email in people():
        person = found(display_name, email)
        if person is None:
            person = Volunteer.objects.create(display_name=display_name, email=email)
            added["volunteers"] += 1
        elif not person.active:
            person.active = True
            person.save(update_fields=["active"])
            added["volunteers revived"] += 1
        seeded[email or display_name] = person
    return seeded


def custody(seeded: dict[str, Volunteer], added: Counter[str]) -> list[Location]:
    """One custody location per holder, named after them because it has to say who.

    The first address of each deliberate pair holds one, so the reserve act has
    two named people with hardware at home and the room can watch three
    reasonable requests join into one answer nobody would publish.
    """
    places = []
    for address in CUSTODY_HOLDERS:
        holder = seeded[address]
        place, created = Location.objects.get_or_create(
            name=f"{holder.display_name} (custody)",
            parent=None,
            defaults={"kind": Location.Kind.VOLUNTEER_CUSTODY, "held_by": holder},
        )
        added["custody locations"] += created
        _seeding.revived(place)
        places.append(place)
    return places


def stock(places: list[Location], added: Counter[str]) -> bool:
    """Move a little hardware off the demo shelf and into somebody's home.

    Declines where the ledger holds anything this project's seeds did not
    write, for the reason `seed_demo_data` gives at length: a movement cannot
    be taken back out.

    THE DELIVERY IS POSTED FIRST, and leaving it out was a defect that could
    not be undone. On a database where `seed_demo_data` had never run,
    `ledger_is_ours()` is true of an empty ledger, so this checked stock out of
    a shelf nothing had ever put stock on -- driving the balances permanently
    negative (-4 LiteBeams, -50 connectors, measured) on a table that refuses
    DELETE. Its own key means a second run, and a database where that command
    has already run, both find it there and post nothing.
    """
    if not seed_demo_data.ledger_is_ours():
        return False
    catalogue = seed_demo_data.items(seed_demo_data.categories(added), added)
    shelf = seed_demo_data.locations(added)[seed_demo_data.SHELF]
    seed_demo_data.post(
        "receipt",
        StockTransaction.Kind.RECEIPT,
        Volunteer.objects.get(email=CUSTODY_HOLDERS[0]),
        seed_demo_data.RECEIVED,
        catalogue,
        shelf,
        added,
    )
    for place in places:
        key = f"{seed_demo_data.DEMO_KEY_PREFIX}custody:{place.pk}"
        if StockTransaction.objects.filter(idempotency_key=key).exists():
            continue
        recorded = StockTransaction.objects.create(
            actor=place.held_by,
            idempotency_key=key,
            kind=StockTransaction.Kind.CHECKOUT,
            reason="Posture demo",
        )
        added["transactions"] += 1
        for name, quantity in HELD_AT_HOME:
            StockMovement.objects.create(
                transaction=recorded,
                item=catalogue[name],
                quantity=quantity,
                from_location=shelf,
                to_location=place,
            )
            added["movements"] += 1
    return True


def undone(added: Counter[str]) -> None:
    """Take the roster back out, and say what the ledger would not let go.

    A volunteer the ledger refers to cannot be deleted -- `actor` is PROTECT --
    so this retires those instead, which is what an administrator does with a
    real duplicate anyway and what `guides/administrator.md` teaches. Their
    custody locations are retired with them, because retiring the holder of an
    active one is refused for the reason `VolunteerDetailSerializer.validate`
    gives.
    """
    for display_name, email in people():
        person = found(display_name, email)
        if person is None:
            continue
        held = list(person.custody_locations.all())  # ty: ignore[unresolved-attribute]
        if person.transactions.exists():  # ty: ignore[unresolved-attribute]
            for place in held:
                if place.active:
                    place.active = False
                    place.save(update_fields=["active"])
                    added["custody locations retired"] += 1
            person.active = False
            person.save(update_fields=["active"])
            added["volunteers retired"] += 1
            continue
        for place in held:
            place.delete()
            added["custody locations removed"] += 1
        person.delete()
        added["volunteers removed"] += 1


ADDED = ("volunteers", "volunteers revived", "custody locations", "transactions", "movements")
REMOVED = (
    "volunteers removed",
    "volunteers retired",
    "custody locations removed",
    "custody locations retired",
)

# What the section of counts is headed, so a reader -- and the tests -- find it
# by the same word the command prints.
HEADING = "Added by this run"
UNDO_HEADING = "Taken back out by this run"


class Command(BaseCommand):
    """The command. NOT a `ReportingCommand`, and that base says why not.

    It prints one section and then says more -- which pair to search for on the
    day, and how to take the roster back out -- exactly as `seed_demo_data`
    does, and for the reason named there: a hook on that base for "and then say
    this too" would be machinery built for a handful of callers. So this uses
    `_telemetry.running` directly, which is what it is for.
    """

    help = "Seed the invented roster the access-posture demo is performed against. Development servers only."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--undo",
            action="store_true",
            help="Remove the seeded roster again. Anything the ledger refers to is retired rather than deleted.",
        )
        parser.add_argument(
            "--with-stock",
            action="store_true",
            help="Also move hardware into two volunteers' custody. Cannot be undone: the ledger refuses DELETE.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        # The guard `_seeding` argues for both of the other seeding commands,
        # asked of this one for the same reason.
        _seeding.refuse_unless_a_development_server(
            "This command writes eighty invented volunteers into whatever database DATABASE_URL names.",
        )
        added: Counter[str] = Counter()
        with _telemetry.running(_telemetry.named(self)) as counted:
            # One transaction, so a run that fails half way through does not
            # leave half a roster and no sign of which half.
            with transaction.atomic():
                section, posted = self.seed(added, undo=options["undo"], with_stock=options["with_stock"])
            counted.update(_telemetry.figures(section))

        for line in _report.render(*section):
            self.stdout.write(line)
        if options["undo"]:
            return
        self.stdout.write("")
        if options["with_stock"] and not posted:
            # Said rather than left as a nought in the column above. A run that
            # declined and a run that had already posted both report no
            # transactions, and only one of them means the reserve act will
            # have nothing to join against.
            self.stdout.write(
                "The ledger already holds transactions this project's seeds did not write, so no "
                "stock was moved into anybody's custody. The roster above is there either way.\n"
            )
        for line in _report.render(
            "Search for these on the day",
            [(COLLIDING_NAME, len(COLLIDING)), (SEPARATED_NAME, len(SEPARATED))],
        ):
            self.stdout.write(line)
        self.stdout.write(
            f"\n{COLLIDING_NAME} is the pair that renders as one string under "
            f"ANONYMOUS_PAYLOAD=masked. {SEPARATED_NAME} is the pair that does not, so "
            f"typing one of their addresses in full returns exactly one row.\n"
            f"\nEverybody here is invented and every address is under a reserved name that "
            f"resolves nowhere. Run this again with --undo to remove them."
        )

    @staticmethod
    def seed(added: Counter[str], *, undo: bool, with_stock: bool) -> tuple[Report, bool]:
        """Write the scene, or take it back out.

        Hands back what to print and whether the ledger half of it happened,
        which is `seed_demo_data`'s shape and is that command's argument
        arriving here: the one thing a column of counts cannot say is that a
        run declined to write rather than finding its work already done.
        """
        if undo:
            undone(added)
            return (UNDO_HEADING, [(row, added[row]) for row in REMOVED]), True
        places = custody(roster(added), added)
        posted = stock(places, added) if with_stock else True
        return (HEADING, [(row, added[row]) for row in ADDED]), posted
