"""Turning the staged rows into the transactions and movements they describe.

The last step of the import and the only one that writes to the ledger. The
leading underscore, and being here rather than beside the models, are for the
reason `_staging.py` gives about itself.

What the six rules each contribute, and what a trip becomes, is
[data-model.md](../../../../docs/data-model.md#migrating-the-existing-sheet).
Rule 3 is the one whose answer is deliberately not acted on, and the reason is
below rather than there, because it is a decision this module made.

One run is one database transaction. The ledger is append-only, so a run that
stopped halfway could not be tidied up afterwards: the rows it had managed to
write would stand for good and the next run would post the rest beside them.

A movement's quantity is the figure the sheet holds and is never scaled; what
that leaves an administrator to settle, and the flag that asks them, are
`_quantities.py`.

## A trip is one act, so a mixed trip is more than one transaction

A `StockTransaction` carries one `kind`, and `stock_movement_matches_kind`
holds every movement under it to the shape that kind calls for. A batch whose
rows are not all the same kind therefore cannot be one transaction -- a
volunteer putting two things back and taking a third in the same minute is two
acts in one trip -- so such a batch becomes one transaction per kind. The rows
are all kept and the only thing lost is the claim that they happened together.
The run prints how many batches that was, counted over the transactions the
run actually formed: a batch whose second kind is carried only by rows this
step refused became one transaction, and saying otherwise would make the block
disagree with the ledger beneath it.

Rule 5 hands a batch over in timestamp order, because that is the order it
groups in. Everything below reads a group in **row** order instead -- the note
it writes, the job it takes, the row a reader is sent to. A submissions tab
that has been re-sorted, or that carries a row somebody typed in by hand, is
enough for the two to differ, and of the two orders only one is the one a
person looking at the spreadsheet can see.

## The zone every timestamp is read in

`StagedSubmissionRow.at` is text because the export's timestamps are naive and
nothing in the workbook says what they are naive *about*. `occurred_at` is not,
so a zone has to be chosen, and the choice is the project's own `TIME_ZONE` --
taken from the setting rather than written out again here, so the two cannot
come to disagree. The rows were typed by volunteers standing in New York into a
sheet belonging to the same people, so the wall clock a row carries is a New
York wall clock. Reading it as UTC instead would move four years of evening
work onto the following day.

Two hours a year are not one instant in that zone: a reading inside the hour
the clocks go back could be either of two, and one inside the hour they go
forward was never on any clock. `make_aware` settles both, and no better answer
exists for a figure a volunteer typed to the nearest minute.

## Every reason a row is not posted is one of a list, and the list is printed

`Unpostable` is that list and `_line` is where it is applied. The rule the
members answer to is that **anything the ledger's own writers would object to
is screened here first**. An objection that gets past this arrives as a
constraint or a trigger firing inside one atomic run, which rolls back every
other transaction and movement the run had formed and hands the operator a
Postgres error naming a row number in a table rather than a row in the sheet.
One mistyped year in one cell of three thousand is enough.

So a row with no timestamp is refused, and so is one whose timestamp has not
happened yet. `occurred_at` is not nullable, the ledger is ordered by it, and
it can never be edited, so a row with no time could only be given one this
import made up; today's date says a trip from 2022 happened this morning and
puts it at the top of the recent-activity page. A row dated ahead of the run is
the same fault written the other way, and `stock_transaction_occurred_at_not_
in_the_future` refuses it outright.

A quantity of zero or less is no movement at all --
`stock_movement_quantity_positive` says so -- and neither is one the column
cannot hold. `StockMovement.quantity` is `numeric(12, 3)`, so a phone number
typed into the quantity cell overflows it, and a figure below half a
thousandth is rounded to nothing and then refused by that same constraint.
Between those two lies the quieter one: a quantity with a fourth decimal place
is rounded *up* into the ledger, silently, and a stored figure nobody wrote is
worse than a refused row that is counted. All three are one member.

An item that has been retired is refused for the rows coming back only.
`inventory_to_location_is_offered` in migration 0010 lets stock leave a retired
item and refuses it arriving, and exempts an adjustment from both, so the
screen here is exactly that shape rather than "not active".

A row naming nobody is refused for the reason `_people.py` settles, and one
naming a volunteer with no row is refused because there is no actor a trigger
would accept. Both stay staged and are counted on every run.

## Two states this refuses to run into at all

A row this step cannot post is counted and left where it is. A *database* it
cannot post into is different in kind, and the difference is the same one the
whole module turns on: a state in which this would post part of the export is
worse than one in which it posts none of it, because the part is not
retractable. Both of these stop the run before a row is written and say what to
do about it.

The first is the catalogue step not having run over these staged rows,
recognised by a row whose string the rule resolved to a catalogued name that
has no `Item` behind it. That state is dangerous rather than merely empty: the
rows whose string resolves to *nothing* do not fail that test, so they would be
posted against the placeholder while every row with a real answer was refused,
and nothing in the printed report would look wrong. `import_sheet` says the
order of the four steps is not negotiable, but a command run by name never
reads that, and this is the one step nobody can undo.

The second is the provisional location below having been withdrawn, which the
same trigger refuses stock arriving at. A run holding no check-in would still
succeed, so leaving it to the database means an import that fails or not
depending on which rows this export happens to hold.

## One provisional location, and nothing that claims to be a place

Rule 3 reads places out of the notes field, and every one of them is a
candidate for `inventory-tng-o5t` to take to the people who use the room rather
than a `Location`. Nothing here mints one. But a movement has to touch
somewhere, because `stock_movement_has_a_side` refuses one that touches
nothing, so the import makes a single location that is deliberately not a
place: `UNPLACED` below. A warehouse, because the six kinds offer no "not yet
known" and each of the other five asserts something nobody has established -- a
room, a shelf, a vehicle, a hub, or a volunteer.

Stock leaves it for `NULL` on the way out and arrives from `NULL` on the way
in. The far side is outside the system, which is all the old sheet ever
recorded, so the balance this produces is the figure that sheet computed, item
by item, less the rows the run says it could not post. That is the only claim
the import is in a position to make, and it is worth more than a guessed
geography.

o5t is expected to empty that row and rename or retire it, and a later run has
to find the same one rather than mint a second beside it. So it is looked up by
what it has already been used for -- the place the sheet's own transactions
have moved stock through -- and only by name where no run has used one yet.

The reading this must *not* take is a custody location per volunteer, `held_by`
naming them as `location_held_by_iff_custody` requires. Whether volunteers are
to be asked to track custody at all is the open question in
[decision 0008](../../../../docs/decisions/0008-stock-ledger-transfer-graph.md#open-question-for-stakeholders),
and minting seventy of those rows would answer it before the meeting.

Nothing is lost by waiting: every note is imported verbatim onto its
transaction, so rule 3 runs again over the ledger once o5t has settled the
list, and moving stock out of `UNPLACED` into the places it settles on is an
ordinary transfer. The run prints how many submissions name a candidate, so how
much work that is can be seen now.

## What makes a second run add nothing

`StockTransaction.idempotency_key` -- the column a phone's retry uses, meaning
the same thing here: this act is recorded, so do not record it twice. It beats
a table of our own listing what has been posted because the ledger is the only
honest authority on what the ledger holds, where a table beside it can claim a
row was posted when nothing was.

**A key names an act, and not a cell.** It is `sheet:` and a digest of the
three things that say which act this is: who made it, of which kind, and the
time the earliest of its rows carries. Not the row it opens with, which is
what this first used and which is a coordinate rather than an identity --
Sheets renumbers every row below a deleted one, so a re-export of a tab
somebody has tidied re-keys every batch under the deletion, and the ledger
would take a second copy of four years of work with no rule changed and no row
lost. A digest survives a deletion, an insertion, and a re-sort, and it is not
a string an API client could guess and take.

What a key deliberately does not survive is a different *reading* of the sheet.
A run under a changed rule 5 or rule 6 finds different acts, and one under a
changed rule 2 finds a different kind for the same rows; either posts beside
what is there rather than instead of it. That is a second reading of history
rather than a re-run, and the place for one is an empty database.

A corrected *cell* is the other way round, and deliberately: the key holds, so
a tidied note or an amended quantity adds nothing rather than posting the trip
again. That is the right way round for a table nobody can edit, and the run
prints what it added, so a correction not applied reads as a zero instead of
hiding as a duplicate.

Two acts collide only if one person made both, of one kind, with the earliest
row of each written in the same second -- which rule 5 already reads as one
trip unless it can put no name to either. Where that happens they are posted as
the one transaction the key says they are, so nothing is dropped for it.

The unique index on the column is a second guard rather than the guarantee.
It is scoped to the actor, so two runs at once agree about the actor and the
later insert is refused; a run either side of a merge does not agree, because
the walk in `_Known.actor` follows the merge forward. The lookup covers that
half: it ignores the actor, so a transaction posted before a merge is still
recognised after one.
"""

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import blake2s

from django.core.management.base import CommandError
from django.db import models, transaction
from django.utils import timezone

from inventory.management.commands import _quantities
from inventory.management.commands._identifiers import folded, uncategorised
from inventory.models import Item, Location, StockMovement, StockTransaction, Volunteer
from inventory.sheet import Report, batches, corrections, each, items, jobs, locations, people
from inventory.sheet.workbook import Sheet, Submission

# The one location the import makes, named so that nobody mistakes it for a
# survey of where anything actually is. The module docstring argues it.
UNPLACED = "Unplaced inventory (sheet import)"

# The item every row whose string reaches nothing is posted against. Not an
# `ItemIdentifier` target and never given one: `UnresolvedItemString` says why
# no string may be made to name a stand-in.
PLACEHOLDER = "Unidentified item (sheet import)"

# What that row is, on the field describing an item rather than on the one
# asking a question about it: this is not a task anybody can finish, and its
# `sheet_flag` carries the same quantity question every other imported item's
# does.
PLACEHOLDER_DESCRIPTION = (
    "Stands in for the item strings the import could make nothing of. Every movement under it "
    "came from a sheet row naming a string that reaches no catalogued item, or naming none at "
    "all, and the strings themselves are the review list kept beside the staged rows. Answering "
    "one of them does not move the movements already here, because the ledger cannot be "
    "rewritten: put the stock right with a count instead."
)

# `StockTransaction.reason` is a hundred characters, so it names the practice
# and the note beneath it carries what the volunteer actually wrote.
CORRECTION = "The note says the sheet's own record was wrong"

# What every key this writes begins with, so the ledger can be asked which of
# its transactions came from the sheet. Named on the model because the API has
# to refuse a client the same prefix, and two spellings of it would be one of
# them quietly letting a client through.
FROM_THE_SHEET = StockTransaction.FROM_THE_SHEET

# Half of `StockMovement.quantity`'s `numeric(12, 3)` each: everything the
# column can hold is below the first and an exact multiple of the second. Named
# rather than tested for inline, because the module docstring argues all three
# ways of falling outside them at once.
QUANTITY_CEILING = Decimal(10) ** 9
QUANTITY_STEP = Decimal("0.001")


class NotReady(CommandError):
    """The database is not in a state this step may post into.

    A `CommandError` because every caller is a management command and what an
    operator wants is the sentence rather than a traceback. Raised only before
    anything has been written, which the module docstring explains is the only
    place it could be raised from.
    """


class Unpostable(StrEnum):
    """Why a submission reaches no movement.

    Enumerated, and every one of them printed on every run whether or not this
    export produced it, for the reason `inventory/sheet/__init__.py` gives
    about a report's labels: a line that appeared only when the data held it
    would make a figure true of one workbook.

    In the order `_line` tries them, which is the order they are printed in.
    """

    NOBODY = "naming nobody"
    NO_VOLUNTEER = "naming a volunteer with no row"
    UNTIMED = "carrying no timestamp"
    AHEAD = "carrying a timestamp that has not happened"
    QUANTITY = "carrying no quantity above zero"
    UNSTORABLE = "carrying a quantity the ledger cannot hold"
    # Never above zero in a report, because a run that found one stopped: the
    # module docstring says why that state is refused whole rather than in
    # part. The line is printed all the same, under the rule above.
    NO_ITEM = "naming an item with no row"
    RETIRED = "returning an item since retired"


@dataclass(frozen=True)
class Posted:
    """What one run of `post` did, for the command to print.

    Totals with this run's share beside them, for the reason `Minted` in
    `_identifiers.py` gives about its own pairs.
    """

    transactions: int
    transactions_added: int
    movements: int
    movements_added: int
    batches: int
    mixed: int
    submissions: int
    posted: int
    # What each member of `Unpostable` refused, as the tally was taken. The
    # block printed from it holds a line per member either way, because
    # `sheet.each` is what fills in the ones this export did not produce.
    refused: Counter[Unpostable]
    placeholder: int
    corrections: int
    with_job: int
    several_jobs: int
    naming_a_place: int
    items_flagged: int


def section(posted: Posted) -> Report:
    """Where every submission ended up, and what the ledger holds because of it.

    Beside the counts rather than in a command, which `_staging.section`
    argues.

    The submissions come first and their lines are a partition, so a reader can
    settle in one block that every row of the export either reached a movement
    or is named with the reason it did not. The rows posted against the
    placeholder are a subset of the ones that reached a movement rather than a
    further way of failing, and the indent is where that is said.

    Three other lines are subsets for the same reason and are indented as ones:
    what this run added is counted inside the ledger's totals and not beside
    them, and a batch that became more than one transaction is one of the
    batches above it. Only the submissions are shares that sum.
    """
    return "Ledger", [
        ("submissions", posted.submissions),
        ("  reaching a movement", posted.posted),
        ("   of those, against the placeholder item", posted.placeholder),
        *each(Unpostable, posted.refused),
        ("transactions", posted.transactions),
        (" posted by this run", posted.transactions_added),
        ("movements", posted.movements),
        (" posted by this run", posted.movements_added),
        ("batches rule 5 found", posted.batches),
        (" becoming more than one transaction", posted.mixed),
        ("transactions recording a correction", posted.corrections),
        ("transactions naming a job", posted.with_job),
        (" whose rows named more than one", posted.several_jobs),
        ("submissions naming a candidate location", posted.naming_a_place),
        ("items flagged for their quantities", posted.items_flagged),
    ]


@dataclass(frozen=True)
class _Line:
    """One staged row, read down to everything a movement needs from it.

    `catalogued` is the name rule 1 answered with, and is empty where the rule
    answered with nothing -- which is the placeholder's row. The item itself is
    not looked up here, so that a run finding no such row never mints a
    placeholder for it to point at.

    `who` is the export's own key for the person, from rule 6 rather than from
    the volunteer it reached: the key an act is identified by must not move
    when two records are merged, and `actor` does.
    """

    submission: Submission
    who: str
    actor: Volunteer
    kind: str
    quantity: Decimal
    at: datetime
    catalogued: str


@dataclass(frozen=True)
class _Act:
    """One transaction the sheet describes, and the rows that go under it.

    Held in row order, which is the order everything written off it reads in.
    Every row shares the act's kind, its submitter and therefore its actor, so
    the first row speaks for all three.
    """

    key: str
    lines: tuple[_Line, ...]
    job: str
    several_jobs: bool

    @property
    def kind(self) -> str:
        return self.lines[0].kind

    @property
    def actor(self) -> Volunteer:
        return self.lines[0].actor

    @property
    def at(self) -> datetime:
        return min(one.at for one in self.lines)


@dataclass(frozen=True)
class _Known:
    """What the database already holds that a staged row is resolved against."""

    volunteers: Mapping[str, Volunteer]
    by_id: Mapping[int, Volunteer]
    items: Mapping[str, Item]

    def actor(self, key: str) -> Volunteer | None:
        """The volunteer this row may be recorded against, or None where none is.

        A merge is followed forward to whoever survived it, because that is
        what a merge means to a reader and because `stock_transaction_actor_
        selectable` refuses the duplicate outright. The same walk as
        `_survivor_of` in `views.py`, and the visited set is kept for the
        reason stated there -- more so here, where a cycle would spin inside an
        open transaction rather than inside one request.
        """
        volunteer = self.volunteers.get(key)
        if volunteer is None:
            return None
        seen = {volunteer.pk}
        while volunteer.merged_into_id is not None and volunteer.merged_into_id not in seen:  # ty: ignore[unresolved-attribute]
            volunteer = self.by_id[volunteer.merged_into_id]  # ty: ignore[unresolved-attribute]
            seen.add(volunteer.pk)
        return volunteer if volunteer.is_selectable else None


def _known() -> _Known:
    volunteers = list(Volunteer.objects.all())
    return _Known(
        volunteers={one.sheet_key: one for one in volunteers if one.sheet_key},
        by_id={one.pk: one for one in volunteers},
        items={one.name: one for one in Item.objects.all()},
    )


def _kind(submission: Submission) -> str:
    """Which of the three kinds the import posts this row as."""
    if corrections.is_correction(submission.note):
        return StockTransaction.Kind.ADJUSTMENT
    return StockTransaction.Kind.CHECKOUT if submission.is_check_out else StockTransaction.Kind.CHECKIN


def _arrives(submission: Submission, kind: str) -> bool:
    """Whether this row's movement has a to-side, which is what retirement bites on.

    Migration 0010's rule is about stock arriving, and it exempts an adjustment
    from the question altogether. A check-out is pure departure, so neither a
    retired item nor a retired location is an obstacle to one.
    """
    return not submission.is_check_out and kind != StockTransaction.Kind.ADJUSTMENT


def _storable(quantity: float) -> Decimal | None:
    """This quantity as the column would hold it, or None where it would not.

    From the string rather than the float, so a decimal column is given the
    number the spreadsheet showed and not the nearest binary one. `quantize`
    is compared against rather than applied: a figure needing a fourth decimal
    place is one this cannot store, and storing the rounded one instead writes
    a number nobody typed.
    """
    value = Decimal(str(quantity))
    if value >= QUANTITY_CEILING or value != value.quantize(QUANTITY_STEP):
        return None
    return value


def _line(
    submission: Submission,
    person: people.Person,
    known: _Known,
    catalogued: str,
    now: datetime,
) -> _Line | Unpostable:
    """This row read, or the reason it reaches no movement.

    Every reason is a member of `Unpostable` and the module docstring says what
    decides which screens belong here. The order they are tried in is the order
    they are printed in, so a row failing two of them is counted under the
    first and the lines are a partition of the population rather than an
    overlapping tally.

    `now` is passed in rather than read here, so that three thousand rows are
    all judged against one instant.
    """
    if person.key is None:
        return Unpostable.NOBODY
    actor = known.actor(person.key)
    if actor is None:
        return Unpostable.NO_VOLUNTEER
    if submission.at is None:
        return Unpostable.UNTIMED
    at = timezone.make_aware(submission.at)
    if at > now:
        return Unpostable.AHEAD
    if submission.quantity is None or submission.quantity <= 0:
        return Unpostable.QUANTITY
    quantity = _storable(submission.quantity)
    if quantity is None:
        return Unpostable.UNSTORABLE
    if catalogued and catalogued not in known.items:
        return Unpostable.NO_ITEM
    kind = _kind(submission)
    if catalogued and not known.items[catalogued].active and _arrives(submission, kind):
        return Unpostable.RETIRED
    return _Line(
        submission=submission,
        who=person.key,
        actor=actor,
        kind=kind,
        quantity=quantity,
        at=at,
        catalogued=catalogued,
    )


def _note(lines: Sequence[_Line]) -> str:
    """What the transaction says about itself: where it came from, and the prose.

    The rows are named because the staged row is where anybody asking about
    this transaction has to end up, and there is nowhere else on the ledger to
    say so. The notes are kept per row rather than run together, because two
    rows of one trip can say different things and only one of them may name the
    job the transaction carries.
    """
    numbered = [one.submission.row for one in lines]
    listed = ", ".join(str(number) for number in numbered)
    written = [f"row {one.submission.row}: {one.submission.note}" for one in lines if one.submission.note]
    return "\n".join([f"Imported from the sheet, {'row' if len(numbered) == 1 else 'rows'} {listed}.", *written])


def _job(lines: Sequence[_Line]) -> tuple[str, bool]:
    """The job this transaction is against, and whether its rows named more than one.

    The first, in row order, which is what `jobs.job_reference` already does
    within one note. The field holds one string and the second is counted
    rather than dropped in silence.
    """
    found = [reference for one in lines if (reference := jobs.job_reference(one.submission.note))]
    return (found[0] if found else ""), len(set(found)) > 1


def _unplaced() -> Location:
    """The one location the import posts through.

    Found by what the sheet's own transactions have already moved through, and
    only made where no run has made one yet, for the reason the module
    docstring gives about o5t renaming it.
    """
    used = (
        Location.objects.filter(
            models.Q(movements_out__transaction__idempotency_key__startswith=FROM_THE_SHEET)
            | models.Q(movements_in__transaction__idempotency_key__startswith=FROM_THE_SHEET)
        )
        .distinct()
        .first()
    )
    return (
        used
        or Location.objects.get_or_create(
            name=UNPLACED,
            parent=None,
            defaults={"kind": Location.Kind.WAREHOUSE},
        )[0]
    )


def _withdrawn(location: Location) -> None:
    """Stop the run where the place it posts through has been retired.

    The second of the two states the module docstring refuses whole, and a
    step of its own beside `_unminted` rather than a thing finding the row
    happens to do: a guard nobody can see being called is a guard the next
    reader moves, and this one may not be asked before there is something to
    post -- a run holding nothing must leave no location behind.
    """
    if not location.active:
        raise NotReady(
            f"The location this import posts through, {location.name!r}, has been retired, so no "
            "stock can arrive there and nothing has been posted. Offer that location again to "
            "import the rest of the export into it, or import what is left into a fresh database."
        )


def _placeholder() -> Item:
    """The item the unresolvable rows are posted against."""
    item, _ = Item.objects.get_or_create(
        name=PLACEHOLDER,
        defaults={"category": uncategorised(), "description": PLACEHOLDER_DESCRIPTION},
    )
    return item


def _against(lines: Iterable[_Line], known: _Known) -> dict[int, Item]:
    """The item each row's movement is against, minting a placeholder if one is wanted.

    Minted here rather than up front so that an export every one of whose
    strings reaches a catalogued item leaves no stand-in behind for somebody to
    wonder about later. Every postable row is mapped and not only the ones this
    run adds, because the flag written below counts the export rather than the
    run -- and a row already posted against the placeholder is a row the
    placeholder already exists for.
    """
    placeholder: Item | None = None
    against: dict[int, Item] = {}
    for one in lines:
        if one.catalogued:
            against[one.submission.row] = known.items[one.catalogued]
        else:
            placeholder = placeholder or _placeholder()
            against[one.submission.row] = placeholder
    return against


def _grouped(batch: Sequence[Submission], lines: Mapping[int, _Line]) -> list[list[_Line]]:
    """This batch's postable rows, split into one group per kind.

    Rows in row order and groups in the order of the rows they open with, so
    that a run over the same submissions produces the same transactions
    whatever order rule 5 handed the batch over in.
    """
    together: dict[str, list[_Line]] = {}
    for submission in batch:
        if (line := lines.get(submission.row)) is not None:
            together.setdefault(line.kind, []).append(line)
    return sorted(
        (sorted(group, key=lambda one: one.submission.row) for group in together.values()),
        key=lambda group: group[0].submission.row,
    )


def _identity(lines: Sequence[_Line]) -> str:
    """What act these rows are, as a key that survives the sheet being renumbered.

    Who, of what kind, when: the module docstring argues that those three are
    the act, that a row number is not, and what follows from two groups
    answering them the same way.
    """
    said = "\n".join([lines[0].who, lines[0].kind, min(one.at for one in lines).isoformat()])
    return FROM_THE_SHEET + blake2s(said.encode(), digest_size=16).hexdigest()


def _acts(found: Sequence[Sequence[Submission]], lines: Mapping[int, _Line]) -> tuple[list[_Act], int]:
    """The transactions these batches describe, and how many became more than one.

    Two groups answering to one key are one act by that key's own definition,
    so they are gathered into one transaction rather than one of them being
    left unposted by a later run's lookup.
    """
    together: dict[str, list[_Line]] = {}
    split = 0
    for batch in found:
        groups = _grouped(batch, lines)
        split += len(groups) > 1
        for group in groups:
            together.setdefault(_identity(group), []).extend(group)
    acts = []
    for key, gathered in together.items():
        ordered = tuple(sorted(gathered, key=lambda one: one.submission.row))
        reference, several = _job(ordered)
        acts.append(_Act(key=key, lines=ordered, job=reference, several_jobs=several))
    return acts, split


def _unminted(refused: Counter[Unpostable]) -> None:
    """Stop the run where the catalogue step has not run over these staged rows.

    The module docstring says why this state is refused whole: the rows this
    would post are exactly the ones whose item is unknowable, and they are not
    retractable once posted.
    """
    missing = refused[Unpostable.NO_ITEM]
    if missing:
        raise NotReady(
            f"{missing} staged rows name a catalogued item that has no row of its own, so "
            "`manage.py mint_items` has not been run over these staged rows. Nothing has been "
            "posted: posting now would record the rows whose item string reaches nothing and "
            "refuse every row that names a real one, into a ledger that cannot be corrected. "
            "Run `manage.py mint_items` and post again."
        )


@transaction.atomic
def post(sheet: Sheet) -> Posted:
    """Post what the staged rows describe, and report what that came to.

    In one transaction for the reason the module docstring gives: a ledger
    written half way cannot be unwritten.
    """
    known = _known()
    who = people.directory(sheet)
    # The catalogue step's list and not the tab's, for the reason `folded`
    # gives: resolving against the tab answers with names it minted nothing
    # for, and this step reads that as its not having run at all.
    catalogue = folded(sheet.catalogue)
    resolutions = {string: items.resolve(string, catalogue) for string in {s.item for s in sheet.submissions}}
    now = timezone.now()

    lines: dict[int, _Line] = {}
    refused: Counter[Unpostable] = Counter()
    for submission in sheet.submissions:
        read = _line(submission, who.volunteer(submission), known, resolutions[submission.item].item or "", now)
        if isinstance(read, Unpostable):
            refused[read] += 1
        else:
            lines[submission.row] = read
    _unminted(refused)

    found = batches.batches(sheet.submissions)
    acts, split = _acts(found, lines)
    already = set(
        StockTransaction.objects.filter(idempotency_key__startswith=FROM_THE_SHEET).values_list(
            "idempotency_key", flat=True
        )
    )
    posting = [act for act in acts if act.key not in already]
    against = _against(lines.values(), known)
    written = _write(posting, against)
    flagged = _quantities.flag(
        ((against[one.submission.row], one.quantity) for one in lines.values()),
        (against[one.submission.row] for act in posting for one in act.lines),
    )

    return Posted(
        # Counted off the ledger rather than added up here, so that the figure
        # is the one the database holds even where a second writer got in.
        transactions=StockTransaction.objects.filter(idempotency_key__startswith=FROM_THE_SHEET).count(),
        transactions_added=len(posting),
        movements=StockMovement.objects.filter(transaction__idempotency_key__startswith=FROM_THE_SHEET).count(),
        movements_added=written,
        batches=len(found),
        mixed=split,
        submissions=len(sheet.submissions),
        posted=len(lines),
        refused=refused,
        placeholder=sum(1 for one in lines.values() if not one.catalogued),
        corrections=sum(1 for act in acts if act.kind == StockTransaction.Kind.ADJUSTMENT),
        with_job=sum(1 for act in acts if act.job),
        several_jobs=sum(1 for act in acts if act.several_jobs),
        naming_a_place=sum(1 for one in sheet.submissions if locations.locations(one.note)),
        items_flagged=flagged,
    )


def _write(acts: Sequence[_Act], against: Mapping[int, Item]) -> int:
    """Insert a transaction per act and a movement per line, and count the movements.

    `bulk_create` rather than a save each, which the catalogue steps could not
    use: neither ledger model keeps history, so there is no signal to miss, and
    an import writing five thousand rows one at a time is five thousand round
    trips. The database's own rules are untouched by it -- every trigger these
    rows meet fires on `INSERT`.
    """
    if not acts:
        return 0
    unplaced = _unplaced()
    _withdrawn(unplaced)
    posting = [
        StockTransaction(
            actor=act.actor,
            kind=act.kind,
            occurred_at=act.at,
            reason=CORRECTION if act.kind == StockTransaction.Kind.ADJUSTMENT else "",
            job_reference=act.job,
            note=_note(act.lines),
            idempotency_key=act.key,
        )
        for act in acts
    ]
    StockTransaction.objects.bulk_create(posting)
    movements = [
        StockMovement(
            transaction=written,
            item=against[one.submission.row],
            quantity=one.quantity,
            from_location=unplaced if one.submission.is_check_out else None,
            to_location=None if one.submission.is_check_out else unplaced,
        )
        for written, act in zip(posting, acts, strict=True)
        for one in act.lines
    ]
    StockMovement.objects.bulk_create(movements)
    return len(movements)
