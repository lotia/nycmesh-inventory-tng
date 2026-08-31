"""What a pull request carries as evidence that its review cycle ran.

One implementation of a question two very different things ask. The landing
gate asks it locally, before writing a receipt; a required CI check asks it on
GitHub's side, where no command spelling can route around the answer. They must
not be able to disagree, and the marker rule is subtle enough that two copies
would -- see ``carries`` for what "posted" means and why it is not "present".

``inventory-tng-x0jp`` is why the second caller exists.

## What counts

A stage is evidenced by a comment whose body carries its marker ON A LINE OF
ITS OWN, and ``code-review`` is additionally evidenced by any review submitted
through GitHub's review API, which is what ``/code-review --comment`` leaves
behind. DEVELOPERS.md "One review pass, findings filed per issue" is where the
rule lives; this is the reader for it.

## What is deliberately not checked

**Which head the evidence was left at.** The local receipt ties evidence to a
head, because a file on somebody's disk would otherwise vouch for a branch it
had never seen. Nothing here needs that: the evidence IS the pull request, and
it cannot be moved to another one. docs/decisions/0020-who-merges.md argues
that difference and names the open question -- ``inventory-tng-8nqo`` -- whose
settling would change it. When it settles, this is the code that changes.

Stdlib only, and no virtualenv: this runs from a workflow step and from a bash
script that has neither. Same reason ``ci-check-names.py`` is written that way.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

#: The marker for each stage, and the whole of what a stage is here. Named once
#: because the gate and the check have to look for the same string; DEVELOPERS.md
#: is where the choice of a marker over prose is argued.
MARKERS = {
    "code-review": "<!-- review-cycle: code-review -->",
    "simplify": "<!-- review-cycle: simplify -->",
}

#: The name the required check reports under, which is the job's ``name:`` in
#: ci.yml. Named here because scripts/landing-gate.sh has to read PAST it when
#: it asks whether a pull request is green: that check is red precisely when
#: the cycle has not run, and treating it as an ordinary failure silenced the
#: nudge for the one state it exists to catch.
CHECK = "Review cycle"

#: The stages a cycle has, in the order a person runs them.
STAGES = ("code-review", "simplify")

# AN EMPTY SET OF STAGES MEANS "NOTHING IS MISSING", which is the one answer
# nothing here may give by accident: the merge guard reads it as a complete
# cycle and the required check exits 0. It is refused here rather than guarded
# at each caller, because two shell readers had the guard and the CI check --
# the one that gates `main` -- had none.
#
# SystemExit rather than assert, and the difference is the whole reason this is
# five lines instead of one. An assert has to be CAUGHT to become a sentence,
# so every caller grows a `try/except AssertionError` that reworders it -- and
# when that was tried here, three of the five importers got one and two did
# not, leaving a traceback the stop hook reported as "could not read what gh
# said". An assert is also stripped under `-O` and PYTHONOPTIMIZE, which would
# remove this from the required check exactly as it removes it from everything
# else. SystemExit is caught by nobody, says the same sentence on the same
# stream at every caller, and survives the flag.
if not STAGES:
    raise SystemExit("review_cycle names no stages, so nothing could be checked.")

#: Stages a submitted review is evidence for on its own, with no marker to
#: type. The review IS the artifact, and asking for a marker beside it would be
#: asking somebody to write down that a pass had happened -- which is the thing
#: a marker exists to avoid having to trust.
#:
#: ANY review, including the empty-bodied ones GitHub creates to carry a single
#: inline diff comment. That is deliberate: an inline finding is a review pass
#: leaving evidence, and on one recent pull request six of seven entries here
#: were that shape.
BY_REVIEW = ("code-review",)


def settled(check: dict[str, Any]) -> bool:
    """Whether one entry from a checks list is green FOR THE PURPOSES OF REVIEW.

    THE REVIEW-CYCLE CHECK IS NOT PART OF THE GREENNESS QUESTION, and leaving
    it in silenced the stop hook exactly where it was needed. ``CHECK`` is red
    PRECISELY WHEN the cycle has not run -- so "stop only when the checks are
    green" meant the nudge went quiet for the one state it was registered to
    catch, which is a session deciding the work is finished instead of running
    the cycle. Marking a batch ready is likewise what INVITES the review that
    turns it green, so the same exclusion is what lets a pull request put back
    into draft be offered again without a commit pushed to satisfy a checker.

    The rest of the list still has to be green: a branch whose tests are
    failing has nothing to review yet, which is why this reads past one name
    rather than dropping the question.

    HERE RATHER THAN IN ITS TWO CALLERS, which is the whole point. The stop
    hook and the `ready` arm of scripts/landing-gate.sh both ask this, and when
    they held separate copies -- one in Python, one as a ``gh --jq`` filter --
    the copies drifted: NEUTRAL was green to one and red to the other. That is
    the same drift ``carries`` is in this module to prevent, in the same file,
    found the same way.

    Both shapes gh reports are accepted because both callers see one each:
    ``statusCheckRollup`` says ``context``/``state`` for a commit status and
    ``name``/``conclusion`` for a check run, and ``gh pr checks --json`` says
    ``name``/``state``.
    """
    if (check.get("name") or check.get("context") or "") == CHECK:
        return True
    verdict = (check.get("conclusion") or check.get("state") or "").upper()
    return verdict in ("SUCCESS", "NEUTRAL", "SKIPPED")


def carries(body: str | None, marker: str) -> bool:
    """Whether ``body`` POSTS ``marker``, rather than merely showing it.

    The distinction is the whole of this function, and DEVELOPERS.md "Quoting
    one is not posting one" carries what it cost to learn: a substring test let
    a comment that only mentioned the marker stand as evidence.

    So it has to be alone on its line and outside any fence. Markdown allows up
    to three spaces before a construct; a fourth makes it a code block, which is
    the marker being displayed rather than applied. Both refusals below print
    the marker indented for exactly that reason, and would otherwise be evidence
    that the pass they are complaining about had run.

    Somebody determined can still put the marker alone on a line having run
    nothing. That is a forgery rather than an accident, and it is not what this
    closes -- see docs/decisions/0020-who-merges.md.
    """
    fenced = False
    for line in (body or "").splitlines():
        bare = line.rstrip()
        if bare.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced:
            continue
        if bare.lstrip() == marker and len(bare) - len(bare.lstrip()) < 4:
            return True
    return False


def evidence(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """What each stage has behind it, as artifacts somebody can go and read.

    ``payload`` is what ``gh pr view --json comments,reviews`` returns, and it
    is every comment and every review rather than a first page: gh loops on the
    cursor until there is no next page. That matters enough to state, because a
    reader that saw only a page would call an evidenced stage unevidenced, and
    the comments here ARE the artifact.

    ONLY ISSUE COMMENTS ARE IN ``comments``. A review body and an inline diff
    comment are not: both arrive under ``reviews``, the second as an entry with
    an empty body. So the simplify marker has to be posted with ``gh pr comment``
    -- posting it as a review body puts it somewhere this never looks, and the
    refusal would then tell somebody to do the thing they had just done.
    """
    comments = payload.get("comments") or []
    reviews = payload.get("reviews") or []

    found: dict[str, list[dict[str, Any]]] = {}
    for stage in STAGES:
        marker = MARKERS[stage]
        found[stage] = [
            {
                "kind": "comment",
                "id": c.get("id"),
                "author": (c.get("author") or {}).get("login"),
                "at": c.get("createdAt"),
                "url": c.get("url"),
            }
            for c in comments
            if carries(c.get("body"), marker)
        ]
        if stage in BY_REVIEW:
            found[stage] += [
                {
                    "kind": "review",
                    "id": r.get("id"),
                    "author": (r.get("author") or {}).get("login"),
                    "at": r.get("submittedAt"),
                    "commit": (r.get("commit") or {}).get("oid"),
                }
                for r in reviews
            ]
    return found


def missing(found: dict[str, list[dict[str, Any]]]) -> list[str]:
    """Which stages have nothing behind them, in the order they are run."""
    return [stage for stage in STAGES if not found.get(stage)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Say whether a pull request carries evidence that its review cycle ran.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print what was found as JSON rather than as a report",
    )
    # SO THAT THE NAME HAS ONE READER TOO. `CHECK` is needed by a shell script
    # and by two test suites, and each of them reaching in with its own
    # four-line `sys.path` shim would be three copies of the extraction beside
    # the one copy of the string this module exists to hold. Answered before
    # stdin is touched, because a caller that only wants the name has no
    # payload to give.
    parser.add_argument(
        "--check-name",
        action="store_true",
        help="print the name the review-cycle check reports under, and exit",
    )
    args = parser.parse_args(argv)

    if args.check_name:
        print(CHECK)
        return 0

    # UNREADABLE INPUT REFUSES. A check that could not see is not a check with
    # nothing to object to -- the same reasoning scripts/landing-gate.sh applies
    # to a gh that will not answer, and the reason this exits 2 rather than 1:
    # nothing was examined, which is a different answer from having examined and
    # objected.
    try:
        payload = json.load(sys.stdin)
    except ValueError as exc:
        print(f"Could not read what gh said: {exc}", file=sys.stderr)
        return 2
    if not isinstance(payload, dict):
        print("Expected an object from gh, with comments and reviews in it.", file=sys.stderr)
        return 2

    found = evidence(payload)
    short = missing(found)

    if args.json:
        print(json.dumps({"evidence": found, "missing": short}, indent=2, sort_keys=True))
        return 1 if short else 0

    for stage in STAGES:
        items = found[stage]
        if items:
            print(f"  {stage:<12} {len(items)} on the pull request")
        else:
            print(f"  {stage:<12} nothing on the pull request")
    print()

    if not short:
        print("The review cycle ran. Both stages have something behind them.")
        return 0

    for stage in short:
        print(f"Nothing on this pull request shows the {stage} pass ran.", file=sys.stderr)
        print("", file=sys.stderr)
        if stage in BY_REVIEW:
            # No marker offered for this one, deliberately: the review submits
            # its own evidence, so asking for a marker here would be asking
            # somebody to type that a pass had happened.
            print("  It needs a person: an agent cannot run it.", file=sys.stderr)
            print("", file=sys.stderr)
            print("      /code-review <pr> --comment", file=sys.stderr)
        else:
            print("  Post its findings to the pull request with this line in the body:", file=sys.stderr)
            print("", file=sys.stderr)
            print(f"      {MARKERS[stage]}", file=sys.stderr)
        print("", file=sys.stderr)

    print(
        'See DEVELOPERS.md "One review pass, findings filed per issue".',
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
