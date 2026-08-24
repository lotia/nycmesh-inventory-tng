"""The invented roster the access-posture demo is performed against.

PROVISIONAL, with the command it tests: `inventory-tng-81f7.4` deletes both.

WHAT IS ACTUALLY BEING ASSERTED HERE, because "the seed makes rows" is not
worth a file. Every act of the run of show depends on a property of this data,
and each of those properties is easy to lose to an innocent edit -- a different
address on one row separates the pair act two exists for, and a different
proportion makes act three's honest drawback a lie. So the properties are held
here rather than checked by whoever is about to stand up in front of people.

And one of them is not about the demo at all. Everybody in this roster is
invented, and a change that let a real person into it would cause the exact
harm the consultation was convened to prevent -- in a room that may contain
them, on a projector, possibly recorded. That is asserted too.
"""

import io
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from inventory.management.commands import seed_posture_demo
from inventory.models import Location, StockBalance, StockMovement, StockTransaction, Volunteer
from inventory_tng import postures

pytestmark = pytest.mark.django_db


def seed(**options: Any) -> str:
    printed = io.StringIO()
    with override_settings(DEBUG=True):
        call_command("seed_posture_demo", stdout=printed, **options)
    return printed.getvalue()


# --------------------------------------------------------------------------
# The roster the acts are performed against
# --------------------------------------------------------------------------


def test_it_seeds_a_roster_a_terminal_has_to_scroll_through() -> None:
    """Act one's whole effect is a page of people going past. Two dozen reads
    as test data, which is the opposite of the point."""
    seed()

    assert Volunteer.objects.count() == seed_posture_demo.ROSTER
    # The constant itself, not a second query: what is being asserted here is
    # that the number the runbook promises has not been trimmed.
    assert seed_posture_demo.ROSTER >= 80


def test_about_45_percent_of_them_never_gave_an_address() -> None:
    """Act three names this as its own drawback, so it has to be true on the
    screen rather than asserted from a slide. It is the proportion
    `Volunteer.NULL_WHEN_BLANK` records of the historical rows."""
    seed()

    without = Volunteer.objects.filter(email__isnull=True).count()

    assert round(without / seed_posture_demo.ROSTER, 2) == pytest.approx(0.45, abs=0.02)


def test_two_pairs_share_a_display_name() -> None:
    seed()

    for shared in (seed_posture_demo.COLLIDING_NAME, seed_posture_demo.SEPARATED_NAME):
        assert Volunteer.objects.filter(display_name=shared).count() == 2


def test_exactly_one_of_those_pairs_collides_under_the_mask() -> None:
    """ACT TWO IS THIS ASSERTION, performed. Two people, one string.

    Held here rather than trusted to whoever chose the addresses: the collision
    is a property of two characters in each of them, and any edit that looks
    like tidying can separate the pair the demo exists to show together.
    """
    seed()

    collides = {
        postures.masked(person.email)
        for person in Volunteer.objects.filter(display_name=seed_posture_demo.COLLIDING_NAME)
    }
    separates = {
        postures.masked(person.email)
        for person in Volunteer.objects.filter(display_name=seed_posture_demo.SEPARATED_NAME)
    }

    assert len(collides) == 1, "the pair act two exists for no longer renders as one string"
    assert len(separates) == 2, "the pair act three types an address for no longer has two"


def test_the_separated_pair_is_told_apart_by_typing_one_address_in_full() -> None:
    """Act three's hard case, asked of the data rather than of the endpoint."""
    seed()
    address = seed_posture_demo.SEPARATED[0][1]

    assert Volunteer.objects.filter(email=address).count() == 1


def test_the_masks_do_not_all_collapse_into_one_string() -> None:
    """Guards the collision above against passing for the wrong reason.

    Every address under one domain would make act two's two identical rows an
    artefact of the seed rather than a property of masking, and every assertion
    here would still pass.
    """
    seed()

    masked = {postures.masked(person.email) for person in Volunteer.objects.exclude(email__isnull=True)}

    assert len(masked) > 10


# --------------------------------------------------------------------------
# Invented, which is the one instruction that cannot be waived
# --------------------------------------------------------------------------


def test_every_address_is_under_a_name_that_resolves_nowhere() -> None:
    """RFC 2606 and RFC 6761 reserve these, so none of them can ever belong to
    anybody. A roster on a projector must not be able to reach a real inbox."""
    seed()
    reserved = (".example", ".test", ".invalid", ".localhost")

    for person in Volunteer.objects.exclude(email__isnull=True):
        assert person.email.endswith(reserved), f"{person.email} is not a reserved name"


def test_the_roster_is_the_same_every_run() -> None:
    """A presenter reads "search for Sean" off a runbook written days earlier,
    so a roster that differed each morning would be unrehearsable."""
    assert seed_posture_demo.people() == seed_posture_demo.people()


# --------------------------------------------------------------------------
# Running it twice, and taking it back out
# --------------------------------------------------------------------------


def test_running_it_twice_adds_nobody() -> None:
    seed()
    seed()

    assert Volunteer.objects.count() == seed_posture_demo.ROSTER


def test_undo_removes_the_roster_again() -> None:
    seed()

    seed(undo=True)

    assert Volunteer.objects.count() == 0
    assert Location.objects.filter(kind=Location.Kind.VOLUNTEER_CUSTODY).count() == 0


def test_undo_on_a_database_it_never_seeded_removes_nothing() -> None:
    Volunteer.objects.create(display_name="Somebody Else")

    seed(undo=True)

    assert Volunteer.objects.count() == 1


# --------------------------------------------------------------------------
# The custody locations, and the stock that is deliberately opt-in
# --------------------------------------------------------------------------


def test_two_volunteers_hold_stock_where_a_location_has_to_say_who() -> None:
    """The reserve act joins three reasonable reads into one answer nobody
    would publish, and this is the row that makes the join reach a person."""
    seed()

    custody = Location.objects.filter(kind=Location.Kind.VOLUNTEER_CUSTODY)

    assert custody.count() == len(seed_posture_demo.CUSTODY_HOLDERS)
    for place in custody:
        assert place.held_by is not None
        assert place.held_by.display_name in place.name


def test_nothing_reaches_the_ledger_unless_it_is_asked_for() -> None:
    """The ledger refuses DELETE, so a movement written here is in that
    database for good. It is a flag for exactly that reason."""
    seed()

    assert StockTransaction.objects.count() == 0


def test_with_stock_puts_hardware_in_somebody_s_home() -> None:
    seed(with_stock=True)

    into_custody = StockMovement.objects.filter(to_location__kind=Location.Kind.VOLUNTEER_CUSTODY)

    assert into_custody.count() == len(seed_posture_demo.CUSTODY_HOLDERS) * len(seed_posture_demo.HELD_AT_HOME)


def test_with_stock_never_drives_a_shelf_below_zero() -> None:
    """The one thing this command does that CANNOT be undone.

    `ledger_is_ours()` is true of an empty ledger, so on a database where
    `seed_demo_data` had never run this checked stock out of a shelf nothing
    had ever stocked -- and the two ledger tables refuse DELETE, so the
    negative balances were permanent. It posts that delivery first now.
    """
    seed(with_stock=True)

    negative = StockBalance.objects.filter(quantity__lt=0)

    assert not list(negative), [f"{row.item} at {row.location} is {row.quantity}" for row in negative]


def test_undo_brings_back_a_roster_it_had_to_retire() -> None:
    """`--undo` retires whoever the ledger refers to, so a re-seed meets them.

    `roster` is where the reviving is argued. What it buys is this: the whole
    roster back, and the two custody locations with it.
    """
    seed(with_stock=True)
    seed(undo=True)

    seed()

    assert Volunteer.objects.filter(active=True).count() == seed_posture_demo.ROSTER
    assert Location.objects.filter(kind=Location.Kind.VOLUNTEER_CUSTODY, active=True).count() == 2


def test_a_volunteer_already_on_file_under_another_spelling_does_not_stop_the_run() -> None:
    """A display name is deliberately not unique and an address is, so the
    holders are looked up by address; by name this raised StopIteration inside
    the transaction with nothing saying what had clashed."""
    Volunteer.objects.create(display_name="Sean W.", email=seed_posture_demo.CUSTODY_HOLDERS[0])

    seed()

    held = Location.objects.get(held_by__email=seed_posture_demo.CUSTODY_HOLDERS[0])
    assert held.held_by is not None
    assert held.held_by.display_name == "Sean W."


def test_with_stock_run_twice_posts_one_set_of_movements() -> None:
    seed(with_stock=True)
    seed(with_stock=True)

    assert StockMovement.objects.filter(to_location__kind=Location.Kind.VOLUNTEER_CUSTODY).count() == 4


def test_undo_retires_whoever_the_ledger_refers_to_rather_than_failing() -> None:
    """`actor` is PROTECT and the ledger cannot be rewritten, so a clean
    removal is not available once stock has moved. Retiring is what an
    administrator does with a real duplicate anyway."""
    seed(with_stock=True)

    seed(undo=True)

    holders = Volunteer.objects.filter(email__in=seed_posture_demo.CUSTODY_HOLDERS)
    assert holders.exists()
    assert not holders.filter(active=True).exists()
    assert not Location.objects.filter(kind=Location.Kind.VOLUNTEER_CUSTODY, active=True).exists()


def test_it_says_when_it_declined_to_write_a_ledger_it_does_not_own(volunteer: Volunteer) -> None:
    """A nought in the column reads the same as a run that had already posted,
    and only one of them leaves the reserve act with nothing to join against."""
    StockTransaction.objects.create(actor=volunteer, kind=StockTransaction.Kind.RECEIPT)

    printed = seed(with_stock=True)

    assert "no stock was moved into anybody's custody" in printed
    assert StockMovement.objects.count() == 0


# --------------------------------------------------------------------------
# Where it will not run
# --------------------------------------------------------------------------


@override_settings(DEBUG=False)
def test_it_refuses_anywhere_that_is_not_a_development_server() -> None:
    """The guard `_seeding` argues for both seeding commands, asked of this one."""
    with pytest.raises(CommandError, match="DEBUG is off"):
        call_command("seed_posture_demo")


def test_it_says_which_pair_to_search_for() -> None:
    """The presenter reads this off the terminal rather than out of the code."""
    printed = seed()

    assert seed_posture_demo.COLLIDING_NAME in printed
    assert seed_posture_demo.SEPARATED_NAME in printed
    assert "--undo" in printed
