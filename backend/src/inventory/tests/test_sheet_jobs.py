"""Tests for rule 4, the job reference a note names.

The three properties worth pinning are the three the export made necessary:
case is folded in both directions, a space between the letters and the digits
is allowed, and a note naming two jobs still yields one string for a field
that holds one.
"""

import pytest

from inventory.sheet.jobs import job_reference, section
from inventory.tests.sheets import notes


@pytest.mark.parametrize(
    "note",
    [
        "install at NN217",
        # Lower case is 21 of the 137 references in the export and title case
        # is 40 more; a case-sensitive read loses 61 of the 136 submissions.
        "nn217",
        "Nn217",
        "NN 217 uplink",
        "Nn 217",
    ],
)
def test_a_job_is_recognised_however_it_is_spelled(note: str) -> None:
    assert job_reference(note) == "NN217"


@pytest.mark.parametrize(
    "note",
    [
        "",
        "fixing inventory",
        # A pattern anchored on the letters alone would read the NN out of a
        # word, and 'ANNEX' is the shape that does it.
        "ANNEX 217",
        # Letters with no number are not a reference to anything.
        "for NN",
        # No submission writes a hash, and the module's docstring says why
        # the pattern therefore does not admit one.
        "NN#217",
    ],
)
def test_a_note_naming_no_job_yields_nothing(note: str) -> None:
    assert job_reference(note) is None


def test_a_note_naming_two_jobs_yields_the_first() -> None:
    """`nn498-nn6622` is in the export, and is a link between two nodes rather
    than two jobs. The field holds one string, so the rule has to choose.
    """
    assert job_reference("nn498-nn6622") == "NN498"


def test_the_report_says_what_choosing_the_first_costs() -> None:
    """A partition that quietly loses a row is the failure this whole line of
    work exists to stop, so the job nothing will carry is counted.
    """
    _, counted = section(notes("nn498-nn6622", "install at NN217", "fixing inventory"))

    partition = dict(counted)
    assert partition["submissions citing a job"] == 2
    assert partition["distinct jobs cited"] == 3
    assert partition["submissions citing more than one"] == 1
    assert partition["cited jobs the imported field will not carry"] == 1


def test_the_report_prices_the_latitude_the_pattern_takes() -> None:
    """The census is the whole evidence for folding case and for allowing a
    space, so the report carries it rather than the brief asserting it. Every
    spelling the rule admits gets a line, including the ones nobody wrote, per
    the enumeration rule on `Report`.
    """
    _, counted = section(notes("NN217", "Nn217", "nn 217", "no job here"))

    partition = dict(counted)
    assert partition["references written"] == 3
    assert partition["  NN"] == 1
    assert partition["  Nn"] == 1
    assert partition["  nn\u2423"] == 1
    assert partition["  nN"] == 0
    assert partition["  nN\u2423"] == 0
    assert partition["submissions a case-sensitive read would find"] == 1
    assert partition["submissions a read allowing no space would find"] == 2
