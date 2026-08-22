"""Rule 5: where a burst of submissions starts and stops.

The old form carried one item per submission, so a single trip to the shelf
appears as a run of rows a few seconds apart. The new model records that trip
as one `StockTransaction` with many movements, so the importer has to decide
which rows were one trip. At runtime the question does not arise -- the cart
is one transaction already -- which is why this is migration-only.

**A batch is a run of submissions by one submitter, each within ten minutes of
the one before it, keyed on the volunteer's case-folded name. A submission
with no name, or no timestamp, is a batch of one.**

Two things in that had to be settled -- the window, and the submitter key --
and both arguments are
[§5 of the brief](../../../../docs/briefs/sheet-classifiers.md#5-submissions-to-batch)
along with the figures that decide them. `section` below prints every rejected
reading as well as the chosen one, for the reason `Report` gives.

Three things a reader of this module needs that the brief does not carry:

- The gap is measured from the **previous** submission, not the first, so the
  window slides. `WINDOW` is the gap.
- `Submission.at` is `None` where a row was typed by hand rather than
  submitted through the form, and `workbook.py` asks any rule that sorts on it
  to say what it does with those. This one puts them in a batch alone, which
  is what it also does with a row naming nobody: losing a grouping is
  recoverable, and inventing a trip out of two strangers is not.
- The submitter key here is the folded name, and rule 6 builds volunteers out
  of the same spellings. A merge rule that changes what counts as one name
  moves the figures below.
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
    prolific submitter. Kept, and printed, because the figure it produces is
    the one the brief used to quote -- see the enumeration rule on `Report`.
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
