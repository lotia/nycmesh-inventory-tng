"""Tests for the import that turns the export's people into `Volunteer` rows.

Rule 6 is tested in `test_sheet_people`; what is claimed here is what the
import does with its answer. Three properties carry the design: every
volunteer the rule names gets a row, nothing the rule is unsure of is merged,
and what it is unsure of is on the row rather than in the output -- so the
assertions are mostly about `merged_into` staying empty and `sheet_flag` not.

The sheets say only who wrote what, because that is all rule 6 reads.
"""

import io
from typing import Any

import pytest
from django.core.management import call_command

from inventory.management.commands import _people, _staging
from inventory.management.commands._people import LONGEST_NAME
from inventory.models import Volunteer
from inventory.sheet.workbook import Sheet
from inventory.tests.sheets import sheet_of, submission

pytestmark = pytest.mark.django_db


def wrote(*written: tuple[str, str]) -> Sheet:
    """A sheet of submissions differing only in the name and address on them."""
    return sheet_of(
        [submission(row=number, name=name, email=email) for number, (name, email) in enumerate(written, start=2)],
    )


def named(display_name: str) -> Volunteer:
    return Volunteer.objects.get(display_name=display_name)


def test_every_volunteer_the_export_names_becomes_a_row() -> None:
    minted = _people.mint(wrote(("Ada", "ada@example.net"), ("Grace", "grace@example.net")))

    assert (minted.volunteers, minted.created, minted.already) == (2, 2, 0)
    assert list(Volunteer.objects.values_list("display_name", "sheet_key")) == [("Ada", "ada"), ("Grace", "grace")]


def test_a_volunteer_is_shown_by_a_spelling_somebody_actually_wrote() -> None:
    """The rule keys on the folded spelling, which is the right key and the
    wrong thing to put in front of an administrator. The busiest spelling of
    the key is what the row shows.
    """
    _people.mint(wrote(("Ada", ""), ("Ada", ""), ("ada", "")))

    assert Volunteer.objects.get().display_name == "Ada"


def test_a_volunteer_known_only_by_an_address_is_a_row_too() -> None:
    """So the movement stays attributed to somebody an administrator can name
    later, rather than to nobody.
    """
    minted = _people.mint(wrote(("Ada", "ada@example.net"), ("testing", "ghost@example.net")))

    assert (minted.volunteers, minted.by_address) == (2, 1)
    assert named("ghost@example.net").sheet_key == "ghost@example.net"


def test_a_flag_names_the_volunteers_this_one_might_be() -> None:
    """Two short names a character apart, neither beside an address. The rule
    declines to join them and says so; this is where it says it.
    """
    minted = _people.mint(wrote(("Aidan", ""), ("Aiden", "")))

    assert minted.flagged == 2
    assert named("Aidan").sheet_flag.startswith("Possibly the same person as Aiden.")
    assert named("Aiden").sheet_flag.startswith("Possibly the same person as Aidan.")


def test_an_address_nobody_was_named_beside_is_flagged_as_the_question_it_is() -> None:
    """A different question from the one above -- there is nobody in
    particular for it to be a duplicate of -- so it is a different sentence.
    """
    _people.mint(wrote(("testing", "ghost@example.net")))

    assert named("ghost@example.net").sheet_flag.startswith("Known only by an address")


def test_the_import_merges_nobody() -> None:
    """The whole reason a flag exists rather than a merge: the import proposes
    and an administrator disposes, for the reason `_people.py` gives.
    """
    _people.mint(wrote(("Aidan", ""), ("Aiden", "")))

    assert Volunteer.objects.selectable().count() == 2
    assert not Volunteer.objects.filter(merged_into__isnull=False).exists()


def test_a_volunteer_the_rule_is_sure_of_is_flagged_with_nothing() -> None:
    _people.mint(wrote(("Ada", "ada@example.net")))

    assert named("Ada").sheet_flag == ""


def test_an_address_two_volunteers_wrote_becomes_neither_ones_email() -> None:
    """The column is unique, so one of the two would otherwise get it and the
    other would get whichever error the constraint raised.
    """
    minted = _people.mint(wrote(("Ada", "shared@example.net"), ("Grace", "shared@example.net")))

    assert minted.addressed == 0
    assert list(Volunteer.objects.values_list("email", flat=True)) == [None, None]


def test_a_volunteer_who_wrote_several_addresses_keeps_the_busiest() -> None:
    """One column, several addresses, and the rest still readable in the
    staged rows.
    """
    minted = _people.mint(wrote(("Ada", "home@example.net"), ("Ada", "mesh@example.net"), ("Ada", "mesh@example.net")))

    assert minted.addressed == 1
    assert Volunteer.objects.get().email == "mesh@example.net"


def test_a_volunteer_who_wrote_no_address_has_none_rather_than_an_empty_one() -> None:
    """NULL and not "", or the second such volunteer collides with the first."""
    _people.mint(wrote(("Ada", ""), ("Grace", "")))

    assert list(Volunteer.objects.values_list("email", flat=True)) == [None, None]


def test_an_address_a_volunteer_already_here_holds_is_left_off_the_new_row() -> None:
    """Somebody self-registered with an address the export also carries. The
    column is unique, so writing it would end the whole step on a constraint
    the operator cannot act on -- after two earlier steps have committed.
    """
    already = Volunteer.objects.create(display_name="Jo", email="jo@example.net")

    minted = _people.mint(wrote(("Jo", "jo@example.net")))

    assert (minted.address_held, minted.addressed) == (1, 0)
    assert Volunteer.objects.get(sheet_key="jo").email is None
    already.refresh_from_db()
    assert (already.email, already.sheet_key) == ("jo@example.net", None)


def test_a_spelling_that_stops_speaking_leaves_its_address_where_it_is() -> None:
    """The other way in, and the one no fresh database shows: `jo b` speaks for
    the group until a refreshed export makes `jo` the busier spelling, so the
    key moves and the next run mints beside the row rather than finding it. The
    address is on the first row and stays there.
    """
    _people.mint(wrote(("jo b", "jo@example.net"), ("jo b", "jo@example.net"), ("jo", "jo@example.net")))
    assert Volunteer.objects.get().sheet_key == "jo b"

    minted = _people.mint(
        wrote(
            ("jo b", "jo@example.net"),
            ("jo b", "jo@example.net"),
            ("jo", "jo@example.net"),
            ("jo", "jo@example.net"),
            ("jo", "jo@example.net"),
        )
    )

    assert minted.address_held == 1
    assert list(Volunteer.objects.order_by("sheet_key").values_list("sheet_key", "email")) == [
        ("jo", None),
        ("jo b", "jo@example.net"),
    ]


def test_something_with_no_at_sign_in_it_is_not_an_address() -> None:
    """`EmailField` would take it and the admin would then refuse to save the
    volunteer until somebody invented an address for them.
    """
    minted = _people.mint(wrote(("Ada", "n/a")))

    assert minted.addressed == 0
    assert named("Ada").email is None


def test_the_submissions_that_reach_nobody_mint_nobody() -> None:
    """There is nothing to key a volunteer on, and a shared stand-in would
    attribute several people's work to one row that is not a person.
    """
    minted = _people.mint(wrote(("Ada", "ada@example.net"), ("testing", "")))

    assert (minted.volunteers, minted.nobody) == (1, 1)
    assert Volunteer.objects.count() == 1


def test_the_length_a_name_is_shortened_to_is_the_columns_own() -> None:
    """`_people` writes the number out, so this is what keeps a widened column
    from going on being cut at the old width.
    """
    # `Any` for the reason `test_mint_items` gives about the same expression.
    column: Any = Volunteer._meta.get_field("display_name")

    assert column.max_length == LONGEST_NAME


def test_a_name_longer_than_the_column_is_shortened_rather_than_refused() -> None:
    """Every volunteer the rule names has to get a row -- the ledger import
    joins to it -- so this is the one cell that is cut instead of declined. The
    whole spelling is still on `sheet_key`, which is unbounded.
    """
    typed = "Ada " + "Byron " * 40

    minted = _people.mint(wrote((typed, "")))

    volunteer = Volunteer.objects.get()
    assert minted.shortened == 1
    assert len(volunteer.display_name) == LONGEST_NAME
    assert volunteer.display_name.endswith("…")
    assert volunteer.sheet_key == typed.lower()


def test_an_address_only_volunteer_is_shown_by_a_shortened_address_too() -> None:
    """`shown` falls back to the key, which for these is the address, and an
    address is as free-text as a name.
    """
    address = "a" * LONGEST_NAME + "@example.net"

    minted = _people.mint(wrote(("testing", address)))

    assert minted.shortened == 1
    assert len(Volunteer.objects.get().display_name) == LONGEST_NAME


def test_a_second_run_mints_nobody_twice() -> None:
    sheet = wrote(("Ada", "ada@example.net"), ("Aidan", ""), ("Aiden", ""))
    _people.mint(sheet)

    again = _people.mint(sheet)

    assert (again.volunteers, again.created, again.already) == (3, 0, 3)
    assert Volunteer.objects.count() == 3


def test_a_second_run_leaves_a_name_an_administrator_has_corrected_alone() -> None:
    """The half of `_people.py`'s re-run rule that writes nothing: a name on a
    row that is already here is left exactly as it is.
    """
    sheet = wrote(("Aidan", ""), ("Aiden", ""))
    _people.mint(sheet)
    settled = named("Aidan")
    settled.display_name = "Aidan Byron"
    settled.save()

    _people.mint(sheet)

    settled.refresh_from_db()
    assert settled.display_name == "Aidan Byron"


def test_a_doubt_about_a_volunteer_already_here_reaches_their_row() -> None:
    """The rule's answer changes when the export does: a refreshed export adds
    one near-miss spelling and raises a doubt about the volunteer it is near,
    who has had a row since the first run. A flag that stopped at the report
    would leave half the question invisible to the only person who can settle
    it, and the count claiming an outcome that did not happen.
    """
    _people.mint(wrote(("sean", "")))
    assert named("sean").sheet_flag == ""

    minted = _people.mint(wrote(("sean", ""), ("seon", "")))

    assert (minted.flagged, minted.flagged_now) == (2, 2)
    assert named("sean").sheet_flag.startswith("Possibly the same person as seon.")
    assert Volunteer.objects.exclude(sheet_flag="").count() == minted.flagged


def test_a_flag_already_on_a_row_is_not_written_over() -> None:
    """It may be an administrator's own words by now, and the import has
    nothing to say that is worth losing them for.
    """
    _people.mint(wrote(("Aidan", ""), ("Aiden", "")))
    settled = named("Aidan")
    settled.sheet_flag = "Spoke to them: two different people, leaving both."
    settled.save()

    minted = _people.mint(wrote(("Aidan", ""), ("Aiden", ""), ("Aidon", "")))

    settled.refresh_from_db()
    assert settled.sheet_flag == "Spoke to them: two different people, leaving both."
    assert (minted.flagged, minted.flagged_now) == (3, 1)


def test_a_question_settled_by_merging_is_not_asked_again() -> None:
    """Merging is the settlement the flag asks for, and it takes the row out of
    the list the flag is read off, so re-flagging it would be asking a question
    of a row nobody is offered any more.
    """
    _people.mint(wrote(("sean", ""), ("Ada", "ada@example.net")))
    merged = named("sean")
    merged.merged_into = named("Ada")
    merged.save()

    minted = _people.mint(wrote(("sean", ""), ("seon", ""), ("Ada", "ada@example.net")))

    merged.refresh_from_db()
    assert merged.sheet_flag == ""
    assert (minted.flagged, minted.flagged_now) == (1, 1)


def test_the_command_reads_the_staged_rows_and_says_what_it_minted() -> None:
    """Through the staging tables, which is how it is run: the workbook is not
    ours to publish and everything after `stage_sheet` works without it.
    """
    _staging.stage(wrote(("Ada", "ada@example.net"), ("Aidan", ""), ("Aiden", ""), ("testing", "ghost@example.net")))
    out = io.StringIO()

    call_command("import_volunteers", stdout=out)

    printed = [" ".join(line.split()) for line in out.getvalue().splitlines()]
    assert printed == [
        "Volunteers",
        "volunteers the export names 4",
        "minted by this run 4",
        "already here 0",
        "known only by an address 1",
        "flagged for an administrator 3",
        "of those, flagged by this run 3",
        "given an address of their own 2",
        "whose address is already another row's 0",
        "shown by a shortened name 0",
        "submissions reaching nobody 0",
    ]
