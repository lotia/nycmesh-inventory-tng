"""Tests for the catalogue and identifiers rule 1's answers become.

Sheets are built with ``sheets.sheet_of`` rather than from a workbook: what is
being claimed here is about what a resolution becomes in the database, and the
trip from a spreadsheet cell to a staged row is already claimed, and tested, in
``test_stage_sheet``. The one test that goes through the command stages a real
workbook, because the command's whole point is that it reads the staged tables
and never a file.
"""

import io
from pathlib import Path
from typing import Any

import pytest
from django.core.management import call_command
from django.db import IntegrityError, connection

from inventory.management.commands import _identifiers, _staging
from inventory.management.commands._identifiers import LONGEST_NAME, LONGEST_VALUE, UNCATEGORISED
from inventory.models import Category, Item, ItemIdentifier
from inventory.sheet import workbook
from inventory.staging import UnresolvedItemString
from inventory.tests.sheets import sheet_of, submission
from inventory.tests.workbooks import build

pytestmark = pytest.mark.django_db


def identifiers() -> list[tuple[str, str, str]]:
    """Every identifier, as the three things this rule decides about one."""
    return sorted((one.value, one.kind, one.item.name) for one in ItemIdentifier.objects.select_related("item"))


def test_every_catalogued_name_becomes_an_item_and_names_itself() -> None:
    """The name a volunteer sees in the pick-list is a string that has meant
    the item, so it is an identifier like any other -- and an item nobody has
    ever submitted still has to exist for the ones who will.
    """
    minted = _identifiers.mint(sheet_of([submission(item="LiteBeam")], catalogue=("LiteBeam", "Omni DC")))

    assert (minted.items, minted.items_added) == (2, 2)
    assert list(Item.objects.values_list("name", flat=True)) == ["LiteBeam", "Omni DC"]
    assert identifiers() == [
        ("LiteBeam", ItemIdentifier.Kind.ALIAS, "LiteBeam"),
        ("Omni DC", ItemIdentifier.Kind.ALIAS, "Omni DC"),
    ]


def test_an_imported_item_lands_somewhere_that_says_it_is_not_categorised() -> None:
    """`Item.category` cannot be empty and the export supplies no grouping, so
    an imported item lands in one whose name is the admission. `UNCATEGORISED`
    argues it.
    """
    _identifiers.mint(sheet_of([submission()], catalogue=("LiteBeam",)))

    assert Item.objects.get(name="LiteBeam").category.name == UNCATEGORISED
    assert Category.objects.get(name=UNCATEGORISED).parent is None


def test_an_item_already_in_the_catalogue_is_left_where_it_is(category: Category) -> None:
    """Somebody who has already filed an item under a real category has made a
    decision, and a re-run of the import is not a reason to undo it.
    """
    Item.objects.create(name="LiteBeam", category=category)

    minted = _identifiers.mint(sheet_of([submission()], catalogue=("LiteBeam",)))

    assert (minted.items, minted.items_added) == (1, 0)
    assert Item.objects.get(name="LiteBeam").category == category


def test_a_string_differing_only_in_case_is_the_same_identifier() -> None:
    """`value_normalised` is unique, so the second spelling is not a second
    row -- and inserting it as one would raise rather than merge.
    """
    minted = _identifiers.mint(
        sheet_of([submission(item="litebeam"), submission(row=3, item="LiteBeam")], catalogue=("LiteBeam",))
    )

    assert (minted.identifiers, minted.identifiers_added) == (1, 1)
    # The catalogue's spelling, because the catalogue is minted first: it is
    # the one the pick-list shows and the one a person recognises.
    assert identifiers() == [("LiteBeam", ItemIdentifier.Kind.ALIAS, "LiteBeam")]


def test_the_database_disagreeing_with_python_costs_one_row_and_not_the_import() -> None:
    """The load-bearing case decision 0026 was written for.

    `İSTANBUL` and `ISTANBUL` are one string to PostgreSQL, which folds U+0130
    to a bare `i`, and two to Python, which leaves a combining dot behind. So
    the catalogue tab keeps both -- it dedupes with Python's fold -- two items
    are minted, and the second identifier meets a unique index that has already
    seen its own spelling of the first.

    Before this, `mint` was atomic and the insert was not, so that single
    disagreement rolled back the entire catalogue and the operator was shown a
    constraint name. Now it is a savepoint, a number in the report, and an
    import that finished.
    """
    minted = _identifiers.mint(sheet_of([submission()], catalogue=("ISTANBUL", "İSTANBUL")))

    assert minted.items_added == 2, "both spellings are still separate items; only the identifier collides"
    assert minted.folds_disagreed == 1
    # One identifier, because one is what the column will hold -- counted by
    # the database's answer rather than by Python's, which would say two.
    assert (minted.identifiers, minted.identifiers_added) == (1, 1)
    assert ItemIdentifier.objects.count() == 1
    # And the second item is reported as one whose string names another item,
    # which is the pre-existing signal for exactly this shape of problem.
    assert minted.naming_another_item == 1


def test_an_integrity_error_that_is_not_the_unique_index_is_not_swallowed(category: Category) -> None:
    """The refusal is an answer only when it is the answer being asked for.

    `_hold` reads an `IntegrityError` as "the database already holds this
    string", which is right for the unique index and wrong for everything else.
    So it goes back and asks which row holds it, and re-raises when the answer
    is that none does -- here, a foreign key pointing at an item that has been
    deleted. Treating that as an ordinary outcome would turn a broken import
    into a report full of zeroes.
    """
    item = Item.objects.create(name="Vanishing", category=category)
    Item.objects.filter(pk=item.pk).delete()
    # Django makes foreign keys DEFERRABLE INITIALLY DEFERRED, so without this
    # the violation surfaces at commit -- long after `_hold` has returned, and
    # in a test that would have passed while proving nothing.
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")

    with pytest.raises(IntegrityError):
        _identifiers._hold(item, "whatever")


def test_python_merging_two_names_the_database_keeps_apart_is_a_duplicate_row() -> None:
    """The disagreement read the other way round, and where it is caught.

    Python's `str.lower()` applies the final-sigma rule, so it makes one string
    of these two; PostgreSQL's `lower()` does not, so it makes two. Left to the
    minting loop that would be the worse outcome of the pair -- no exception,
    one item quietly given no identifier, and nothing in the report saying so.

    It never reaches the loop. `_tab` folds the catalogue before an `Item`
    exists, so this is one catalogued name and a duplicate row, which is
    already a number somebody reads. That is the check doing exactly what its
    docstring claims: settling a refusal before anything has been minted.

    The guard in the loop stands anyway, because the tab is not the only thing
    that reaches it, and because Python is not entitled to decide this either
    way round.
    """
    minted = _identifiers.mint(sheet_of([submission()], catalogue=("ΟΔΟΣ", "ΟΔΟς")))

    assert (minted.items, minted.items_added) == (1, 1)
    assert minted.duplicated == 1
    assert (minted.identifiers, minted.identifiers_added) == (1, 1)
    assert minted.folds_disagreed == 0
    # And the one item that exists has an identifier, which is the promise.
    assert ItemIdentifier.objects.count() == 1


def test_a_hand_written_alias_names_the_item_it_was_written_for() -> None:
    """`tp link` is the Archer router, which the catalogue calls `Tp-Link`."""
    _identifiers.mint(sheet_of([submission(item="tp link")], catalogue=("Tp-Link",)))

    assert ItemIdentifier.objects.get(value="tp link").item.name == "Tp-Link"


def test_a_retired_code_that_decodes_is_recorded_as_the_legacy_scheme() -> None:
    """The kind is the one the data model added for these, so an administrator
    reading the list can tell a 2022 SKU from something a volunteer typed.
    """
    _identifiers.mint(sheet_of([submission(item="NYCM-ER-LBEG2")], catalogue=("LiteBeam",)))

    assert identifiers() == [
        ("LiteBeam", ItemIdentifier.Kind.ALIAS, "LiteBeam"),
        ("NYCM-ER-LBEG2", ItemIdentifier.Kind.LEGACY_NYCM, "LiteBeam"),
    ]


def test_a_string_naming_no_catalogued_item_is_recorded_with_the_reason() -> None:
    """And gets no identifier. `mast` naming a stand-in would tell a volunteer
    the system knew which of the three they meant.
    """
    minted = _identifiers.mint(sheet_of([submission(item="mast")], catalogue=("LiteBeam",)))

    assert (minted.unresolved, minted.unaccounted) == (1, 0)
    assert ItemIdentifier.objects.filter(value="mast").count() == 0
    assert UnresolvedItemString.objects.get(value="mast").reason == "names one of three masts"


def test_a_string_nobody_accounted_for_is_recorded_with_no_reason_and_counted() -> None:
    """The outcome rule 1 says must never happen. It still has to be visible:
    a run that produced one has to say which string it was.
    """
    minted = _identifiers.mint(sheet_of([submission(item="thingummy")], catalogue=("LiteBeam",)))

    assert (minted.unresolved, minted.unaccounted) == (1, 1)
    assert UnresolvedItemString.objects.get(value="thingummy").reason == ""


def test_a_row_naming_no_item_at_all_produces_nothing() -> None:
    """Three rows of the export carry a direction and an empty item column.
    An empty string is not a string that ever named an item.
    """
    minted = _identifiers.mint(sheet_of([submission(item="")], catalogue=("LiteBeam",)))

    assert (minted.identifiers, minted.unresolved) == (1, 0)
    assert not UnresolvedItemString.objects.exists()


def test_a_second_run_mints_nothing_and_changes_nothing() -> None:
    """The whole import has to be re-runnable, so this step has to be."""
    sheet = sheet_of(
        [submission(item="tp link"), submission(row=3, item="mast")],
        catalogue=("Tp-Link", "LiteBeam"),
    )
    _identifiers.mint(sheet)
    before = identifiers()

    again = _identifiers.mint(sheet)

    assert (again.items_added, again.identifiers_added) == (0, 0)
    assert (again.items, again.identifiers, again.unresolved) == (2, 3, 1)
    assert identifiers() == before


def test_a_string_the_rule_can_now_answer_leaves_the_review_list() -> None:
    """Which is what the list is for: add the catalogue row it was asking for,
    stage and mint again, and the entry is replaced by the identifier.
    """
    _identifiers.mint(sheet_of([submission(item="Omni DC")], catalogue=("LiteBeam",)))
    assert UnresolvedItemString.objects.filter(value="Omni DC").exists()

    _identifiers.mint(sheet_of([submission(item="Omni DC")], catalogue=("LiteBeam", "Omni DC")))

    assert not UnresolvedItemString.objects.exists()
    assert ItemIdentifier.objects.get(value="Omni DC").item.name == "Omni DC"


def test_the_lengths_this_refuses_at_are_the_columns_own() -> None:
    """`_identifiers` writes them out rather than reading them off `_meta`, so
    this is what keeps a widened column from being refused at the old width.
    """
    # Annotated `Any` because `get_field` types as a field that may carry no
    # length at all, which is the very reason the module writes the numbers
    # out. See DEVELOPERS.md#typing.
    columns: list[Any] = [Item._meta.get_field("name"), ItemIdentifier._meta.get_field("value")]

    assert [column.max_length for column in columns] == [LONGEST_NAME, LONGEST_VALUE]


def test_two_catalogue_rows_spelling_one_name_catalogue_one_item() -> None:
    """`Item.name` is case-sensitively unique and the identifier table is not,
    so minting both would leave the second item with no identifier at all --
    reachable by no scan and no typed string, while the ledger goes on adding
    stock to it. Rule 1 can only ever reach the first of the pair, so the first
    is the one the catalogue keeps.
    """
    minted = _identifiers.mint(sheet_of([submission(item="Litebeam")], catalogue=("LiteBeam", "Litebeam")))

    assert (minted.items, minted.duplicated) == (1, 1)
    assert list(Item.objects.values_list("name", flat=True)) == ["LiteBeam"]
    assert identifiers() == [("LiteBeam", ItemIdentifier.Kind.ALIAS, "LiteBeam")]
    # Not curation: nobody pointed anything anywhere, and the line that would
    # have counted this says somebody did.
    assert minted.naming_another_item == 0
    assert set(Item.objects.values_list("pk", flat=True)) == set(
        ItemIdentifier.objects.values_list("item_id", flat=True)
    )


def test_a_catalogue_row_too_long_to_be_a_name_is_refused_and_nothing_is_written() -> None:
    """The whole step is one transaction, so a row the column cannot take
    would otherwise abort the import with a `psycopg` error and leave the
    operator no catalogue at all.
    """
    pasted = "L" * (LONGEST_NAME + 1)

    minted = _identifiers.mint(sheet_of([submission(item="LiteBeam")], catalogue=("LiteBeam", pasted)))

    assert (minted.items, minted.names_too_long) == (1, 1)
    assert list(Item.objects.values_list("name", flat=True)) == ["LiteBeam"]
    assert not ItemIdentifier.objects.filter(value=pasted).exists()
    assert not UnresolvedItemString.objects.exists()


@pytest.mark.parametrize(
    "length",
    [
        # One past the identifier column, which is what refuses it.
        LONGEST_VALUE + 1,
        # Past PostgreSQL's btree key limit as well, so the review list could
        # not hold it either -- the reason one bound covers both tables.
        3000,
    ],
)
def test_an_item_string_too_long_to_be_an_identifier_is_refused_and_nothing_is_written(length: int) -> None:
    """It is still readable in the staged rows, and the run finishes and says
    so rather than rolling back the catalogue around it.
    """
    pasted = "p" * length

    minted = _identifiers.mint(sheet_of([submission(item=pasted), submission(row=3)], catalogue=("LiteBeam",)))

    assert (minted.strings_too_long, minted.unresolved) == (1, 0)
    assert not UnresolvedItemString.objects.exists()
    assert not ItemIdentifier.objects.filter(value=pasted).exists()
    # The rest of the run stands, which is the point of refusing rather than
    # letting the column raise.
    assert identifiers() == [("LiteBeam", ItemIdentifier.Kind.ALIAS, "LiteBeam")]


def test_an_identifier_already_naming_another_item_is_counted_and_left_alone(category: Category) -> None:
    """Somebody moved it deliberately -- the admin has a page for exactly this
    -- and an import that quietly moved it back would undo that on every run.
    The count is how the run says it happened rather than hiding it.
    """
    elsewhere = Item.objects.create(name="Omni DC", category=category)
    ItemIdentifier.objects.create(item=elsewhere, kind=ItemIdentifier.Kind.ALIAS, value="omnitik")

    minted = _identifiers.mint(sheet_of([submission(item="omnitik")], catalogue=("OmniTikPOE",)))

    assert minted.naming_another_item == 1
    assert ItemIdentifier.objects.get(value="omnitik").item == elsewhere


def test_the_command_reads_the_staged_rows_and_says_what_it_minted(tmp_path: Path) -> None:
    """No path argument: the workbook is `stage_sheet`'s business and nobody
    else's, so this runs against whatever the tables hold.
    """
    path = build(
        tmp_path,
        [
            (None, "ada@example.net", "Ada", workbook.CHECKING_OUT, "tp link", 1.0, ""),
            (None, "ada@example.net", "Ada", workbook.CHECKING_OUT, "mast", 1.0, ""),
        ],
        catalogue=("Tp-Link", "LiteBeam"),
    )
    _staging.stage(workbook.read(path))
    out = io.StringIO()

    call_command("mint_items", stdout=out)

    printed = [" ".join(line.split()) for line in out.getvalue().splitlines()]
    assert printed == [
        "Catalogue",
        "catalogued items 2",
        "this run added 2",
        "duplicate rows in the catalogue tab 0",
        "catalogue rows too long to be a name 0",
        "identifiers 3",
        "this run added 3",
        "the two folds disagreed 0",
        "already naming another item 0",
        "strings naming no catalogued item 1",
        "with no reason written 0",
        "strings too long to be an identifier 0",
    ]
