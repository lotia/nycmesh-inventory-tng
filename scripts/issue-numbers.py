#!/usr/bin/env python3
"""What became of every bead the export named, once the push had run.

Two questions, and they are one question asked of one file, which is why they
are answered here together rather than half here and half in shell:

  * DID IT GET AN ISSUE AT ALL? `bd github sync` warns and carries on when a
    reference will not go, so its exit status is not evidence that every id in
    the batch was filed. Only the tracker afterwards can say.
  * AND IF IT IS CLOSED HERE, WHICH ISSUE HAS TO BE CLOSED THERE? `bd` files
    every issue open -- its create call sends a title, a body and labels and no
    state at all, so a bead that finished months ago arrives looking like live
    work. See
    docs/decisions/0031-the-issue-list-is-a-window-on-the-tracker.md point 5.

The second question needs a number and the first does not, which is what
tempted them apart. But a bead with no reference is the answer to both, and
splitting them meant the open ones fell out of this reader's view and had to be
recovered by intersecting two sets in bash -- at the point in the run where
nothing can be undone, in the one language there with no test coverage of its
own.

NUMBERS ON STDOUT, FINDINGS ON STDERR, in report.sh's `fail`/`note` vocabulary
so the caller can `dispatch` them rather than re-deciding what each line means.
A gap is a `fail`: the run must not print an all-clear over a bead it was asked
to file and did not.

WHY THIS MAY READ A REFERENCE WHERE `unsynced.py` REFUSES TO. That reader
matches an issue GitHub offered against the refs a tracker holds, and parsing
there was a real bug -- issue numbers are unique only within one repository, so
a pattern accepted another repository's issue as ours. Nothing is being matched
here: the bead is ours, `bd` wrote the ref onto it moments ago, and the only
question is which number that ref names. Addressing something is not
identifying it. The shape itself is `unsynced.number_of`'s to know, not this
file's -- two opinions about that is exactly the split this reader was widened
to end.

Usage:
    bd export | issue-numbers.py <file of id<TAB>status>
"""

import sys
from pathlib import Path

from unsynced import number_of, ref_of, rows


def named(listing: Path) -> dict[str, str]:
    """Every bead the run worked from, and the status it has in the tracker.

    All of them, not only the closed ones. Narrowing here is what hid an open
    bead the push had passed over: it is in no closing pass, so nothing else
    would ever have mentioned it.
    """
    out = {}
    for line in listing.read_text().splitlines():
        if not line.strip():
            continue
        identifier, _, status = line.partition("\t")
        if identifier.strip():
            out[identifier.strip()] = status.strip() or "open"
    return out


def report(export: str, listing: Path, wanted: dict[str, str]) -> tuple[list[str], list[str]]:
    """`(numbers to close, lines for the caller to dispatch)`.

    Only issues for beads THIS RUN filed are offered for closing. Every closed
    bead whose issue is open is arguably out of step, but one of them may be
    open because a person reopened it, and an export is not the place that
    decides such a thing -- it closes what it filed and leaves the rest to the
    ordinary reconciliation.
    """
    numbers, findings, seen = [], [], set()
    for number, row in rows(export):
        identifier = row.get("id")
        if identifier not in wanted:
            continue
        seen.add(identifier)
        ref = ref_of(row, listing, number)
        if ref is None:
            findings.append(f"fail {identifier} was passed over and still has no issue")
            continue
        if wanted[identifier] != "closed":
            continue
        issue = number_of(ref)
        if issue is None:
            findings.append(f"fail {identifier} is closed, and {ref} names no issue to close")
        else:
            numbers.append(issue)

    for missing in sorted(wanted.keys() - seen):
        findings.append(f"fail {missing} was named for filing and is not in the tracker at all")

    if not findings:
        findings.append(f"note All {len(wanted)} named have an issue.")
    return numbers, findings


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: bd export | {Path(sys.argv[0]).name} <file of id<TAB>status>")
    listing = Path(sys.argv[1])
    if not listing.exists():
        raise SystemExit(f"{listing} does not exist, so nothing here knows what was filed")

    numbers, findings = report(sys.stdin.read(), listing, named(listing))
    for line in findings:
        print(line, file=sys.stderr)
    for issue in numbers:
        print(issue)
    return 0


if __name__ == "__main__":
    sys.exit(main())
