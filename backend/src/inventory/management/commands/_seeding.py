"""The rules both seeding commands obey, written where neither can drift from the other.

`seed_demo_data` puts something to look at in a development database and
`seed_integration_data` pins the scene the browser suite drives. Two scenes,
and underneath them the same three rules: neither may run anywhere but a
development server, both meet names whose row somebody has since retired, and
both need an actor the ledger will still accept. Written out twice they had
already drifted -- the two scenes name the same radio differently, which is
why the capture run has to match a heading exactly -- so they are written here
once.

Underscored, and beside the commands that use it rather than away from them,
for the reason `_workbook.py` gives about its own name.

What stays with each command is its own guard on top of the one below: the
integration seed also demands the flag that acknowledges a published
credential, and `seed_demo_data`'s docstring argues why it asks for nothing
beyond DEBUG.
"""

from django.conf import settings
from django.core.management.base import CommandError

from inventory.models import Item, Location, Volunteer

REFUSAL = "Refusing to seed: DEBUG is off, so this is not a development server."


def refuse_unless_a_development_server(and_also: str = "", acknowledged: bool = False) -> None:
    """Stop unless `settings.DEBUG` says this is somebody's own machine.

    Both commands ship inside the backend image, so either is one wrong
    `kubectl` context away from a pod holding real stock. `and_also` is
    appended by a caller with something further to say about what it would
    otherwise have written.

    `acknowledged` is a caller saying its own flag was typed on THIS
    invocation, which is the only thing allowed past the DEBUG rule. Why that
    is a flag rather than another variable is argued once, on
    `seed_integration_data`'s own acknowledgement, and it is the reason a
    deployed demo gets one too.
    """
    if settings.DEBUG or acknowledged:
        return
    raise CommandError(f"{REFUSAL} {and_also}".strip())


def revived(row: Item | Location) -> None:
    """Bring a retired row of this name back, rather than stepping over it.

    An item name is unique outright and a location name is unique within its
    parent, so a retired row wearing one of these names *is* the row and no
    second one can be made beside it. Left as it was found, it is a scene the
    read API declines to offer, and the first thing to read a pick-list fails
    for a reason that is not a bug.
    """
    if not row.active:
        row.active = True
        row.save(update_fields=["active"])


def selectable_volunteer(display_name: str) -> tuple[Volunteer, bool]:
    """The volunteer of this name that the ledger will accept, and whether it is new.

    Not `get_or_create`, and the opposite of `revived` above: a display name is
    deliberately not unique, so a second row carrying one would make that call
    raise, and the row already wearing it may be somebody else entirely. It has
    to come back selectable, because a transaction refuses an actor that the
    pick-list has dropped -- which is what a developer's own database does to
    one of these the day it is merged or retired.
    """
    found = Volunteer.objects.selectable().filter(display_name=display_name).first()
    if found is not None:
        return found, False
    return Volunteer.objects.create(display_name=display_name), True
