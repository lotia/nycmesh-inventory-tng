"""Tests for rule 2, whether a note is a correction.

The rule needs both halves, so the tests worth having are the ones where only
one half is present: a repair to hardware, and a note naming stock that does
nothing to it. Those are what the export contains and what a looser rule gets
wrong.
"""

import pytest

from inventory.sheet.corrections import is_correction, section
from inventory.tests.sheets import notes


@pytest.mark.parametrize(
    "note",
    [
        "fixing inventory",
        "updating inventory",
        "inventory correction",
        "inventory correct",
        # Whole-note equality loses this one, and it is the same act as the
        # first with a detail added.
        "fixing inventory (2 today)",
        "correcting inventory",
        "initial inventory",
        "inventory seeding",
        "inventory adjustment",
        "update inventory counts",
        "fix the count",
        # Three spellings of the word are in the ledger, and what the rule
        # reads is what was typed.
        "fixing invenotry",
        "fixing inventry",
        "fixing invneottr",
    ],
)
def test_a_note_adjusting_the_record_is_a_correction(note: str) -> None:
    assert is_correction(note)


@pytest.mark.parametrize(
    "note",
    [
        "",
        "installs",
        "order arrived",
        # An act with nothing to adjust: these are repairs to hardware at a
        # site, and a rule reading the verb alone imports them as adjustments.
        "fixing loose pole nn540",
        "hex house fix",
        "gsg fiber fix",
        # A record with no act: an order, and a place.
        "inventory order",
        "apartment stock",
        "moving inventory to basement",
        "demo of how to use inventory",
    ],
)
def test_a_note_with_only_half_the_rule_is_not_a_correction(note: str) -> None:
    assert not is_correction(note)


def test_the_report_partitions_every_submission_exactly_once() -> None:
    """The four lines are a partition, so they sum to the population it states
    above them. A rule whose buckets overlap is how a figure nobody could
    reproduce got published.
    """
    sheet = notes("fixing inventory", "inventory order", "hex house fix", "installs", "")

    _, counted = section(sheet)

    partition = dict(counted)
    assert partition["submissions"] == len(sheet.submissions)
    assert partition["  with no note at all"] == 1
    assert partition["  naming the record and an act of adjusting it"] == 1
    assert partition["  naming the record only"] == 1
    assert partition["  naming an act only"] == 1
    assert partition["  naming neither, note or no note"] == 2
    # Summed by name rather than by position, so that a line added above them
    # cannot leave this passing while it adds up something else.
    assert (
        partition["  naming the record and an act of adjusting it"]
        + partition["  naming the record only"]
        + partition["  naming an act only"]
        + partition["  naming neither, note or no note"]
        == partition["submissions"]
    )


def test_the_report_shows_all_three_readings_of_the_enumerated_phrases() -> None:
    """`inventory correction` contains `inventory correct`, which is what makes
    a per-phrase sum count a row twice. Per row it is one row, and the third
    reading is printed because it is the one being argued against.
    """
    _, counted = section(notes("inventory correction", "fixing inventory (2 today)"))

    partition = dict(counted)
    assert partition["the four enumerated phrases, whole-note"] == 1
    assert partition["  the same phrases, per row"] == 2
    assert partition["  the same phrases, summed per phrase"] == 3
