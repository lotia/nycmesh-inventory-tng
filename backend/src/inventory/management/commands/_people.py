"""Turning the export's people into `Volunteer` rows, without merging any of them.

Rule 6 in `inventory/sheet/people.py` answers who the workbook's people are and
which of its spellings it is unsure about. This is what does something with that
answer: every volunteer the rule names gets a row, and every doubt it has gets
written onto the row it is about. Underscored, and here rather than beside the
model, for the reason `_staging.py` gives about itself -- the rule reads
spreadsheets and must stay out of the application's boot.

## The import mints, and never merges

`merged_into` is an administrator's to set. Merging removes a row and leaves the
ledger pointing where it always pointed, so it can be done at any time; a merge
this made would already be written into every ledger row imported against it,
and nothing can take that back. So the row count this produces is the top of the
range and an administrator can only walk it down. Both ends, and why the rule
errs generously, are
[§6 of the brief](../../../../docs/briefs/sheet-classifiers.md#6-person-to-volunteer).

## A doubt is a field, not a log line

`Volunteer.sheet_flag` carries it, so the question arrives where the answer is
given: an administrator reading the volunteer list sees which rows the export
could not tell apart, filters to them, merges or clears each one, and needs no
copy of the import's output and no copy of the workbook. A printed report would
be gone by the time anybody has the standing to act on it, and the count this
command prints is a check on the brief rather than the record.

`Volunteer.sheet_key` is the other half: it is what the rule knew the volunteer
by, so a second run finds the row rather than minting beside it, and the ledger
import (`inventory-tng-24q.4`) joins a submission's person to a row through it.

## What a re-run may write onto a row that is already here

A name it may not: a re-run cannot tell a spelling it chose itself from one an
administrator has corrected, and the correction is the better of the two by
definition.

A flag it may, onto a row carrying none. The rule's doubts change when the
export does -- a refreshed export adding one near-miss spelling raises a doubt
about the volunteer it is near, who already has a row -- and a doubt that stops
at the report is a doubt in the one place this module argues above that it must
not be. An empty field cannot be told from one an administrator emptied, so
this does re-ask a question somebody has already answered "no" to; that costs
them emptying it again, against a duplicate nobody is ever told about, and
those are not the same size. What it never does is overwrite or clear a flag
that is there, or flag a row an administrator has settled the way that removes
it from the list -- `Volunteer.is_selectable` is that test, so a merged or
retired row is left alone.

Which makes the printed count the rows that carry a flag rather than the doubts
the rule raised, so the number and the administrator's filter cannot disagree.

## A name too long for the column is shortened, never refused

Every volunteer the rule names gets a row -- the ledger import joins to it --
so `display_name` is truncated to what the column takes rather than the row
being refused. Nothing is lost: `sheet_key` is unbounded and holds the whole
spelling, and the staged rows hold every cell it came from. The count says how
many rows were shortened.

## The seven that reach nobody

Seven submissions name nobody and carry no address that names anybody, so there
is no volunteer to mint for them and none to attribute them to. Nothing is
minted, and the ledger import (`inventory-tng-24q.4`) does not post them.

A stand-in row to hang them on is what
[data-model.md](../../../../docs/data-model.md#migrating-the-existing-sheet)
once proposed, and the `stock_transaction_actor_selectable` trigger refuses
it -- that record now says why. The short of it: making one would put a way to
move stock anonymously into the pick-list, to record seven rows nobody can
attribute anyway.

So they stay in `StagedSubmissionRow`, readable exactly as they arrived, and an
administrator who can say whose they were records the movement themselves. The
count is printed on every run rather than left to be noticed, which is what
keeps this from being a silent drop.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass

from django.db import transaction
from django.utils.text import Truncator

from inventory.models import Volunteer
from inventory.sheet import Report, people
from inventory.sheet.people import Directory
from inventory.sheet.workbook import Sheet

# What `Volunteer.display_name` takes, for the reason the module docstring
# gives about shortening rather than refusing. Written out rather than read off
# `_meta` for the reason `_identifiers.LONGEST_NAME` gives, and asserted
# against the column by `test_import_volunteers` for the same one.
LONGEST_NAME = 100


@dataclass(frozen=True)
class Minted:
    """What one run of `mint` did, for the command to print."""

    volunteers: int
    created: int
    already: int
    by_address: int
    # Rows carrying a flag once this run is done, and the ones this run wrote.
    # Rows rather than doubts, so the number and the filter an administrator
    # reads it off cannot disagree -- the module docstring argues it.
    flagged: int
    flagged_now: int
    addressed: int
    # Volunteers whose address is already on some other row, so it was left
    # off theirs rather than written and refused by the unique constraint.
    address_held: int
    # Rows whose display name would not fit the column and was shortened.
    shortened: int
    nobody: int


def section(minted: Minted) -> Report:
    """Who the export names, and how much of it an administrator is being handed.

    Beside the counts rather than in a command, which `_staging.section`
    argues.

    The split between minted and already here is what a second run is read on,
    and the flags are the line an administrator is being handed work by, so
    both are printed rather than a single total.
    """
    return "Volunteers", [
        ("volunteers the export names", minted.volunteers),
        ("  minted by this run", minted.created),
        ("  already here", minted.already),
        ("  known only by an address", minted.by_address),
        ("  flagged for an administrator", minted.flagged),
        ("   of those, flagged by this run", minted.flagged_now),
        ("  given an address of their own", minted.addressed),
        ("  whose address is already another row's", minted.address_held),
        ("  shown by a shortened name", minted.shortened),
        ("submissions reaching nobody", minted.nobody),
    ]


def _addresses(sheet: Sheet, who: Directory) -> dict[str, Counter[str]]:
    """Each volunteer mapped to the addresses they wrote, and how often."""
    written: dict[str, Counter[str]] = defaultdict(Counter)
    for one in sheet.submissions:
        person = who.volunteer(one)
        if person.key and (address := people.addressed(one.email)):
            written[person.key][address] += 1
    return written


def _email(who: Directory, written: Counter[str]) -> str:
    """The one address that is this volunteer's own, or "" where none is.

    The column holds one address and is unique, and the export breaks both
    ends of that: a volunteer may have written several, and one address may
    have been written by several volunteers. Neither may be settled by
    whichever row the loop reaches first, which is what a unique constraint
    would otherwise do on the operator's behalf.

    So an address written beside more than one volunteer is nobody's here --
    `Directory.shared` is which ones those are -- and where one volunteer wrote
    several, the busiest is the one an administrator would pick out as theirs.
    Nothing is lost either way: every address anybody wrote is still in the
    staged rows.

    A value with no `@` in it is not an address, which is the test
    `people.spelled` applies from the other side. It reaches this column only
    from a hand-typed row, and it would make the volunteer unsavable in the
    admin without ever having been anybody's email.

    Both ends are answered from the workbook, which is all `Directory` knows.
    Whether the address is already on a row this import did not write is a
    question about the table, and `mint` settles it.
    """
    mine = [address for address in written if "@" in address and address not in who.shared]
    return people.busiest(mine, written) if mine else ""


def _flag(shown: dict[str, str], might_be: tuple[str, ...]) -> str:
    """What to tell an administrator about a volunteer the rule is unsure of."""
    if not might_be:
        return (
            "Known only by an address: no name was ever written beside it. "
            "Give this volunteer a name, or merge it into whoever it turns out to be."
        )
    # Named as the administrator sees them in the list rather than by the
    # folded key, so the flag can be acted on without knowing there is a key.
    return (
        f"Possibly the same person as {', '.join(shown[other] for other in might_be)}. "
        "Merge this row into them if so; empty this field if not."
    )


@transaction.atomic
def mint(sheet: Sheet) -> Minted:
    """Give every volunteer the export names a row, and report what that came to.

    In one transaction so that a run interrupted halfway leaves no half a
    directory, which is a state the next run would read as "these were already
    imported" and never finish.
    """
    who = people.directory(sheet)
    named = set(who.by_name.values())
    written = _addresses(sheet, who)
    # Every key's display name, including the addresses that are their own
    # volunteer, so a flag can name a candidate the way the list shows it.
    shown = {key: who.spellings.get(key, key) for key in who.volunteers}

    # Which address is on which row already. The column is unique where it is
    # present, so an address held by anybody else is one this import may not
    # write -- including onto a volunteer who registered themselves, and onto
    # the row a changed spelling has just made a second of. Read once and kept
    # in step below, rather than a query per volunteer.
    holders = dict(Volunteer.objects.exclude(email=None).values_list("email", "sheet_key"))

    created = 0
    addressed = 0
    flagged = 0
    flagged_now = 0
    address_held = 0
    shortened = 0
    for key in sorted(who.volunteers):
        email = _email(who, written[key])
        if email and holders.get(email, key) != key:
            address_held += 1
            email = ""
        # Truncator counts the ellipsis it adds inside the budget, so what
        # comes out fits the column and says that it was cut.
        name = Truncator(shown[key]).chars(LONGEST_NAME)
        shortened += name != shown[key]
        flag = _flag(shown, who.flagged[key]) if key in who.flagged else ""
        volunteer, minted = Volunteer.objects.get_or_create(
            sheet_key=key,
            defaults={"display_name": name, "email": email, "sheet_flag": flag},
        )
        wrote_flag = bool(minted and flag)
        if not minted and flag and not volunteer.sheet_flag and volunteer.is_selectable:
            volunteer.sheet_flag = flag
            volunteer.save(update_fields=["sheet_flag"])
            wrote_flag = True
        if volunteer.email:
            holders[volunteer.email] = key
        created += minted
        # Read off the row rather than off what the rule worked out, so that
        # an address or a flag this run declined to write is counted as the
        # row has it and not as the rule wanted it.
        addressed += bool(volunteer.email)
        flagged += bool(volunteer.sheet_flag)
        flagged_now += wrote_flag
    return Minted(
        volunteers=len(who.volunteers),
        created=created,
        already=len(who.volunteers) - created,
        by_address=len(who.volunteers - named),
        flagged=flagged,
        flagged_now=flagged_now,
        addressed=addressed,
        address_held=address_held,
        shortened=shortened,
        nobody=sum(1 for one in sheet.submissions if who.volunteer(one).how == people.How.NOBODY),
    )
