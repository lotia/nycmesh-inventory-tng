"""Rule 2: whether a note is a correction rather than volunteer activity.

Volunteers fake check-ins and check-outs because the sheet gives them no other
way to say "the shelf disagrees with the record". Those rows import as
`adjustment`, and telling them from real movement is what makes attribution
reporting mean anything -- decision 0008's supporting decision 4 rests on it.

## Whole-note against substring

The brief long said substring matching double-counts, because `inventory
correction` contains `inventory correct` and each of those rows is swept up by
both predicates. That is true of *summing a count per phrase*, and false of
asking a row whether it matches any of them: a row is one row however many
predicates it satisfies. So the objection is to the arithmetic rather than to
substring matching.

Substring, evaluated per row, is therefore the reading, because whole-note
equality is the one that loses rows it has no reason to: `fixing inventory (2
today)` is the same act as `fixing inventory`, written by somebody who added a
detail. All three readings are in
[§2 of the brief](../../../../docs/briefs/sheet-classifiers.md#2-note-to-correction)
with the counts they give, and `section` below prints them, so the argument
can be checked rather than believed.

## The rule

A note is a correction when it names **the record** and **an act of adjusting
it**, and both halves are needed. Neither on its own is one:

- `fixing loose pole nn540` and `hex house fix` are repairs to hardware at a
  site, not to a number in a spreadsheet.
- `inventory order` is an order, and `apartment stock` is a place.

The two vocabularies below were read off the 700 distinct notes rather than
imagined; `invneottr` is in the record half because somebody typed it, and the
reading of a rule is what the ledger says rather than what it should have said.
Two entries are the exception and are there on purpose: `recount` appears
nowhere in this export and `stock` never appears beside an act, so neither
changes a figure today. They are the same act and the same object as the
entries beside them, and a rule the importer applies to rows written after
today is not improved by omitting the obvious sibling of a word already in it.
"""

import re

from inventory.sheet import Report
from inventory.sheet.workbook import Sheet

# The record being adjusted. `inven\w*` rather than the word, because
# `invenotry` and `inventry` are both in the ledger.
RECORD = re.compile(r"inven\w*|\binvneottr\b|\bcounts?\b|\bstock\b", re.IGNORECASE)

# The act of adjusting it.
ADJUSTMENT = re.compile(
    r"\bfix\w*|\bcorrect\w*|\bupdat\w*|\badjust\w*|\bseed\w*|\binitial\b|\brecount\w*", re.IGNORECASE
)

# What the brief enumerated before the rule was settled. Kept so that the
# report can show all three readings of them side by side, which is what makes
# the choice above checkable rather than a claim. Printing only two would
# leave the summed-per-phrase reading quoted by prose and produced by nothing,
# which is the failure this package exists to stop.
ENUMERATED = ("fixing inventory", "updating inventory", "inventory correction", "inventory correct")

# Notes naming the record while doing nothing to it, which is why the rule
# does not special-case a bare `inventory`. Enumerated rather than inferred:
# the point of the pair is that one reading is arguable and the other is not.
NOT_THE_PRACTICE = ("inventory order", "apartment stock")


def is_correction(note: str) -> bool:
    """Whether this note says the record was wrong rather than that stock moved."""
    return bool(RECORD.search(note) and ADJUSTMENT.search(note))


def section(sheet: Sheet) -> Report:
    """The partition, the near misses the rule declines, and all three
    readings of the enumerated phrases.

    The two half-matching lines are the ones to argue with. `inventory` alone
    on its own is plainly the same practice written lazily, and `inventory
    order` and `apartment stock` are plainly not, so the rule takes neither
    and says how many it left. Changing that is a decision somebody can now
    make against a number; the brief has them.

    The three readings are printed rather than described because the third is
    the one being argued against, and a figure quoted in prose that no code
    produces is what this package exists to stop.
    """
    notes = [s.note.lower() for s in sheet.submissions]
    record = [n for n in notes if RECORD.search(n)]
    adjustment = [n for n in notes if ADJUSTMENT.search(n)]
    corrections = [n for n in notes if is_correction(n)]
    only_record = [n for n in record if not ADJUSTMENT.search(n)]
    return "Corrections", [
        # The denominator, because the four lines under it are a partition of
        # every submission rather than of the ones carrying a note -- unlike
        # the locations section, which divides only those, and which nobody
        # should have to work that out by comparing two totals.
        ("submissions", len(notes)),
        ("  with no note at all", sum(1 for n in notes if not n)),
        ("  naming the record and an act of adjusting it", len(corrections)),
        ("  naming the record only", len(record) - len(corrections)),
        ("  naming an act only", len(adjustment) - len(corrections)),
        ("  naming neither, note or no note", len(notes) - len(record) - len(adjustment) + len(corrections)),
        # The two readings of "naming the record only" that the brief argues
        # over: the bare word, which is plainly this practice written lazily,
        # against the ones that are plainly a place or an order. Printed so
        # that changing the rule is a decision made against a number.
        # Over the rows the line above them counts, and by substring, which
        # is the reading this module settles on. Equality here would have made
        # the section argue one way and count the other.
        ("   of those, the bare word alone", sum(1 for n in only_record if n == "inventory")),
        ("   of those, an order or a place", sum(1 for n in only_record if any(p in n for p in NOT_THE_PRACTICE))),
        ("the four enumerated phrases, whole-note", sum(1 for n in notes if n in ENUMERATED)),
        ("  the same phrases, per row", sum(1 for n in notes if any(p in n for p in ENUMERATED))),
        ("  the same phrases, summed per phrase", sum(sum(1 for n in notes if p in n) for p in ENUMERATED)),
    ]
