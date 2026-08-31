"""The invented pick-list keeps the shape the consultation is argued from.

`_roster` carries the reasoning. What is held here is that the three properties
the demonstration depends on survive a change to the name pool, because every
one of them could be lost by an edit that looked harmless.

`inventory-tng-w2f6`, which existed because the run of show told a presenter to
seed "around 80 names, with two pairs deliberately sharing a display name" and
nothing in this repository could do it.
"""

from collections import Counter

import pytest
from django.core.management import call_command
from pytest_django.fixtures import Settings

from inventory.management.commands import _roster
from inventory.models import Volunteer

#: What the measured figures assume, and the reason a number appears here at
#: all: "a single letter returns 54 of 86" counts names.
MEASURED_NAMES = 86

#: The proportion carrying no address, measured against the real records. Held
#: to a range rather than a figure -- the exact count moves with the pool size
#: and the argument does not, but a roster where nearly everybody has an
#: address would quietly make act three's honest caveat untrue.
WITHOUT_ADDRESS = (0.40, 0.55)


def test_it_is_the_size_the_figures_assume() -> None:
    names = {person.display_name for person in _roster.roster()}

    assert len(names) == MEASURED_NAMES, (
        f"the roster carries {len(names)} distinct names and every figure in the consultation is "
        f"measured against {MEASURED_NAMES}; change the figures or change this"
    )


def test_two_names_are_worn_by_two_people_each() -> None:
    """The case the pick-list cannot resolve, and the reason a second line exists."""
    worn = Counter(person.display_name for person in _roster.roster())
    shared = sorted(name for name, count in worn.items() if count > 1)

    assert len(shared) == 2, f"expected two shared display names and found {shared}"


def test_one_shared_pair_would_blur_to_the_same_string() -> None:
    """The two-Seans example, which is what makes the masking argument visible.

    Same first initial and same provider, so any blur of the two addresses
    produces one string and separates nobody. Without this the demonstration
    can describe the measurement and cannot show it.
    """
    worn: dict[str, list[_roster.Person]] = {}
    for person in _roster.roster():
        worn.setdefault(person.display_name, []).append(person)
    pairs = [people for people in worn.values() if len(people) > 1]

    def blur(address: str) -> tuple[str, str]:
        """What a masked address keeps: the first letter, and the provider."""
        local, _, provider = address.partition("@")
        return local[0], provider

    blurred = [
        {blur(person.email) for person in people if person.email}
        for people in pairs
        if all(person.email for person in people)
    ]

    assert any(len(shapes) == 1 for shapes in blurred), (
        "no pair of same-named people shares a first initial and a mail provider, so blurring their "
        "addresses would tell them apart and the measured argument cannot be shown"
    )


def test_about_the_measured_proportion_carry_no_address() -> None:
    people = _roster.roster()
    without = sum(1 for person in people if not person.email)
    share = without / len(people)

    assert WITHOUT_ADDRESS[0] <= share <= WITHOUT_ADDRESS[1], (
        f"{share:.0%} of the roster carries no address, and the run of show says roughly 45% -- those "
        "people are the ones typing an address cannot help, which act three has to admit"
    )


def test_nobody_in_it_could_be_written_to() -> None:
    """The safety argument, held rather than asserted in a docstring.

    The run puts this list on a shared screen. `.invalid` is reserved and never
    resolves, so an address here cannot reach anybody even if the roster
    escapes the room.
    """
    reachable = [p.email for p in _roster.roster() if p.email and not p.email.endswith(".invalid")]

    assert not reachable, f"{reachable} are not .invalid addresses, so they could belong to somebody"


def test_it_is_the_same_roster_every_time() -> None:
    """A presenter is told to search for a name they seeded, which needs it to be fixed."""
    assert _roster.roster() == _roster.roster()


def test_a_smaller_roster_still_carries_the_cases() -> None:
    """So a test may ask for a dozen without losing what the demonstration needs."""
    worn = Counter(person.display_name for person in _roster.roster(names=8))

    assert [name for name, count in worn.items() if count > 1], (
        "a short roster lost its shared names, so anything asking for one gets a pick-list with no collision in it"
    )


# ---------------------------------------------------------------------------
# And that the command actually writes it
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded(db: None, settings: Settings) -> None:
    settings.DEBUG = True
    call_command("seed_demo_data", "--with-demo-roster")


def test_the_flag_writes_the_roster(seeded: None) -> None:
    assert Volunteer.objects.count() > MEASURED_NAMES, (
        "the roster flag did not write the pick-list, so a demonstration has two people in it"
    )


def test_the_collisions_survive_being_written(seeded: None) -> None:
    """The one that failed first, and the reason the writer does not use `selectable_volunteer`.

    That helper returns a selectable row of the same display name, which is
    right everywhere else and collapses exactly the pair this exists to create.
    Seeded through it the roster came out with no collisions at all.
    """
    worn = Counter(Volunteer.objects.values_list("display_name", flat=True))
    shared = sorted(name for name, count in worn.items() if count > 1)

    assert len(shared) == 2, (
        f"the database holds {shared} as shared display names; the two the roster defines collapsed "
        "into one row each, so the pick-list has nothing ambiguous in it"
    )


def test_running_it_twice_writes_one_roster(seeded: None, settings: Settings) -> None:
    """Idempotent on the COUNT of each name, since the name itself cannot be the key."""
    before = Volunteer.objects.count()

    call_command("seed_demo_data", "--with-demo-roster")

    assert Volunteer.objects.count() == before, "a second run added people, so the roster doubles"


def test_without_the_flag_the_pick_list_stays_small(db: None, settings: Settings) -> None:
    """The contributor path is unchanged, which is why the flag exists."""
    settings.DEBUG = True

    call_command("seed_demo_data")

    assert Volunteer.objects.count() < 10, (
        "seeding without the flag wrote the demonstration roster, so the path a new contributor "
        "takes now hands them eighty-eight invented people"
    )
