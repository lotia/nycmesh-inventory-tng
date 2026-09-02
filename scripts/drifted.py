#!/usr/bin/env python3
"""Beads and issues that disagree about whether the work is finished.

The third question, and the one nothing asked. `unsynced.py` finds issues no
bead points at; `unexported.py` finds beads no issue was filed for. Both are
about work one side has never heard of. This is about work they BOTH know, and
describe differently.

WHY IT NEEDED ASKING. It happened after every one of the three batches that
built this machinery, and each time a person noticed by comparing two lists by
hand: `scripts/export-issues.sh` closes only what it filed in the same run --
deliberately, so that an export never overrules somebody who reopened an issue
(decision 0031 point 5) -- so a bead closed *afterwards* is nobody's job, and
`scripts/sync-issues.sh` only runs when somebody thinks to run it. What was left
on GitHub each time was a handful of open issues describing finished work, which
is precisely what the export's closing pass exists to prevent at filing time,
undone afterwards by ordinary use.

IT REPORTS AND DOES NOT FIX. That is a decision rather than a limitation, and
it is recorded in
docs/decisions/0031-the-issue-list-is-a-window-on-the-tracker.md -- the
paragraph on the standing signal, which says what the exception is and why a
schedule must not overrule it. So this names what disagrees and leaves the
settling to `scripts/sync-issues.sh` and a person.

WHAT IS NOT A DISAGREEMENT. A bead with no `external_ref` is unfiled, not
out of step -- `unexported.py` owns that. An issue no bead points at is
unpulled, and `unsynced.py` owns that. A `deferred` or `in_progress` bead is
open work by any reading, and GitHub has only two states, so the tracker's four
are read as closed-or-not and nothing else: `deferred` against an open issue is
agreement, not drift.

AND NEITHER IS ANOTHER REPOSITORY'S ISSUE, which is why the repository has to
be named on the command line rather than carried in the listing. This iterates
every row of the export, so it is the first reader for which "the ref is ours"
is not true by construction -- and it compared the number it read out of one
against a listing fetched with `--repo`, which is exactly the bug `unsynced.py`
had once and says so in its own header. `unsynced.issue_of` is the pairing that
makes reading a number out of a ref safe. inventory-tng-cwpa.12.

BOTH DIRECTIONS COUNT, once the two sides are read that way. Closed here
against open on GitHub is the one that keeps happening -- see above -- but open
here against closed on GitHub is the same disagreement seen from the other end,
and each is named with which way round it runs.

HOW BIG A PAGE TO ASK FOR IS NOT WRITTEN HERE, and the example below says
`$ISSUE_LIMIT` rather than a number for that reason: this reader has no way to
tell a listing that fits from one `gh` stopped part way through, and a number
copied out of an example goes stale the day the real one is raised. Both the
number and the refusal are scripts/repository.sh's -- `listing_cut_short`.

Usage:
    gh issue list --state all --limit "$ISSUE_LIMIT" \\
        --json number,state --jq '.[] | "\\(.number)\\t\\(.state)"' |
        drifted.py .beads/issues.jsonl lotia/nycmesh-inventory-tng
"""

import sys
from pathlib import Path

from unsynced import issue_of, offered, ref_of, rows


def states(text: str) -> dict[str, str]:
    """`{number: "open"|"closed"}`, as `gh issue list` is asked to print it.

    The splitting is `unsynced.offered`'s, so that what counts as a line of a
    GitHub listing is decided in one place. What is added here is the only part
    that differs: GitHub has two states and a value that is neither is refused
    rather than passed over. Read as "not closed", it would report a
    disagreement that is not there and send somebody looking.
    """
    out = {}
    for number, state in offered(text):
        if state.lower() not in {"open", "closed"}:
            raise SystemExit(
                f"expected 'number<TAB>OPEN|CLOSED' per line, and got {state!r} for #{number}"
            )
        out[str(number)] = state.lower()
    return out


def drifted(export: Path, live: dict[str, str], repository: str) -> list[tuple[str, str, bool]]:
    """`(bead id, issue number, closed here)` for every pair that disagrees.

    Data, not a sentence. `unsynced.py` and `unexported.py` both hand back rows
    and let `main` say what they mean, and a reader that words its own finding
    is one whose wording has to be edited in two files to stay agreeing with
    the caller counting its rows.

    A bead whose issue GitHub has never heard of is skipped rather than
    reported. That is not drift -- it is a reference to an issue that does not
    exist, and it is not answerable here.

    SO IS A BEAD POINTING AT ANOTHER REPOSITORY, and it is skipped by the same
    line: `issue_of` hands back no number for a ref the listing's repository did
    not issue, so there is nothing to look up. Skipped rather than refused
    because a bead may perfectly well name somebody else's issue -- it is a
    reference to work elsewhere, which this reader has no opinion about.
    """
    out = []
    for number, row in rows(export.read_text()):
        ref = ref_of(row, export, number)
        if ref is None:
            continue
        issue = issue_of(ref, repository)
        if issue is None or issue not in live:
            continue
        closed_here = row.get("status") == "closed"
        closed_there = live[issue] == "closed"
        if closed_here == closed_there:
            continue
        identifier = row.get("id")
        if not identifier:
            # unexported.py refuses the same row for the same reason: a bead
            # nothing can name is one nobody can act on, and reporting it under
            # a made-up name is worse than stopping.
            raise SystemExit(f"{export}:{number} is a bead with no id, so nothing could name it")
        out.append((identifier, issue, closed_here))
    return out


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit(
            f"usage: gh issue list ... | {Path(sys.argv[0]).name} "
            "<issues.jsonl> <owner/repository>"
        )
    export, repository = Path(sys.argv[1]), sys.argv[2]
    if not export.exists():
        raise SystemExit(f"{export} does not exist, so nothing here knows what the tracker says")

    live = states(sys.stdin.read())
    for identifier, issue, closed_here in drifted(export, live, repository):
        side = "closed here, open on GitHub" if closed_here else "open here, closed on GitHub"
        print(f"{identifier}\t#{issue}\t{side}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
