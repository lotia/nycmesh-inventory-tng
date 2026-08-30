"""The one line a developer needs after `compose up`, available at any time.

`seed_demo_data` prints the codes it made once, and since the stack seeds
itself that line goes past in `compose logs seed` with nothing to repeat it.
Finding a code to type is the first thing anybody tries, and the command's own
docstring says where that leaves somebody who cannot -- so a code they cannot
find is where the demo stops.

What is asserted is that it reads the DATABASE rather than the seed's own
constants -- a version reciting a hardcoded pair would pass a laxer test and
would be wrong for every label minted since.
"""

from io import StringIO

import pytest
from django.core.management import call_command
from django.db.utils import IntegrityError

from inventory.models import Item, Label, Location

pytestmark = pytest.mark.django_db


def shown(**options: bool) -> str:
    out = StringIO()
    call_command("show_label_codes", stdout=out, **options)
    return out.getvalue()


def test_it_reads_the_database_rather_than_a_constant(item: Item) -> None:
    """A label nothing seeded is still a label a scanner resolves."""
    Label.objects.create(code="ZZ9999ZZ99", item=item)

    listed = shown()

    assert "ZZ9999ZZ99" in listed, (
        "a label minted by hand does not appear, so this recites what the seed makes rather than what "
        "the database holds -- and every label made since the seed ran is invisible"
    )
    assert item.name in listed, "the code is shown without saying what it resolves to, which is half the answer"


def test_a_label_on_a_place_says_so(warehouse: Location) -> None:
    """The case `points_at` exists for, and the one that caught the first version.

    It read `label.item.name` and raised
    `AttributeError` on the first shelf label it met. It was found by running
    the command against the compose stack rather than by reading it, which is
    the whole argument for having run it.
    """
    # The stored code, not the one typed here: the model folds the letters a
    # person misreads off a faded sticker onto digits, so `LOC...` is saved as
    # `10C...`. Asserting the typed string tests the alphabet by accident and
    # fails for a reason that has nothing to do with this command.
    label = Label.objects.create(code="LOC0000001", location=warehouse, quantity=None)

    listed = shown()

    assert label.code in listed
    assert warehouse.name in listed
    assert "place" in listed, "a place label is listed without saying it is a place, so it reads as an item"


def test_a_label_must_point_at_something() -> None:
    """Which is why the command has no third kind to display.

    Asserted rather than assumed: the "points at nothing" branch exists only
    because the type checker cannot see `label_targets_exactly_one`, and if
    this constraint were ever relaxed that branch would silently become a real
    case with no test behind it.
    """
    with pytest.raises(IntegrityError):
        Label.objects.create(code="ORP0000001")


def test_a_revoked_label_is_hidden_by_default(item: Item) -> None:
    """Because a scanner refuses it, and offering it invites the confusion."""
    Label.objects.create(code="AA1111AA11", item=item, revoked_at="2026-08-01T00:00:00Z")

    assert "AA1111AA11" not in shown()


def test_but_can_be_asked_for_and_is_marked_when_it_is(item: Item) -> None:
    """ "I typed the code and nothing happened" is what a revoked label feels like.

    So it is reachable and labelled rather than absent: somebody debugging that
    needs to be told the sticker is dead, and nothing else in the application
    says so at a shell.
    """
    Label.objects.create(code="AA1111AA11", item=item, revoked_at="2026-08-01T00:00:00Z")

    listed = shown(including_revoked=True)

    assert "AA1111AA11" in listed
    assert "revoked" in listed, "a revoked label is listed beside live ones with nothing saying it is dead"


def test_an_empty_database_names_what_fills_it() -> None:
    """Rather than printing nothing, which reads as a broken command."""
    listed = shown()

    assert "seed_demo_data" in listed
    assert "No labels" in listed
