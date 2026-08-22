"""Tests for the ledger the staged rows become.

The six rules are tested in their own modules and what the first three import
steps do with them in theirs; what is claimed here is what a staged row becomes
once it reaches the ledger, and what becomes of one that cannot.

Sheets are built with ``sheets.sheet_of`` and put through the two steps this
one depends on, because a movement needs an item and a transaction needs an
actor. The one test that goes through the command stages a sheet first, since
the command's whole point is that it reads the staged tables.

**Every test about a row the step will not post asserts that nothing was
written**, and not only that the row was counted. The ledger cannot be
rewritten, so "counted and skipped" and "counted and posted anyway" are the
difference the whole step exists for, and a refusal test that stops at the
counter cannot tell them apart -- which is how a run that minted a placeholder
and posted an irreversible movement against it stayed green.
"""

import io
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.utils import timezone

from inventory.management.commands import _identifiers, _ledger, _people, _staging
from inventory.management.commands._ledger import PLACEHOLDER, UNPLACED, NotReady, Unpostable
from inventory.models import Item, Location, StockMovement, StockTransaction, Volunteer
from inventory.sheet.workbook import CHECKING_IN, Sheet
from inventory.tests.reports import depths_are_allowed, shares_of
from inventory.tests.sheets import AT, sheet_of, submission

pytestmark = pytest.mark.django_db


def imported(sheet: Sheet) -> _ledger.Posted:
    """The whole import, in the order the steps have to run in."""
    _identifiers.mint(sheet)
    _people.mint(sheet)
    return _ledger.post(sheet)


def movements() -> list[tuple[str, str, str | None, str | None]]:
    """Every movement, as the four things a reader of the ledger asks about one."""
    return [
        (
            str(one.quantity),
            one.item.name,
            one.from_location.name if one.from_location else None,
            one.to_location.name if one.to_location else None,
        )
        for one in StockMovement.objects.select_related("item", "from_location", "to_location").order_by("pk")
    ]


def nothing_was_written() -> bool:
    """Neither a ledger row nor either of the two rows posting one would make.

    Asserted by every refusal test, because the module docstring's claim is
    about what the database holds afterwards and not about a counter.
    """
    return not (
        StockTransaction.objects.exists()
        or StockMovement.objects.exists()
        or Item.objects.filter(name=PLACEHOLDER).exists()
        or Location.objects.exists()
    )


def test_a_batch_becomes_one_transaction_carrying_a_movement_for_each_row() -> None:
    """The whole reason the old form's rows have to be grouped at all: one trip
    to the shelf was one act, and the sheet could only record it a line at a
    time.
    """
    posted = imported(
        sheet_of(
            [
                submission(row=2, item="LiteBeam", quantity=2.0),
                submission(row=3, at=AT + timedelta(minutes=1), item="LiteBeam", quantity=3.0),
            ]
        )
    )

    assert (posted.transactions, posted.movements) == (1, 2)
    written = StockTransaction.objects.get()
    assert (written.kind, written.actor.display_name) == (StockTransaction.Kind.CHECKOUT, "Ada")
    assert StockMovement.objects.filter(transaction=written).count() == 2


def test_a_batch_holding_two_kinds_becomes_one_transaction_for_each() -> None:
    """A transaction has one kind and the database holds its movements to that
    kind's shape, so putting something back and taking something else in the
    same minute cannot be one row.
    """
    posted = imported(
        sheet_of(
            [
                submission(row=2, item="LiteBeam"),
                submission(row=3, direction=CHECKING_IN, item="LiteBeam"),
            ]
        )
    )

    assert (posted.batches, posted.mixed, posted.transactions) == (1, 1, 2)
    assert sorted(StockTransaction.objects.values_list("kind", flat=True)) == ["checkin", "checkout"]


def test_a_batch_whose_second_kind_is_all_refused_is_not_counted_as_holding_two() -> None:
    """The figure is about the transactions the run formed, so a batch that
    became one of them is not printed as a batch that became two.
    """
    posted = imported(
        sheet_of(
            [
                submission(row=2, quantity=None),
                submission(row=3, at=AT + timedelta(minutes=1), direction=CHECKING_IN),
            ]
        )
    )

    assert (posted.batches, posted.mixed, posted.transactions) == (1, 0, 1)


def test_a_correction_is_posted_as_an_adjustment_and_says_so() -> None:
    """Rule 2's answer, and the reason attribution reporting can mean anything:
    a faked check-out is not a volunteer taking hardware away.
    """
    posted = imported(sheet_of([submission(note="fixing inventory")]))

    written = StockTransaction.objects.get()
    assert (posted.corrections, written.kind) == (1, StockTransaction.Kind.ADJUSTMENT)
    assert written.reason == _ledger.CORRECTION


def test_stock_leaves_one_place_on_the_way_out_and_arrives_there_on_the_way_back() -> None:
    """A check-out leaves for nowhere the sheet could name -- `_ledger` says
    why that is the honest shape rather than a gap.
    `_ledger.py` argues why that is the honest reading until o5t has met.
    """
    imported(
        sheet_of(
            [
                submission(row=2, item="LiteBeam", quantity=2.0),
                submission(row=3, at=AT + timedelta(hours=1), direction=CHECKING_IN, item="LiteBeam", quantity=1.0),
            ]
        )
    )

    assert movements() == [("2.000", "LiteBeam", UNPLACED, None), ("1.000", "LiteBeam", None, UNPLACED)]


def test_the_one_place_the_import_makes_claims_to_be_nobody_and_nowhere() -> None:
    """A warehouse with no holder: a custody location would answer decision
    0008's open question, and any other kind would name a room nobody has
    agreed on.
    """
    imported(sheet_of([submission()]))

    unplaced = Location.objects.get()
    assert (unplaced.name, unplaced.kind, unplaced.held_by, unplaced.active) == (
        UNPLACED,
        Location.Kind.WAREHOUSE,
        None,
        True,
    )


def test_a_later_run_finds_that_place_again_after_somebody_renames_it() -> None:
    """Nothing stops o5t renaming the row, and a lookup by name would mint a
    second one beside it and split the balance in two.
    """
    imported(sheet_of([submission(row=2, item="LiteBeam")]))
    Location.objects.update(name="Broome Street shelf")

    _ledger.post(sheet_of([submission(row=3, at=AT + timedelta(hours=1), item="LiteBeam")]))

    assert list(Location.objects.values_list("name", flat=True)) == ["Broome Street shelf"]
    assert StockMovement.objects.count() == 2


def test_a_run_into_that_place_once_it_is_retired_posts_nothing() -> None:
    """Retiring it is the plan once o5t has emptied it, and stock cannot arrive
    at a retired location -- so the run stops before it writes rather than at
    whichever check-in reaches the trigger first.
    """
    imported(sheet_of([submission(row=2, item="LiteBeam")]))
    Location.objects.update(active=False)
    later = sheet_of([submission(row=3, at=AT + timedelta(hours=1), direction=CHECKING_IN, item="LiteBeam")])

    with pytest.raises(NotReady, match="retired"):
        _ledger.post(later)

    assert StockMovement.objects.count() == 1


def test_a_note_naming_a_place_still_makes_no_location() -> None:
    """Rule 3's answers are candidates for a meeting that has not happened, so
    the note is imported verbatim and the rule can be asked again later.
    """
    posted = imported(sheet_of([submission(note="mesh room 131 broome")]))

    assert posted.naming_a_place == 1
    assert list(Location.objects.values_list("name", flat=True)) == [UNPLACED]
    assert "mesh room 131 broome" in StockTransaction.objects.get().note


def test_a_quantity_is_posted_as_the_sheet_wrote_it() -> None:
    """No multiplier, because which of these meant a packet is not in the
    export and a ledger row cannot be corrected once it is wrong.
    """
    imported(sheet_of([submission(item="LiteBeam", quantity=100.0)]))

    assert StockMovement.objects.get().quantity == Decimal("100")


def test_every_item_a_run_posts_against_is_flagged_with_what_it_took_literally() -> None:
    """The flag is the handover: an administrator reading the item list sees
    the quantities the sheet held for it and decides what a packet is.
    """
    posted = imported(
        sheet_of(
            [
                submission(row=2, item="LiteBeam", quantity=1.0),
                submission(row=3, at=AT + timedelta(hours=1), item="LiteBeam", quantity=100.0),
                submission(row=4, at=AT + timedelta(hours=2), item="LiteBeam", quantity=100.0),
            ]
        )
    )

    assert posted.items_flagged == 1
    assert "100 x2, 1 x1" in Item.objects.get(name="LiteBeam").sheet_flag


def test_a_flag_says_how_many_quantities_it_did_not_list() -> None:
    """A flag long enough that nobody reaches the end of it is one nobody
    acts on, so it says how many it left out. `_ledger` has the example.
    """
    imported(
        sheet_of(
            [
                submission(row=number, at=AT + timedelta(hours=number), item="LiteBeam", quantity=float(number))
                for number in range(2, 12)
            ]
        )
    )

    assert (
        "2 x1, 3 x1, 4 x1, 5 x1, 6 x1, 7 x1, and 4 further quantities." in Item.objects.get(name="LiteBeam").sheet_flag
    )


def test_a_flag_counts_the_whole_export_and_not_the_rows_one_run_added() -> None:
    """The sentence claims to list every quantity the sheet holds for the item,
    so a refreshed export must not replace the census with its own new rows.
    """
    imported(sheet_of([submission(row=2, item="LiteBeam", quantity=1.0)]))
    grown = sheet_of(
        [
            submission(row=2, item="LiteBeam", quantity=1.0),
            submission(row=3, at=AT + timedelta(hours=1), item="LiteBeam", quantity=4.0),
        ]
    )

    posted = _ledger.post(grown)

    assert posted.items_flagged == 1
    assert "face value: 1 x1, 4 x1." in Item.objects.get(name="LiteBeam").sheet_flag


def test_an_item_the_export_never_moved_is_flagged_with_nothing() -> None:
    """There is nothing to decide about an item no historical row touched."""
    imported(sheet_of([submission(item="LiteBeam")], catalogue=("LiteBeam", "Omni DC")))

    assert Item.objects.get(name="Omni DC").sheet_flag == ""


def test_a_second_run_leaves_a_flag_an_administrator_has_cleared_alone() -> None:
    """Which is what writing the flag as part of posting buys: a run that posts
    nothing flags nothing.
    """
    sheet = sheet_of([submission()])
    imported(sheet)
    settled = Item.objects.get(name="LiteBeam")
    settled.sheet_flag = ""
    settled.save()

    posted = _ledger.post(sheet)

    assert posted.items_flagged == 0
    assert Item.objects.get(name="LiteBeam").sheet_flag == ""


@pytest.mark.parametrize("quantity", [0.0, -1.0, None])
def test_a_row_carrying_no_quantity_above_zero_is_not_posted(quantity: float | None) -> None:
    """`stock_movement_quantity_positive` says a movement of none of something
    is not a movement, and sixteen rows of the export are like that.
    """
    posted = imported(sheet_of([submission(quantity=quantity)]))

    assert posted.refused[Unpostable.QUANTITY] == 1
    assert nothing_was_written()


@pytest.mark.parametrize("quantity", [9175551234.0, 0.0004, 0.0005])
def test_a_row_carrying_a_quantity_the_column_cannot_hold_is_not_posted(quantity: float) -> None:
    """A phone number typed into the quantity cell overflows `numeric(12, 3)`,
    a figure below half a thousandth rounds to nothing, and the one between
    them is rounded up and stored -- a number nobody wrote, in a table nobody
    can correct.
    """
    posted = imported(sheet_of([submission(quantity=quantity)]))

    assert posted.refused[Unpostable.UNSTORABLE] == 1
    assert nothing_was_written()


def test_a_row_carrying_no_timestamp_is_not_posted() -> None:
    """`occurred_at` cannot be empty and cannot be corrected, so the only
    timestamp available is one this import would have made up.
    """
    posted = imported(sheet_of([submission(at=None)]))

    assert (posted.refused[Unpostable.UNTIMED], posted.posted) == (1, 0)
    assert nothing_was_written()


def test_a_row_dated_after_the_run_is_not_posted() -> None:
    """One mistyped year would otherwise roll back every other transaction the
    run had formed, on a trigger whose message names no sheet row.
    """
    posted = imported(sheet_of([submission(at=AT + timedelta(days=365))]))

    assert posted.refused[Unpostable.AHEAD] == 1
    assert nothing_was_written()


def test_a_row_reaching_nobody_is_not_posted() -> None:
    """Settled before this step: there is no actor a trigger would accept, and
    `_people.py` says why a stand-in is not one.
    """
    posted = imported(sheet_of([submission(name="testing", email="")]))

    assert posted.refused[Unpostable.NOBODY] == 1
    assert nothing_was_written()


def test_nothing_is_made_by_a_run_with_nothing_to_post() -> None:
    """Not the location and not the placeholder: a row somebody would have to
    explain is worse than an empty ledger.
    """
    imported(sheet_of([submission(at=None)]))

    assert not Location.objects.exists()
    assert not Item.objects.filter(name=PLACEHOLDER).exists()


def test_a_row_whose_volunteer_has_no_row_yet_is_not_posted() -> None:
    """Running this before `import_volunteers` posts nothing rather than
    failing, which is what the steps before it do about the same mistake.
    """
    sheet = sheet_of([submission()])
    _identifiers.mint(sheet)

    posted = _ledger.post(sheet)

    assert posted.refused[Unpostable.NO_VOLUNTEER] == 1
    assert nothing_was_written()


def test_a_row_returning_an_item_since_retired_is_not_posted() -> None:
    """Stock may leave a retired item and may not arrive under one, so the row
    coming back is refused and the one going out still posts.
    """
    sheet = sheet_of(
        [
            submission(row=2, direction=CHECKING_IN, item="LiteBeam"),
            submission(row=3, at=AT + timedelta(hours=1), item="LiteBeam", quantity=2.0),
        ]
    )
    _identifiers.mint(sheet)
    _people.mint(sheet)
    Item.objects.filter(name="LiteBeam").update(active=False)

    posted = _ledger.post(sheet)

    assert (posted.refused[Unpostable.RETIRED], posted.posted) == (1, 1)
    assert movements() == [("2.000", "LiteBeam", UNPLACED, None)]


def test_a_run_before_the_catalogue_step_posts_nothing_at_all() -> None:
    """The dangerous half of running the steps out of order: the rows naming a
    real item are refused, and the rows naming nothing resolvable are not, so
    posting would put the unknowable ones into a ledger on their own.
    """
    sheet = sheet_of(
        [
            submission(row=2, item="LiteBeam"),
            submission(row=3, at=AT + timedelta(hours=1), item="mast"),
        ]
    )
    _people.mint(sheet)

    with pytest.raises(NotReady, match="mint_items"):
        _ledger.post(sheet)

    assert nothing_was_written()


def test_a_catalogue_spelling_one_name_twice_still_posts_against_the_item_minted() -> None:
    """Both steps have to fold the tab the same way. `items.resolve` takes an
    exact match before it folds, so a tab holding both spellings answers this
    step with the one the catalogue step minted nothing for -- and the run
    stops, claiming a step that has just run has not.
    """
    sheet = sheet_of([submission(item="Litebeam")], catalogue=("LiteBeam", "Litebeam"))

    posted = imported(sheet)

    assert (posted.posted, posted.placeholder) == (1, 0)
    assert StockMovement.objects.get().item.name == "LiteBeam"


def test_a_string_naming_no_catalogued_item_is_posted_against_the_placeholder() -> None:
    """An unimportable row is not a dropped row. What the placeholder is, and
    why answering the string later does not move the movement, is on the row.
    """
    posted = imported(sheet_of([submission(item="mast")]))

    assert posted.placeholder == 1
    assert StockMovement.objects.get().item.name == PLACEHOLDER
    assert "review list" in Item.objects.get(name=PLACEHOLDER).description


def test_a_row_naming_no_item_at_all_is_posted_against_the_placeholder() -> None:
    """Something moved and what it was is not recoverable. The count and the
    reason are `_identifiers`'; posting it against the placeholder is here.
    """
    posted = imported(sheet_of([submission(item="")]))

    assert posted.placeholder == 1
    assert StockMovement.objects.get().item.name == PLACEHOLDER


def test_no_placeholder_is_made_where_every_string_reaches_an_item() -> None:
    imported(sheet_of([submission(item="LiteBeam")]))

    assert not Item.objects.filter(name=PLACEHOLDER).exists()


def test_a_naive_timestamp_is_read_as_the_zone_the_project_declares() -> None:
    """The export's timestamps say nothing about their zone; `_ledger.py` says
    why this is the one they are read in.
    """
    imported(sheet_of([submission()]))

    assert StockTransaction.objects.get().occurred_at == timezone.make_aware(AT)


def test_a_transaction_opens_at_the_earliest_of_its_rows() -> None:
    imported(
        sheet_of(
            [
                submission(row=2, at=AT + timedelta(minutes=5)),
                submission(row=3, at=AT),
            ]
        )
    )

    assert StockTransaction.objects.get().occurred_at == timezone.make_aware(AT)


def test_a_transaction_reads_its_rows_in_row_order_and_not_in_timestamp_order() -> None:
    """Rule 5 hands a batch over in timestamp order, and a tab that has been
    re-sorted or typed into by hand is enough for the two to differ. The row
    order is the one a person looking at the spreadsheet can see.
    """
    posted = imported(
        sheet_of(
            [
                submission(row=810, at=AT + timedelta(minutes=5), note="delivered to nn498"),
                submission(row=811, at=AT, note="NN217"),
            ]
        )
    )

    written = StockTransaction.objects.get()
    assert (posted.several_jobs, written.job_reference) == (1, "NN498")
    assert written.note.startswith("Imported from the sheet, rows 810, 811.")


def test_the_job_a_note_names_is_on_the_transaction() -> None:
    posted = imported(sheet_of([submission(note="delivered to nn217")]))

    assert posted.with_job == 1
    assert StockTransaction.objects.get().job_reference == "NN217"


def test_a_transaction_whose_rows_name_two_jobs_takes_the_first_and_counts_it() -> None:
    """The field holds one string, so the second is counted rather than lost in
    silence.
    """
    posted = imported(
        sheet_of(
            [
                submission(row=2, note="NN217"),
                submission(row=3, at=AT + timedelta(minutes=1), note="NN498"),
            ]
        )
    )

    assert (posted.with_job, posted.several_jobs) == (1, 1)
    assert StockTransaction.objects.get().job_reference == "NN217"


def test_a_transaction_says_which_staged_rows_it_came_from() -> None:
    """There is nowhere else on a ledger row to record provenance, and the
    staged row is where such a question ends up, as `_ledger` explains.
    """
    imported(
        sheet_of(
            [
                submission(row=2, note="mesh room"),
                submission(row=3, at=AT + timedelta(minutes=1), note=""),
            ]
        )
    )

    assert StockTransaction.objects.get().note == "Imported from the sheet, rows 2, 3.\nrow 2: mesh room"


def test_the_actor_is_whoever_a_merge_points_at() -> None:
    """A merge leaves the ledger alone and readers follow it forward, and the
    actor trigger refuses the duplicate outright.
    """
    sheet = sheet_of([submission(row=2, name="Ada", email=""), submission(row=3, name="Grace", email="")])
    _identifiers.mint(sheet)
    _people.mint(sheet)
    duplicate, survivor = Volunteer.objects.get(sheet_key="ada"), Volunteer.objects.get(sheet_key="grace")
    duplicate.merged_into = survivor
    duplicate.save()

    _ledger.post(sheet)

    assert set(StockTransaction.objects.values_list("actor__display_name", flat=True)) == {"Grace"}


def test_a_merge_that_has_come_round_in_a_circle_is_read_rather_than_walked_forever() -> None:
    """Built in memory, because the trigger that refuses merging into a merged
    record is exactly what stops a cycle being staged through the database --
    and the walk keeps its visited set for the case where that reading is
    wrong, as `views._survivor_of` does.
    """
    one, other = Volunteer(id=1, display_name="Ada"), Volunteer(id=2, display_name="Grace")
    one.merged_into, other.merged_into = other, one
    known = _ledger._Known(volunteers={"ada": one}, by_id={1: one, 2: other}, items={})

    assert known.actor("ada") is None


def test_a_volunteer_who_has_been_retired_is_not_posted_against() -> None:
    """The pick-list no longer offers them, and `stock_transaction_actor_
    selectable` refuses the row rather than recording work against somebody
    withdrawn.
    """
    sheet = sheet_of([submission()])
    _identifiers.mint(sheet)
    _people.mint(sheet)
    Volunteer.objects.update(active=False)

    posted = _ledger.post(sheet)

    assert posted.refused[Unpostable.NO_VOLUNTEER] == 1
    assert not StockTransaction.objects.exists()


def test_a_second_run_posts_nothing_and_does_not_fail() -> None:
    """The ledger is append-only, so a second run has to add nothing rather
    than raise: there would be no way to take back what it wrote.
    """
    sheet = sheet_of(
        [
            submission(row=2, item="LiteBeam"),
            submission(row=3, at=AT + timedelta(hours=1), item="mast"),
        ]
    )
    imported(sheet)
    before = movements()

    again = _ledger.post(sheet)

    assert (again.transactions_added, again.movements_added) == (0, 0)
    assert (again.transactions, again.movements) == (2, 2)
    assert movements() == before


def test_a_re_export_that_has_renumbered_every_row_posts_nothing_again() -> None:
    """A key names an act and not a cell. Sheets renumbers every row below a
    deleted one, so keying on the row would post the whole trip a second time
    with no rule changed and no row lost.
    """
    first = sheet_of(
        [
            submission(row=3, item="LiteBeam"),
            submission(row=4, at=AT + timedelta(minutes=1), item="LiteBeam"),
            submission(row=5, at=AT + timedelta(hours=1), name="Grace", email="", item="LiteBeam"),
        ]
    )
    imported(first)
    before = movements()
    renumbered = sheet_of(
        [
            submission(row=2, item="LiteBeam"),
            submission(row=3, at=AT + timedelta(minutes=1), item="LiteBeam"),
            submission(row=4, at=AT + timedelta(hours=1), name="Grace", email="", item="LiteBeam"),
        ]
    )

    again = _ledger.post(renumbered)

    assert (again.transactions_added, again.movements_added) == (0, 0)
    assert movements() == before


def test_two_batches_one_key_cannot_tell_apart_become_the_one_transaction_it_names() -> None:
    """Rule 5 puts a row whose name field is not a name in a batch of its own,
    so one volunteer's two such rows in the same second are two batches and one
    act. Posting them as the transaction the key names is what keeps the second
    of them from being skipped by a later run's lookup.
    """
    sheet = sheet_of(
        [
            submission(row=2, at=AT - timedelta(hours=1), item="LiteBeam"),
            submission(row=3, name="testing", item="LiteBeam", quantity=1.0),
            submission(row=4, name="testing", item="LiteBeam", quantity=2.0),
        ]
    )

    posted = imported(sheet)

    assert (posted.batches, posted.transactions, posted.movements) == (3, 2, 3)
    together = StockTransaction.objects.get(occurred_at=timezone.make_aware(AT))
    assert StockMovement.objects.filter(transaction=together).count() == 2


def test_the_section_keeps_the_depths_and_the_shares_the_contract_allows() -> None:
    """A label two past its parent is a share that sums to it, and one past is
    a subset of the line above. Both runs, because a first run into an empty
    database makes what it added equal to what is there and hides the
    difference.
    """
    sheet = sheet_of(
        [
            submission(row=2, item="LiteBeam"),
            submission(row=3, at=AT + timedelta(hours=1), item="mast", quantity=0.0),
        ]
    )

    for counted in (_ledger.section(imported(sheet))[1], _ledger.section(_ledger.post(sheet))[1]):
        depths_are_allowed(counted)
        for label, count in counted:
            if shares := shares_of(counted, label):
                assert count == sum(share for _, share in shares), label


def test_the_command_reads_the_staged_rows_and_says_what_it_posted() -> None:
    """Through the staging tables, which is how the whole import is run."""
    sheet = sheet_of(
        [
            submission(row=2, item="LiteBeam", note="fixing inventory nn217"),
            submission(row=3, at=AT + timedelta(hours=1), item="mast", quantity=0.0),
        ]
    )
    _staging.stage(sheet)
    call_command("mint_items", stdout=io.StringIO())
    call_command("import_volunteers", stdout=io.StringIO())
    out = io.StringIO()

    call_command("post_ledger", stdout=out)

    printed = [" ".join(line.split()) for line in out.getvalue().splitlines()]
    assert printed == [
        "Ledger",
        "submissions 2",
        "reaching a movement 1",
        "of those, against the placeholder item 0",
        "naming nobody 0",
        "naming a volunteer with no row 0",
        "carrying no timestamp 0",
        "carrying a timestamp that has not happened 0",
        "carrying no quantity above zero 1",
        "carrying a quantity the ledger cannot hold 0",
        "naming an item with no row 0",
        "returning an item since retired 0",
        "transactions 1",
        "posted by this run 1",
        "movements 1",
        "posted by this run 1",
        "batches rule 5 found 2",
        "becoming more than one transaction 0",
        "transactions recording a correction 1",
        "transactions naming a job 1",
        "whose rows named more than one 0",
        "submissions naming a candidate location 0",
        "items flagged for their quantities 1",
    ]
