"""Print the label codes in this database, and what each one resolves to.

`seed_demo_data` prints the codes it made, once, at the end of a run that may
have been a hundred lines of container output ago. `scripts/bootstrap-dev.sh`
knows this and holds that one line back so it can repeat it under its closing
message -- which is a good answer for the path that goes through bootstrap and
no answer at all for anybody else.

Since `compose up` seeds on its own (inventory-tng-zjqt), "anybody else" is now
the ordinary case: the codes go by in `compose logs seed` and nothing repeats
them. Without this, the first thing a developer wants to try -- typing a code
into the box marked "Scan or type a code" -- needs a trip through the Django
admin or a shell to find out what to type.

So this reads the database rather than the seed's own constants. What is on the
screen is what a scanner would resolve, including labels somebody minted by
hand, and a revoked sticker is shown as revoked rather than quietly omitted --
because "I typed the code and it did nothing" is exactly the confusion a
revoked label produces, and the answer is on this screen or it is nowhere.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from inventory.management.commands import _telemetry
from inventory.models import Label


def points_at(label: Label) -> str:
    """What a scanner would resolve this code to, named with its kind.

    A label carries an item OR a location, and both are nullable -- which the
    first version of this command did not survive: it read `label.item.name`
    and raised `AttributeError` on the first shelf label in a real database.
    Found by running it, not by reading it.

    The kind is written out because the two read alike otherwise. "Bench" is a
    plausible name for a shelf and for a thing on one, and a developer typing a
    code wants to know which screen to expect.
    """
    if label.item is not None:
        return f"item: {label.item.name}"
    if label.location is not None:
        return f"place: {label.location.name}"
    # UNREACHABLE, and kept because the type checker cannot see why: the
    # database enforces `label_targets_exactly_one`, so a row with neither is
    # rejected on insert. It is written as a report of a broken invariant
    # rather than a third kind of label, because if it is ever printed the
    # interesting thing is that a constraint stopped holding.
    return "points at nothing, which the database is supposed to forbid"


class Command(BaseCommand):
    help = "Print every label code in this database and the item it resolves to."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--all",
            action="store_true",
            dest="including_revoked",
            help="Include revoked labels, which a scanner will refuse.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        # `running` rather than `ReportingCommand`, for the reason that base
        # gives about `mint_debug_token`: what this prints is prose and a list,
        # not a section of labels and numbers, so there is nothing for the
        # base's rendering to render. It still says it ran and what it found --
        # a command that reads is still a command somebody ran, and a reader
        # asking "did anybody ever look this up" deserves an answer.
        with _telemetry.running(_telemetry.named(self)) as counted:
            labels = Label.objects.select_related("item", "location").order_by("code")
            if not options["including_revoked"]:
                labels = labels.filter(revoked_at__isnull=True)
            found = list(labels)
            counted["labels"] = len(found)
        if not found:
            # Named as the likely cause rather than reported as an empty set: a
            # database with no labels in it is almost always one nothing has
            # seeded, and the useful thing to say is which command fixes that.
            self.stdout.write(
                "No labels in this database. `seed_demo_data` makes two, and "
                "`docker compose up` runs it -- see DEVELOPERS.md.",
            )
            return

        self.stdout.write("Scan or type any of these:")
        self.stdout.write("")
        for label in found:
            revoked = "  (revoked -- a scanner will refuse it)" if label.revoked_at else ""
            self.stdout.write(f"  {label.code}  {points_at(label)}{revoked}")
