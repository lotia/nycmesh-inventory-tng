"""Tests for the return-language count.

Not a classifier, and the tests say so as much as the module does: what is
pinned here is that the predicate is the word and nothing inferred from
context, and that the count is reported in a way a reader cannot mistake for
a return rate.
"""

import pytest

from inventory.sheet.returns import says_returned, section
from inventory.sheet.workbook import CHECKING_IN
from inventory.tests.sheets import sheet_of, submission


@pytest.mark.parametrize(
    "note",
    ["returning spare", "returned items", "equipment returned", "return", "Returning From Parkside", "returns"],
)
def test_the_word_is_found_in_any_of_its_forms(note: str) -> None:
    assert says_returned(note)


@pytest.mark.parametrize(
    "note",
    [
        "",
        "fixing inventory",
        # Inferred rather than said. The predicate is the narrowest there is
        # on purpose: the claim is that these notes say it in so many words.
        "came back with two",
        "brought back from the install",
        # The word inside another is not the word.
        "returnable packaging",
    ],
)
def test_nothing_is_inferred_from_context(note: str) -> None:
    assert not says_returned(note)


def test_a_check_out_saying_return_is_counted_apart() -> None:
    """Six of them are in the export, and they are why the check-in line is a
    floor rather than a rate: the word does not reliably give the direction.
    """
    sheet = sheet_of(
        [
            submission(note="returning spare", direction=CHECKING_IN),
            submission(note="return to vendor"),
            submission(note="installs"),
        ],
    )

    _, counted = section(sheet)

    partition = dict(counted)
    assert partition["submissions whose note says return"] == 2
    assert partition["  recorded as a check-in"] == 1
    assert partition["  recorded as a check-out"] == 1
    assert partition["check-ins"] == 1


def test_the_check_in_total_is_reported_beside_the_count() -> None:
    """Without the denominator the count reads as a rate, which is the one
    reading this module exists to refuse.
    """
    _, counted = section(sheet_of([submission(direction=CHECKING_IN), submission()]))

    assert dict(counted)["check-ins"] == 1
