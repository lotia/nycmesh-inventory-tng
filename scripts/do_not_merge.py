"""Whether a pull request says "do not merge" in the one way a machine reads.

What the marker is for is DEVELOPERS.md, under merging; why the check that runs
this lives in ci.yml rather than in a label or a hook is said at the job. This
is the reader both of them use. ``inventory-tng-g4dh``.

## What counts

``MARKER`` below, posted in the pull request BODY. ``review_cycle.carries``
decides what "posted" means, which is the whole reason this file is four lines
of logic rather than a substring test.

## Why it borrows from a module about something else

``carries`` lives beside the review-cycle markers because that is where the
posted-versus-shown distinction was learned and is tested. Two features now ask
it one question, which is a second importer and not a second copy -- so this
imports rather than restating, and a third caller is where the predicate would
earn a module of its own.

Stdlib only, and no virtualenv: this runs from a workflow step, the same reason
``review_cycle`` and ``ci-check-names`` are written that way.

Usage:  gh pr view <pr> --json body | python3 do_not_merge.py
"""

from __future__ import annotations

import json
import sys

from review_cycle import carries

#: The one spelling. A comment, so it is invisible in the rendered body and
#: costs a reader nothing; on a line of its own, so writing ABOUT it is free.
MARKER = "<!-- do-not-merge -->"

#: The name GitHub reports the required check under, which is the job's ``name:``
#: in ci.yml. Named here for the reason ``review_cycle.CHECK`` is: a caller has
#: to be able to tell THIS check apart from an ordinary red one, because the
#: advice differs completely -- every other failing check is one to go and make
#: green, and this is the one that must never be.
CHECK = "Not marked do-not-merge"


def main() -> int:
    # SO THAT THE NAME HAS ONE READER TOO. ``review_cycle.main`` answers for its
    # own check the same way, and says why there rather than beside a flag.
    if sys.argv[1:] == ["--check-name"]:
        print(CHECK)
        return 0

    # UNREADABLE INPUT REFUSES, and refuses differently from a marked pull
    # request: nothing was examined, which is not the same answer as having
    # examined and objected. The same reasoning review_cycle applies to a `gh`
    # that will not answer.
    try:
        payload = json.load(sys.stdin)
    except ValueError as exc:
        print(f"Could not read what gh said: {exc}", file=sys.stderr)
        return 2
    if not isinstance(payload, dict):
        print("Expected an object from gh, with a body in it.", file=sys.stderr)
        return 2

    if not carries(payload.get("body"), MARKER):
        print("This pull request is not marked do-not-merge.")
        return 0

    print("This pull request posts the do-not-merge marker.", file=sys.stderr)
    print("", file=sys.stderr)
    print("  It is not to be merged, whatever else is green and whatever", file=sys.stderr)
    print("  state it is in. AGENTS.md says so in the same words, and this", file=sys.stderr)
    print("  check is here so that the rule does not rest on being read.", file=sys.stderr)
    print("", file=sys.stderr)
    print("  If the work is finished and the marker is stale, take the line", file=sys.stderr)
    print("  out of the body and PUSH. Editing it away does not by itself", file=sys.stderr)
    print("  make this look again, and the last verdict is what stands.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
