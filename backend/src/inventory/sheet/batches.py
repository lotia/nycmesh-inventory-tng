"""Rule 5: where a burst of submissions starts and stops.

The old form carried one item per submission, so a single trip to the shelf
appears as a run of rows a few seconds apart. The new model records that trip
as one `StockTransaction` with many movements, so the importer has to decide
which rows were one trip. At runtime the question does not arise -- the cart
is one transaction already -- which is why this is migration-only.

Two things had to be settled, and
[§5 of the brief](../../../../docs/briefs/sheet-classifiers.md#5-submissions-to-batch)
carries every figure behind both. The reasoning is here and the figures are
there because a figure has one home, and `profile_sheet` is what produces it.

## The window: chaining, not a fixed window

A batch continues while the next submission is within ten minutes of the
**previous** one, rather than within ten minutes of the **first**. The two
disagree about the largest batch by a third, so the choice is not cosmetic.

Anchoring cuts a trip on the clock rather than on a pause: somebody working
steadily through a dozen items crosses the anchor's boundary while still
standing at the shelf, and the rows after it become a second transaction that
nothing in the ledger separates from the first. Chaining cuts only where the
person actually stopped, which is the thing being recovered.

The objection to chaining is that it could run away -- a slow but unbroken
afternoon chains into one enormous batch. In this export it does not: no batch
under this rule spans as much as an hour, and the section below prints the
largest so that the next export cannot quietly change that behind the rule.

## The submitter key: the name, not the email

Nearly half the submissions carry no email, and that is what settles it.
Keying on the email puts every one of them under one empty key, so the rule
chains submissions by people who have nothing to do with each other and calls
the result a trip to the shelf. It is not a small effect: the share of
submissions the email key reports inside a batch nearly halves once those rows
are made to stand alone instead, which means most of what that key was
measuring was the collapse rather than anybody's trip.

The name is missing from a small enough number of rows that the same treatment
costs almost nothing, and it is the key rule 6 has to work in anyway --
`inventory-tng-5r2` builds volunteers out of name spellings, because the
emails cannot reach the people who never typed one.

Email-with-a-name-fallback is the worst of the three rather than a compromise.
A volunteer who typed their email on one visit and not on the next gets two
keys, so it invents more submitters than there are spellings of a name, and it
splits real trips on whether somebody filled a field in.

Case is folded, as it is in rules 1 and 3, and for the same reason: case has
never distinguished two of anything in this ledger.

## What is left out, and why it is a batch of one

A submission the rule cannot attribute or cannot place in time is a batch of
its own. `Submission.at` is `None` where a row was typed by hand rather than
submitted through the form, and `workbook.py` asks any rule that sorts on it
to say what it does with those; a row with no name is the same problem seen
from the other side. Neither can honestly be chained to anything, and the two
mistakes are not symmetrical -- importing a trip as several transactions loses
the grouping, while merging strangers' rows into one transaction invents a
trip that nobody made and attributes stock to the wrong person. So the
unattributable row stands alone, and the report counts it rather than letting
it disappear into the singletons.
"""

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from inventory.sheet import Report
from inventory.sheet.people import addressed, spelled
from inventory.sheet.workbook import Sheet, Submission

# Ten minutes, per decision 0008 and the census in the brief.
WINDOW = timedelta(minutes=10)

# Who submitted a row, or None where the rule cannot say. None is not a
# submitter: it is the answer "nobody", and a row that gets it stands alone
# rather than joining every other row that got it.
type Submitter = Callable[[Submission], str | None]

# An entry sorted within one submitter: the timestamp, the sheet row that
# breaks a tie between two submissions written in the same second, and the
# submission itself.
type _Entry = tuple[datetime, int, Submission]


def by_name(submission: Submission) -> str | None:
    """The rule's key: the name, case folded, where it is a name at all.

    `people.spelled` decides that, rather than a second opinion here. Six
    entries in this export answer the name field with something that is not
    one -- `5.0`, `testing`, `update inventory`, an address -- and taking them
    at face value makes each a submitter, which chains rows by unrelated
    people into one trip. Rule 6 already refuses them; a batching key that did
    not would disagree with the volunteer the same row imports as.
    """
    return spelled(submission.name) or None


def by_email(submission: Submission) -> str | None:
    """Keying on the email instead, with the emailless rows standing alone."""
    return addressed(submission.email) or None


def by_email_or_name(submission: Submission) -> str | None:
    """Keying on the email and falling back to the name where there is none."""
    return addressed(submission.email) or spelled(submission.name) or None


def by_email_or_nobody(submission: Submission) -> str:
    """The email key as it was first read, chaining every emailless row as one person.

    Never None, which is the whole of what is wrong with it: the empty string
    is a key like any other here, so the rows carrying no email become one
    prolific submitter. Kept because the figure it produces is the one the
    brief used to quote, and a reading argued against in prose that no code
    produces is the failure this package exists to stop.
    """
    return addressed(submission.email)


def _split(timed: list[_Entry], window: timedelta, chained: bool) -> list[list[_Entry]]:
    """One submitter's submissions, in order, cut into batches.

    The two readings differ in one expression: chaining measures the gap from
    the last submission of the batch so far, anchoring from its first. Written
    once so that the reading being argued against cannot drift away from the
    one being argued for.
    """
    found = [[timed[0]]]
    for entry in timed[1:]:
        current = found[-1]
        since = current[-1] if chained else current[0]
        if entry[0] - since[0] <= window:
            current.append(entry)
        else:
            found.append([entry])
    return found


def batches(
    submissions: Sequence[Submission],
    *,
    submitter: Submitter = by_name,
    window: timedelta = WINDOW,
    chained: bool = True,
) -> list[list[Submission]]:
    """These submissions grouped into the trips to the shelf they record.

    Called with no keywords this is the rule, and one batch is one
    `StockTransaction`. The keywords are the readings the docstring above
    argues against, so that `section` can print what each of them costs
    without a second implementation of the grouping to disagree with this one.

    Batches come back ordered by the sheet row they open with, so two runs
    over the same submissions produce the same list.
    """
    # The unattributable and the untimed go straight in as batches of one; the
    # rest are collected per submitter and cut up below.
    found: list[list[Submission]] = []
    together: dict[str, list[_Entry]] = {}
    for submission in submissions:
        key = submitter(submission)
        if submission.at is None or key is None:
            found.append([submission])
        else:
            together.setdefault(key, []).append((submission.at, submission.row, submission))
    for timed in together.values():
        # Sorted on the pair rather than the whole entry, because a Submission
        # is not comparable and a tie would raise rather than fall through.
        timed.sort(key=lambda entry: entry[:2])
        found += [[submission for _, _, submission in batch] for batch in _split(timed, window, chained)]
    return sorted(found, key=lambda batch: batch[0].row)


def _inside(found: list[list[Submission]]) -> int:
    """Submissions in a batch of more than one -- the share the brief quotes."""
    return sum(len(batch) for batch in found if len(batch) > 1)


def _largest(found: list[list[Submission]]) -> int:
    """The biggest batch, or nothing where there are no submissions at all."""
    return max((len(batch) for batch in found), default=0)


def _keys(submitter: Submitter, submissions: Sequence[Submission]) -> int:
    """How many submitters this reading finds. Nobody is not one of them."""
    return len({submitter(one) for one in submissions} - {None})


def _longest(found: list[list[Submission]]) -> int:
    """Whole minutes from end to end of the batch that took longest.

    The answer to the one real objection to chaining: an unbroken run of short
    gaps could in principle swallow an afternoon, and this is what says
    whether it did. A batch of one spans nothing, and the timestamps are
    checked rather than assumed because the batches standing alone are exactly
    the ones that may have none.
    """
    spans = [
        batch[-1].at - batch[0].at
        for batch in found
        if len(batch) > 1 and batch[0].at is not None and batch[-1].at is not None
    ]
    return int(max(spans, default=timedelta()).total_seconds() // 60)


def section(sheet: Sheet) -> Report:
    """The rule, and what each reading it rejects would have said instead.

    Three alternatives are priced rather than described. The anchored window
    is the other half of the first question; the two email keys are the other
    half of the second, and both of those are printed because the difference
    between them is the argument -- the same key reads very differently
    depending on whether the rows carrying no email are one person or nobody.

    Each rejected reading names itself on every line it owns rather than
    saying "its", so that no two labels in the section are the same string.
    The indent already says which reading a line belongs to, but a report read
    back as a mapping would lose three of these to the last one wearing the
    same name.
    """
    submissions = sheet.submissions
    found = batches(submissions)
    inside = _inside(found)
    # The same predicate `batches` applies, asked of the population, so the
    # partition below cannot disagree with the grouping above it.
    unattributable = sum(1 for s in submissions if s.at is None or by_name(s) is None)
    anchored = batches(submissions, chained=False)
    email = batches(submissions, submitter=by_email)
    lumped = batches(submissions, submitter=by_email_or_nobody)
    fallback = batches(submissions, submitter=by_email_or_name)
    return "Batches", [
        ("submissions", len(submissions)),
        ("  inside a batch of more than one", inside),
        ("  alone in a batch of their own", len(submissions) - inside - unattributable),
        ("  naming nobody, or no time, alone by that rule", unattributable),
        ("batches", len(found)),
        ("largest batch", _largest(found)),
        ("longest batch, in minutes", _longest(found)),
        ("submitters the rule can name", _keys(by_name, submissions)),
        ("anchoring a fixed window instead, batches", len(anchored)),
        ("  the anchored reading, inside a batch", _inside(anchored)),
        ("  the anchored reading, largest batch", _largest(anchored)),
        # The reason the email key is not the one taken, stated as the number
        # it turns on rather than left to the two lines beneath it.
        ("submissions carrying no email", sum(1 for s in submissions if by_email(s) is None)),
        ("keying on the email instead, submitters", _keys(by_email, submissions)),
        ("  the email key, inside a batch", _inside(email)),
        ("  the email key, largest batch", _largest(email)),
        ("  the email key, chaining the emailless as one", _inside(lumped)),
        # Three spaces: this is the largest batch of the *lumped* reading on
        # the line above it, not a second figure about the email key.
        ("   that reading's largest batch", _largest(lumped)),
        ("keying on the email with a name fallback, keys", _keys(by_email_or_name, submissions)),
        ("  the fallback key, inside a batch", _inside(fallback)),
        ("  the fallback key, largest batch", _largest(fallback)),
    ]
