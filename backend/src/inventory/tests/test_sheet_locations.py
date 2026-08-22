"""Tests for rule 3, the places a note names.

The rule is a seed rather than a gazetteer, so the properties worth pinning
are the ones the seed has to get right: case never distinguishes two places,
the mesh room survives every way it was misspelled, and a note naming two
places yields two.
"""

import pytest

from inventory.sheet.locations import locations, section
from inventory.tests.sheets import notes


@pytest.mark.parametrize(
    "note",
    [
        "Sean mesh room 131 broome st",
        # Folded, which is the whole of the case decision: this and the line
        # above are one person writing one room twice.
        "sean mesh room 131 broome st",
        "mesh room",
        "sean mesh room 131broome",
    ],
)
def test_the_room_is_found_however_it_was_written(note: str) -> None:
    assert "131 Broome" in locations(note)


@pytest.mark.parametrize(
    "note",
    [
        # Every spelling of the street the ledger holds, and none of these
        # says `mesh room` -- so the alternation the misspellings exist for is
        # the one being exercised, rather than the first branch passing for
        # all of them.
        "dropped at 131 broome st",
        "dropped at 131 broom st",
        "dropped at 131 beoome st",
        "dropped at 131 briome st",
        "dropped at 131 brooke st",
    ],
)
def test_the_street_is_found_however_it_was_misspelled(note: str) -> None:
    assert locations(note) == ("131 Broome",)


def test_the_street_pattern_does_not_read_broken_as_broome() -> None:
    """Widening the vowels to catch `beoome` also spans `broke`, so the number
    is what keeps `sheet broken?` out of a place.
    """
    assert locations("un-correcting inventory, sheet broken?") == ()


def test_a_note_naming_two_places_yields_two() -> None:
    """`Blue Stockings + mesh room` is the note that makes the widest
    mesh-room predicate safe to take: it names both, rather than meaning the
    other one.
    """
    assert locations("blue stockings + mesh room") == ("131 Broome", "Blue Stockings")


@pytest.mark.parametrize("note", ["", "installs", "order arrived", "fixing inventory"])
def test_a_note_naming_no_place_yields_nothing(note: str) -> None:
    assert locations(note) == ()


def test_a_volunteer_holding_stock_is_a_place() -> None:
    """Decision 0008 makes custody a location, and these are how the ledger
    says so. Which volunteer is rule 6's question, not this one's.
    """
    assert locations("apartment stock") == ("a volunteer's home",)
    assert locations("replenish home stock from mil mundos") == ("Mil Mundos", "a volunteer's home")


def test_the_report_offers_the_seed_and_admits_what_it_misses() -> None:
    """A vocabulary that matched everything would be a gazetteer somebody
    invented. What it leaves is the measure of how far o5t still has to go.
    """
    _, counted = section(notes("mesh room", "blue stockings + mesh room", "installs", ""))

    partition = dict(counted)
    assert partition["submissions with a note"] == 3
    assert partition["  naming a candidate location"] == 2
    assert partition["   of those, naming more than one"] == 1
    assert partition["  naming none of the vocabulary"] == 1
    # The two-space lines are the partition, and the three-space one is a
    # subset of the first. At one indent they summed to more than the parent.
    assert partition["  naming a candidate location"] + partition["  naming none of the vocabulary"] == 3
    assert partition["distinct candidates named"] == 2
    assert partition["  131 Broome"] == 2
    assert partition["  Blue Stockings"] == 1


def test_the_report_keeps_all_three_readings_of_the_room() -> None:
    """They are 38 submissions apart over the real export, which is a fifth of
    the smaller, so a figure quoted without its predicate is not one.
    """
    _, counted = section(notes("mesh room", "mesh room 131", "mesh room broome"))

    partition = dict(counted)
    assert partition["the mesh room, however written"] == 3
    assert partition["  and 131 or a Broome spelling"] == 2
    assert partition["  and Broome spelled correctly"] == 1


def test_the_report_counts_the_notes_naming_it_both_ways() -> None:
    """Folding is what collapses the 41 notes naming the room to 33, and the
    report shows the collapse rather than asserting it. Notes rather than
    spellings: two notes naming the room identically and differing after it
    are two, which is what the brief has always counted.
    """
    _, counted = section(notes("Mesh Room", "mesh room", "mesh room 131"))

    partition = dict(counted)
    assert partition["notes naming it, read literally"] == 3
    assert partition["notes naming it, read case-insensitively"] == 2
