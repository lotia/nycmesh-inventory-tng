"""Which issues a batch holds, read from the exported beads tracker.

Used by check-batch.sh. The export is committed, so it is the same file the
branch is proposing and CI can read it without installing the tracker itself.
Membership is recorded there as a parent-child dependency on the batch's epic.

Usage: batch-membership.py <issues.jsonl> <issue>...   with EPIC optionally set,
and UNFINISHED=1 while the branch is still being built or reviewed.

Prints lines the caller turns into its own output, "note <text>" for something
worth saying and "fail <text>" for something worth refusing over.

TWO OBJECTIONS, AND ONLY ONE OF THEM IS ABOUT A MISTAKE.

An issue landed here that the batch does not hold is wrong whenever it is
found: it means a commit closed something nobody put in this batch, and no
amount of further work makes it right.

An issue in the batch that has not landed yet only means the batch is not
finished. That is the normal state of a branch being built one issue at a
time -- which is exactly what DEVELOPERS.md asks for, so that CI runs per issue
rather than once at the end -- and asking it of an unfinished branch is asking
the wrong question. Measured across every pull request run this repository has,
that question was answered "no" on 48% of them, and a check that is red as a
matter of routine is one nobody reads. inventory-tng-ee2c has the numbers.

So UNFINISHED downgrades the second to a note and leaves the first alone. What
asks the finished question is the landing gate, once, at the merge.
"""

from __future__ import annotations

import json
import os
import sys


def main() -> None:
    path, landed = sys.argv[1], set(sys.argv[2:])
    epic = os.environ.get("EPIC", "")
    # How many commits closed something, which is not how many issues they
    # closed. Absent when a caller has not counted, and then the batch rule
    # below cannot fire -- a rule that guesses is worse than one that waits.
    try:
        landed_commits = int(os.environ.get("LANDED_COMMITS", "0"))
    except ValueError:
        landed_commits = 0

    parent: dict[str, str] = {}
    children: dict[str, set[str]] = {}
    epics: set[str] = set()
    for line in open(path):
        try:
            issue = json.loads(line)
        except ValueError:
            continue  # a blank line, or an export half-written
        # DEVELOPERS.md#the-message: an epic does no work of its own, so it is
        # not one of the issues a batch holds and never needs an epic above it.
        if issue.get("issue_type") == "epic" and issue.get("id"):
            epics.add(issue["id"])
        for dep in issue.get("dependencies") or []:
            if dep.get("type") != "parent-child":
                continue
            # A dependency missing either end is not a membership record. It is
            # skipped rather than raised on, because one malformed row must not
            # take the whole check down with it -- a check that dies is a check
            # that passed, as far as anything downstream can tell.
            child, epic_id = dep.get("issue_id"), dep.get("depends_on_id")
            if not child or not epic_id:
                continue
            parent[child] = epic_id
            children.setdefault(epic_id, set()).add(child)

    landed -= epics

    if not epic:
        batches = {parent[i] for i in landed if i in parent}
        if len(batches) > 1:
            print(
                "fail these commits close issues from %d batches: %s"
                % (len(batches), " ".join(sorted(batches)))
            )
            return
        if not batches:
            # A single issue shipping on its own belongs to no batch and needs
            # no epic. Several landing together do: DEVELOPERS.md#pull-requests
            # wants what belongs to a batch recorded rather than remembered, and
            # silence here is how a batch skips that without anything noticing.
            if len(landed) > 1 and landed_commits > 1:
                print(
                    "fail %d issues landed together with no epic: %s"
                    % (len(landed), " ".join(sorted(landed)))
                )
            return
        epic = batches.pop()

    print("note batch epic: " + epic)

    expected = children.get(epic, set())
    if not expected:
        return

    unfinished = os.environ.get("UNFINISHED") == "1"
    for missing in sorted(expected - landed):
        if unfinished:
            print("note not landed here yet, which is fine until the batch is done: " + missing)
        else:
            print("fail in the batch but not landed here: " + missing)
    for extra in sorted(landed - expected):
        print("fail landed here but not in the batch: " + extra)



if __name__ == "__main__":
    main()
