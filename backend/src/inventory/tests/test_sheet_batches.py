"""Tests for rule 5, where a burst of submissions starts and stops.

The properties worth pinning are the ones the two open questions turned on:
the gap is measured from the previous submission rather than the first, one
person's trip is not joined to another's, and a row the rule cannot attribute
or cannot place in time stands alone rather than joining every other such row.
"""

from datetime import datetime, timedelta

from inventory.sheet.batches import (
    WINDOW,
    batches,
    by_email,
    by_email_or_name,
    by_email_or_nobody,
    by_name,
    section,
)
from inventory.sheet.workbook import Submission
from inventory.tests.sheets import AT, sheet_of, submission

MINUTE = timedelta(minutes=1)


def at(minutes: float, **fields: object) -> Submission:
    """A submission that many minutes after the fixture's fixed clock.

    The row number follows the minute, so a builder never has to state both
    and two submissions cannot silently share a row.
    """
    # The suppression is on `**fields`: `sheets.submission` is keyword-only
    # with a field per column, and ty checks an unpacked dict against the
    # narrowest of those types rather than against each parameter it lands on.
    # DEVELOPERS.md#typing asks a suppression to say which stub is narrow than
    # the thing it describes, and this is ty itself rather than a stub.
    return submission(row=2 + int(minutes * 60), at=AT + minutes * MINUTE, **fields)  # ty: ignore[invalid-argument-type]


def sizes(found: list[list[Submission]]) -> list[int]:
    return [len(batch) for batch in found]


def test_submissions_a_few_seconds_apart_are_one_batch() -> None:
    """The shape the old form produced: one trip to the shelf, one row per
    item taken off it.
    """
    assert sizes(batches([at(0), at(0.5), at(1), at(1.5)])) == [4]


def test_a_pause_longer_than_the_window_starts_a_new_batch() -> None:
    assert sizes(batches([at(0), at(1), at(30), at(31)])) == [2, 2]


def test_a_gap_of_exactly_the_window_still_continues_the_batch() -> None:
    """Ten minutes is the window rather than the first gap outside it, so the
    comparison is inclusive; a rule that flipped it would move every figure in
    the brief by an amount nobody could see in the prose.
    """
    assert sizes(batches([at(0), at(WINDOW.total_seconds() / 60)])) == [2]


def test_the_gap_is_measured_from_the_previous_submission_not_the_first() -> None:
    """The settled half of the window question. Six submissions two minutes
    apart are one trip; the anchored reading cuts it where the clock ran out
    rather than where the person stopped.
    """
    steady = [at(minutes) for minutes in (0, 2, 4, 6, 8, 10, 12)]

    assert sizes(batches(steady)) == [7]
    assert sizes(batches(steady, chained=False)) == [6, 1]


def test_two_people_at_the_shelf_together_are_two_batches() -> None:
    """Interleaved in time and separate in fact. A rule grouping on time alone
    would import both trips as one transaction and attribute it to one of them.
    """
    found = batches(
        [
            at(0, name="Ada", email="ada@example.net"),
            at(1, name="Grace", email="grace@example.net"),
            at(2, name="Ada", email="ada@example.net"),
            at(3, name="Grace", email="grace@example.net"),
        ],
    )

    assert sizes(found) == [2, 2]
    assert {s.name for s in found[0]} == {"Ada"}
    assert {s.name for s in found[1]} == {"Grace"}


def test_the_same_person_spelled_two_ways_is_one_submitter() -> None:
    """Case is folded, as it is in rules 1 and 3: `Lydon` and `lydon` are one
    person, and the export writes both.
    """
    assert sizes(batches([at(0, name="Lydon"), at(1, name="lydon")])) == [2]


def test_a_submission_naming_nobody_stands_alone() -> None:
    """It cannot honestly be chained to anything. Merging strangers' rows into
    one transaction invents a trip nobody made, which is the mistake the email
    key makes at scale.
    """
    assert sizes(batches([at(0, name=""), at(1, name=""), at(2, name="")])) == [1, 1, 1]


def test_a_submission_with_no_timestamp_stands_alone() -> None:
    """A row with no time cannot honestly be chained to anything, and this is
    the answer `batches` gives to the question `workbook.py` poses.
    """
    found = batches([at(0), submission(row=99, at=None), at(1)])

    assert sizes(found) == [2, 1]
    assert found[1][0].row == 99


def test_a_submission_with_no_timestamp_stands_alone_even_naming_somebody() -> None:
    """The two exclusions are independent: a named row with no time is still
    unplaceable, so the name does not rescue it into a neighbour's batch.
    """
    assert sizes(batches([at(0), submission(row=99, at=None, name="Ada"), at(0.1)])) == [2, 1]


def test_no_submissions_yield_no_batches() -> None:
    """The importer is handed whatever the population rule left, which over an
    export with nothing in it is nothing.
    """
    assert batches([]) == []


def test_batches_come_back_in_sheet_order() -> None:
    """Two runs over the same submissions produce the same list, so the
    importer's output does not depend on dictionary iteration order.
    """
    found = batches([at(6, name="Grace"), at(0, name="Ada"), at(1, name="Ada"), at(7, name="Grace")])

    assert [batch[0].row for batch in found] == sorted(batch[0].row for batch in found)
    assert sizes(found) == [2, 2]


def test_submissions_written_in_the_same_second_are_ordered_by_sheet_row() -> None:
    """A tie on the timestamp is broken by the row, because a Submission is
    not comparable and sorting the whole entry would raise rather than fall
    through to it.
    """
    same = datetime(2026, 8, 21, 13, 0, 0)
    found = batches([submission(row=9, at=same), submission(row=3, at=same)])

    assert [s.row for s in found[0]] == [3, 9]


def test_the_email_key_makes_one_prolific_submitter_of_everybody_without_one() -> None:
    """The settled half of the submitter question, and the reason the report
    prints the reading twice. Two strangers who happened to leave the email
    field blank a minute apart are one trip under the reading that was quoted,
    and two trips under the reading that lets `None` mean nobody.
    """
    strangers = [at(0, email="", name="Ada"), at(1, email="", name="Grace")]

    assert sizes(batches(strangers, submitter=by_email_or_nobody)) == [2]
    assert sizes(batches(strangers, submitter=by_email)) == [1, 1]
    assert sizes(batches(strangers, submitter=by_name)) == [1, 1]


def test_the_name_fallback_splits_one_person_who_sometimes_typed_an_email() -> None:
    """Why the the fallback key is the one that invents submitters, and it
    invents a second submitter out of a field somebody left blank.
    """
    ada = [at(0, email="ada@example.net", name="Ada"), at(1, email="", name="Ada")]

    assert sizes(batches(ada, submitter=by_name)) == [2]
    assert sizes(batches(ada, submitter=by_email_or_name)) == [1, 1]


def test_each_key_says_nobody_where_it_has_nothing_to_go_on() -> None:
    """`None` is what makes a row stand alone, so a key that returned the
    empty string instead would quietly rejoin the rows it cannot attribute.
    """
    nothing = submission(email="", name="")

    assert by_name(nothing) is None
    assert by_email(nothing) is None
    assert by_email_or_name(nothing) is None
    # The reading being argued against, which is exactly the one that does not.
    assert by_email_or_nobody(nothing) == ""


def test_the_report_partitions_every_submission_exactly_once() -> None:
    """The three lines under the population are a partition. A batch that lost
    a row between the grouping and the report is how a figure nobody could
    reproduce got published in the first place.
    """
    sheet = sheet_of(
        [
            at(0, name="Ada"),
            at(1, name="Ada"),
            at(40, name="Ada"),
            at(2, name=""),
            submission(row=99, at=None, name="Grace"),
        ],
    )

    counted = dict(section(sheet)[1])
    assert counted["submissions"] == 5
    assert counted["  inside a batch of more than one"] == 2
    assert counted["  alone in a batch of their own"] == 1
    assert counted["  naming nobody, or no time, alone by that rule"] == 2
    assert (
        counted["  inside a batch of more than one"]
        + counted["  alone in a batch of their own"]
        + counted["  naming nobody, or no time, alone by that rule"]
        == counted["submissions"]
    )
    assert counted["batches"] == 4
    assert counted["largest batch"] == 2
    assert counted["submitters the rule can name"] == 2


def test_the_report_prices_the_anchored_window_beside_the_chained_one() -> None:
    """The largest batch is what the window question decides, so both readings
    of it are printed rather than one being described in prose.
    """
    steady = sheet_of([at(minutes, name="Ada") for minutes in (0, 2, 4, 6, 8, 10, 12)])

    counted = dict(section(steady)[1])
    assert counted["largest batch"] == 7
    assert counted["batches"] == 1
    assert counted["anchoring a fixed window instead, batches"] == 2
    assert counted["  the anchored reading, largest batch"] == 6
    assert counted["  the anchored reading, inside a batch"] == 6


def test_the_report_prices_both_readings_of_the_email_key() -> None:
    """What is done with the emailless rows moves this key more than the key
    itself does, so the report carries both numbers rather than one.
    """
    strangers = sheet_of([at(0, email="", name="Ada"), at(1, email="", name="Grace")])

    counted = dict(section(strangers)[1])
    assert counted["keying on the email instead, submitters"] == 0
    # Nobody is inside a batch once the empty key stops being a submitter, and
    # everybody is while it is one.
    assert counted["  the email key, inside a batch"] == 0
    assert counted["  the email key, chaining the emailless as one"] == 2
    assert counted["   that reading's largest batch"] == 2


def test_the_report_counts_the_fallback_keys_it_declines_to_use() -> None:
    """One person, two keys, and the report says so: 'keys' rather than
    'submitters', because that is the whole objection to the reading.
    """
    ada = sheet_of([at(0, email="ada@example.net", name="Ada"), at(1, email="", name="Ada")])

    counted = dict(section(ada)[1])
    assert counted["submitters the rule can name"] == 1
    assert counted["keying on the email with a name fallback, keys"] == 2
    assert counted["largest batch"] == 2
    assert counted["  the fallback key, inside a batch"] == 0
    assert counted["  the fallback key, largest batch"] == 1


def test_no_two_lines_of_the_report_carry_the_same_label() -> None:
    """Four readings each report a largest batch, so the labels have to name
    the reading. A duplicate would be invisible in the printed section and
    would silently drop a figure from anything reading it back as a mapping.
    """
    counted = section(sheet_of([at(0), at(1)]))[1]

    assert len({label for label, _ in counted}) == len(counted)


def test_a_name_field_that_is_not_a_name_is_not_a_submitter() -> None:
    """Six entries in the export answer the name field with something that is
    not one. Taken at face value each becomes a submitter, so rows by
    unrelated people chain into one trip -- and the volunteer the same row
    imports as, which rule 6 decides, would disagree.
    """
    assert by_name(submission(name="update inventory")) is None
    assert by_name(submission(name="5.0")) is None
    assert by_name(submission(name="lydon@nycmesh.net")) is None
    assert by_name(submission(name="Ada")) == "ada"


def test_two_rows_naming_nothing_are_not_one_submitter() -> None:
    """The failure the line above prevents: without it both rows key on the
    same string and chain into a batch neither person made.
    """
    sheet = sheet_of(
        [
            submission(row=2, name="testing", at=AT),
            submission(row=3, name="update inventory", at=AT + timedelta(minutes=1)),
        ],
    )

    assert sizes(batches(sheet.submissions)) == [1, 1]
