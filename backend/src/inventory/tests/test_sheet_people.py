"""Tests for rule 6, which volunteer a submission is from.

The properties worth pinning are the ones that decide a headcount: a spelling
is joined to another only when the workbook shows both that it is the same
name and that it is the same person, a name nothing settles stays its own
volunteer and says it is unsure, and an address stands in only where it points
at exactly one person.
"""

import pytest

from inventory.sheet.people import NOT_A_NAME, Directory, How, addressed, directory, near, section, spelled
from inventory.tests.sheets import sheet_of, submission


def test_case_is_folded_because_it_has_never_distinguished_two_people() -> None:
    assert spelled("Ada") == spelled("ada") == spelled("ADA") == "ada"


@pytest.mark.parametrize("written", sorted(NOT_A_NAME))
def test_a_name_field_holding_no_name_yields_no_key(written: str) -> None:
    """Each of these is a judgement about one string somebody typed, so the
    reason is written beside it rather than inferred.
    """
    assert spelled(written) == ""
    assert NOT_A_NAME[written]


def test_an_address_typed_into_the_name_field_is_read_as_an_address() -> None:
    """A rule rather than a table entry, and it keeps the fallback from
    minting a volunteer whose name is their email.
    """
    assert spelled("ada@example.net") == ""


def test_an_empty_name_field_yields_no_key() -> None:
    assert spelled("") == ""


@pytest.mark.parametrize(
    ("one", "other", "why"),
    [
        ("ada b", "adab", "the same name but for the spaces in it"),
        ("ada", "ada lovelace", "the same name written longer"),
        ("adab", "ada", "the same name written longer"),
        ("aidan", "aiden", "the same name but for one character"),
        ("ada", "adam", "the same name written longer"),
        ("grace", "gracie", "the same name but for one character"),
    ],
)
def test_two_spellings_that_might_be_one_name(one: str, other: str, why: str) -> None:
    assert near(one, other) == why


@pytest.mark.parametrize(
    ("one", "other"),
    [
        ("ada", "grace"),
        ("ada lovelace", "ada byron"),
        ("bob", "robert"),
        # Two characters apart, which is where the reading stops.
        ("aidan", "eiden"),
        # The same length, and differing from the first character on.
        ("bob", "rob r"),
    ],
)
def test_two_spellings_that_are_not_one_name(one: str, other: str) -> None:
    assert near(one, other) == ""


def test_a_longer_name_beside_the_same_address_is_the_same_volunteer() -> None:
    """Both halves hold: the same name written longer, and an address the two
    spellings share.
    """
    who = directory(
        sheet_of(
            [
                submission(name="Ada", email="ada@example.net"),
                submission(name="Ada Lovelace", email="ada@example.net"),
            ],
        ),
    )

    assert who.by_name["ada"] == who.by_name["ada lovelace"]
    assert len(who.volunteers) == 1


def test_a_shared_address_alone_does_not_join_two_names() -> None:
    """The failure this half exists to stop: a volunteer whose hands are full
    asks whoever holds a phone to submit for them, so unioning on the address
    alone pulls everybody it ever carried into one row.
    """
    who = directory(
        sheet_of(
            [
                submission(name="Ada", email="ada@example.net"),
                submission(name="Grace", email="ada@example.net"),
            ],
        ),
    )

    assert who.by_name["ada"] != who.by_name["grace"]
    assert len(who.volunteers) == 2


def test_the_same_name_without_a_shared_address_is_two_volunteers() -> None:
    """A shared spelling with no address behind it is not evidence of one
    person, so the rule declines to join them.
    """
    who = directory(
        sheet_of(
            [
                submission(name="Ada", email=""),
                submission(name="Ada Lovelace", email=""),
            ],
        ),
    )

    assert len(who.volunteers) == 2
    assert set(who.flagged) == who.volunteers


def test_a_volunteer_that_gave_an_address_is_not_flagged() -> None:
    """An address is what settles a doubt, so a volunteer carrying one is not
    a question for an administrator however near another name theirs is.
    """
    who = directory(
        sheet_of(
            [
                submission(name="Ada", email="ada@example.net"),
                submission(name="Ada Lovelace", email=""),
            ],
        ),
    )

    assert set(who.flagged) == {"ada lovelace"}
    assert who.flagged["ada lovelace"] == ("ada",)


def test_a_volunteer_near_nobody_is_not_flagged() -> None:
    who = directory(
        sheet_of([submission(name="Grace", email=""), submission(name="Ada", email="")]),
    )

    assert who.flagged == {}


def test_the_busiest_spelling_speaks_for_a_group() -> None:
    """A volunteer's key is a spelling somebody wrote rather than one this
    invents, and the one most of them wrote.
    """
    who = directory(
        sheet_of(
            [
                submission(name="Ada Lovelace", email="ada@example.net"),
                submission(name="Ada Lovelace", email="ada@example.net"),
                submission(name="Ada", email="ada@example.net"),
            ],
        ),
    )

    assert who.by_name["ada"] == "ada lovelace"


def test_a_chain_of_joins_reaches_one_volunteer() -> None:
    """`Ada` joins `Ada L` on one address and `Ada L` joins `Ada Lovelace` on
    another, so all three are one volunteer even though the outer two share no
    address of their own.
    """
    who = directory(
        sheet_of(
            [
                submission(name="Ada", email="ada@example.net"),
                submission(name="Ada L", email="ada@example.net"),
                submission(name="Ada L", email="lovelace@example.net"),
                submission(name="Ada Lovelace", email="lovelace@example.net"),
            ],
        ),
    )

    assert len({who.by_name[name] for name in ("ada", "ada l", "ada lovelace")}) == 1


def test_an_address_naming_one_volunteer_stands_in_for_a_missing_name() -> None:
    sheet = sheet_of(
        [
            submission(name="Ada", email="ada@example.net"),
            submission(name="", email="ada@example.net"),
        ],
    )
    who = directory(sheet)

    reached = who.volunteer(sheet.submissions[1])
    assert (reached.key, reached.how) == ("ada", How.ADDRESS)
    assert who.volunteers == {"ada"}


def test_an_address_nobody_is_named_beside_is_a_volunteer_of_its_own() -> None:
    """The movement stays attributed to a row an administrator can later put
    a name to, rather than being dropped.
    """
    sheet = sheet_of([submission(name="", email="ghost@example.net")])
    who = directory(sheet)

    reached = who.volunteer(sheet.submissions[0])
    assert (reached.key, reached.how) == ("ghost@example.net", How.ADDRESS)
    assert who.flagged["ghost@example.net"] == ()


def test_an_address_naming_more_than_one_volunteer_names_nobody() -> None:
    """minting a row for it would duplicate somebody the directory already holds."""
    sheet = sheet_of(
        [
            submission(name="Ada", email="shared@example.net"),
            submission(name="Grace", email="shared@example.net"),
            submission(name="", email="shared@example.net"),
        ],
    )
    who = directory(sheet)

    reached = who.volunteer(sheet.submissions[2])
    assert (reached.key, reached.how) == (None, How.NOBODY)
    assert reached.why == "the address is written beside more than one volunteer"


def test_a_submission_carrying_neither_reaches_nobody() -> None:
    sheet = sheet_of([submission(name="", email="")])

    reached = directory(sheet).volunteer(sheet.submissions[0])

    assert (reached.key, reached.how, reached.why) == (None, How.NOBODY, "no name and no address")


def test_a_name_field_that_is_not_a_name_falls_through_to_the_address() -> None:
    """`Testing` is not a volunteer, and the address beside it is where the
    row's attribution actually is.
    """
    sheet = sheet_of(
        [
            submission(name="Ada", email="ada@example.net"),
            submission(name="Testing", email="ada@example.net"),
        ],
    )

    reached = directory(sheet).volunteer(sheet.submissions[1])

    assert (reached.key, reached.how) == ("ada", How.ADDRESS)


def test_a_spelling_the_directory_never_saw_is_its_own_volunteer() -> None:
    """Which is what the rule says about every spelling nothing joins, so a
    directory built over one export answers about a row from another.
    """
    who = directory(sheet_of([submission(name="Ada", email="ada@example.net")]))

    reached = who.volunteer(submission(name="Grace", email=""))

    assert (reached.key, reached.how) == ("grace", How.NAME)


def test_survivors_is_the_floor_the_flags_leave() -> None:
    """A flag is a question, so the rows minted are the top of the headcount
    and this is the bottom: what is left if every question answers "yes".
    """
    who = directory(
        sheet_of(
            [
                submission(name="Ada", email=""),
                submission(name="Ada Lovelace", email=""),
                submission(name="Grace", email="grace@example.net"),
            ],
        ),
    )

    assert len(who.volunteers) == 3
    assert who.survivors() == 2


def test_an_address_only_volunteer_survives_because_it_might_be_anybody() -> None:
    """There is nobody in particular for it to be, so it cannot be merged away
    in the floor the way a flagged name can.
    """
    who = directory(
        sheet_of(
            [
                submission(name="Ada", email="ada@example.net"),
                submission(name="", email="ghost@example.net"),
            ],
        ),
    )

    assert who.survivors() == 2


def test_a_directory_built_by_hand_answers_the_same_way() -> None:
    """The rule is the directory's, not `directory()`'s, so the importer can
    hold one it assembled itself.
    """
    who = Directory(by_name={"ada": "ada"}, by_address={}, flagged={}, held={}, shared={}, submissions={"ada": 1})

    assert who.volunteers == {"ada"}
    assert who.volunteer(submission(name="Ada", email="")).key == "ada"


REPORTED = sheet_of(
    [
        # One volunteer written two ways, joined by the address they share.
        submission(name="Ada", email="ada@example.net"),
        submission(name="Ada Lovelace", email="ada@example.net"),
        # Two spellings nothing settles: separate volunteers, both flagged.
        submission(name="Grace", email=""),
        submission(name="Grace Hopper", email=""),
        # A volunteer with an address, near nobody.
        submission(name="Bob", email="bob@example.net"),
        # A name field holding no name, and no address to fall back to.
        submission(name="5.0", email=""),
        # An address nobody is named beside: a volunteer of its own.
        submission(name="", email="ghost@example.net"),
    ],
)


def test_the_report_partitions_the_spellings_and_the_submissions() -> None:
    _, counted = section(REPORTED)

    partition = dict(counted)
    assert partition["distinct name spellings"] == 6
    assert partition["  the same but for case"] == 0
    assert partition["distinct names"] == 6
    assert partition["  holding no name at all"] == 1
    assert partition["  joined to another by a shared address"] == 1
    assert partition["  a volunteer in their own right"] == 4
    assert partition["volunteers the import mints"] == 5
    assert partition["  known only by an address"] == 1
    assert partition["  flagged as possibly a duplicate"] == 3
    assert partition["the fewest volunteers this can be"] == 4
    assert partition["submissions reaching a volunteer"] == 6
    assert partition["  by the name field"] == 5
    assert partition["  by an address, the name being unusable"] == 1
    assert partition["submissions reaching nobody"] == 1


def test_the_report_counts_spellings_that_differ_only_in_case() -> None:
    """The line is what says how much of the 102 the fold answers, so it has
    to move when a case variant does.
    """
    _, counted = section(sheet_of([submission(name="Ada"), submission(name="ada")]))

    assert dict(counted)["  the same but for case"] == 1
    assert dict(counted)["distinct names"] == 1


def test_the_report_notices_a_not_a_name_entry_no_submission_wrote() -> None:
    """A table entry that has gone stale would otherwise be silent, the same
    self-check items.py runs over its alias targets.
    """
    _, counted = section(sheet_of([submission(name="Ada")]))

    assert dict(counted)["not-a-name entries no submission wrote"] == len(NOT_A_NAME)


def test_the_floor_never_comes_out_above_the_ceiling() -> None:
    """The case `directory` explains: recognising a self-minted address by its
    key equalling the address cannot tell it from a volunteer whose name is
    that address, and counted one person twice.
    """
    who = directory(
        sheet_of(
            [
                submission(row=2, name="Sean", email="sean"),
                submission(row=3, name="", email="sean"),
            ],
        ),
    )

    assert who.volunteers == {"sean"}
    assert who.flagged == {}
    assert who.survivors() == 1


def test_an_address_nobody_names_is_still_flagged_when_it_is_not_a_name() -> None:
    """The guard above must not stop doing the job it was there for."""
    who = directory(sheet_of([submission(name="", email="nobody@example.net")]))

    assert who.flagged == {"nobody@example.net": ()}


def test_the_address_key_is_folded_the_way_the_name_key_is() -> None:
    """Rule 5 keys on this answer too, so a fold written inline in both
    modules is two of them deciding separately whether two addresses are one.
    `lower` rather than `casefold`, matching the database's own column.
    """
    assert addressed(" ADA@example.net ".strip()) == "ada@example.net"
    # casefold() maps this to `ss` and lower() does not; the identifier
    # tables say they are two.
    assert addressed("STRASSE@example.net") != addressed("Straße@example.net")
