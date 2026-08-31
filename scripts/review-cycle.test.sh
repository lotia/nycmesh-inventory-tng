#!/usr/bin/env bash
# What counts as evidence that the review cycle ran, and what must not.
#
# The second half is the important half, and it is not a style preference. A
# reader that treats a MENTION of a marker as evidence has already let a pull
# request through on the strength of a comment explaining that nothing had been
# recorded -- inventory-tng-egh4.1, which happened. Every case below that says
# "is not evidence" is that failure, spelled a different way.
#
# Usage: scripts/review-cycle.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
READER="$HERE/review_cycle.py"
. "$HERE/testlib.sh"

REVIEW='<!-- review-cycle: code-review -->'
SIMPLIFY='<!-- review-cycle: simplify -->'

# read <comment bodies...>  -- a pull request carrying those comments and no
# reviews. Bodies go through argv rather than a heredoc so a case can hold
# leading spaces, fences and newlines without the shell eating them.
read_comments() {
  # RECORD SEPARATOR, not NUL: `$(...)` drops null bytes and warns, so the
  # bodies arrived joined and every case here would have been reading one
  # comment instead of two.
  BODIES=$(printf '%s\036' "$@") python3 -c '
import json, os, sys
bodies = [b for b in os.environ["BODIES"].split("\036") if b]
print(json.dumps({"comments": [
    {"body": b, "id": "C_%d" % i, "author": {"login": "someone"},
     "createdAt": "2026-08-31T10:00:00Z", "url": "http://x/%d" % i}
    for i, b in enumerate(bodies)], "reviews": []}))' | python3 "$READER" 2>&1
}

# A pull request carrying a submitted review and nothing else.
read_review() {
  python3 -c '
import json
print(json.dumps({"comments": [], "reviews": [
    {"id": "R_1", "author": {"login": "someone"},
     "submittedAt": "2026-08-31T10:00:00Z", "commit": {"oid": "a" * 40},
     "state": "COMMENTED"}]}))' | python3 "$READER" 2>&1
}

both() { read_comments "$REVIEW" "$SIMPLIFY"; }

echo "what a complete cycle looks like"
out=$(both); status=$?
assert "$out" "$status" 0 "The review cycle ran" "both markers posted is a complete cycle"

out=$(read_comments "findings

$REVIEW" "$SIMPLIFY"); status=$?
assert "$out" "$status" 0 "The review cycle ran" "a marker under prose in the same comment counts"

# THE ONE STAGE THAT NEEDS NO MARKER, and the reason it needs none: the review
# is the artifact `/code-review --comment` leaves behind, so asking for a marker
# would be asking somebody to type that a pass had happened.
out=$(read_comments "$SIMPLIFY"); status=$?
assert "$out" "$status" 1 "code-review" "a simplify marker alone is not the whole cycle"

out=$( (python3 -c '
import json
print(json.dumps({"comments": [{"body": "'"$SIMPLIFY"'", "id": "C_1",
    "author": {"login": "someone"}, "createdAt": "t", "url": "u"}],
  "reviews": [{"id": "R_1", "author": {"login": "someone"},
    "submittedAt": "t", "commit": {"oid": "a"}, "state": "COMMENTED"}]}))' \
  | python3 "$READER") 2>&1 ); status=$?
assert "$out" "$status" 0 "The review cycle ran" \
  "a submitted review evidences code-review with no marker typed"

out=$(read_review); status=$?
assert "$out" "$status" 1 "simplify" "but a review alone does not evidence simplify"

echo
echo "quoting a marker is not posting one"
# Each of these is a real shape a comment takes when it TALKS about the marker,
# and each was evidence once.
out=$(read_comments "my comment carried no \`$REVIEW\` marker, which is why nothing recorded" "$SIMPLIFY")
status=$?
assert "$out" "$status" 1 "code-review" "a marker inside a sentence is not evidence"

out=$(read_comments "post the findings with:

    $REVIEW

and it records" "$SIMPLIFY"); status=$?
assert "$out" "$status" 1 "code-review" "nor one indented four spaces, which is a code block"

out=$(read_comments "\`\`\`
$REVIEW
\`\`\`" "$SIMPLIFY"); status=$?
assert "$out" "$status" 1 "code-review" "nor one inside a fenced block"

out=$(read_comments "~~~
$REVIEW
~~~" "$SIMPLIFY"); status=$?
assert "$out" "$status" 1 "code-review" "nor inside a tilde fence"

# Markdown allows three spaces before a construct and no more, so this side of
# the boundary is still posted. Both halves are pinned because a reader that
# got the boundary wrong in the other direction would refuse honest evidence.
out=$(read_comments "   $REVIEW" "$SIMPLIFY"); status=$?
assert "$out" "$status" 0 "The review cycle ran" "three spaces is still a posted marker"

out=$(read_comments "$REVIEW trailing words" "$SIMPLIFY"); status=$?
assert "$out" "$status" 1 "code-review" "a marker with anything after it on the line is not alone"

echo
echo "the refusal says what to do, and it differs by stage"
out=$(read_comments "$REVIEW"); status=$?
assert "$out" "$status" 1 "$SIMPLIFY" "a missing simplify pass names the marker to post"

out=$(read_comments "$SIMPLIFY"); status=$?
assert "$out" "$status" 1 "/code-review <pr> --comment" \
  "a missing review pass names the command instead"
refute "$out" "$status" 1 "$REVIEW" \
  "and never offers the marker for the stage that submits its own evidence"

echo
echo "what it does when it cannot see"
# NOTHING EXAMINED IS NOT NOTHING WRONG, and the exit code says which. This is
# the direction inventory-tng-3sp is about: a check that fails open leaves the
# rule resting on it being believed while nothing enforces it.
out=$(printf 'not json' | python3 "$READER" 2>&1); status=$?
assert "$out" "$status" 2 "Could not read what gh said" "unreadable input refuses, and says so"

out=$(printf '[]' | python3 "$READER" 2>&1); status=$?
assert "$out" "$status" 2 "Expected an object" "valid JSON of the wrong shape refuses too"

out=$(printf '{}' | python3 "$READER" 2>&1); status=$?
assert "$out" "$status" 1 "code-review" "an answer with neither list in it is short of both stages"

echo
echo "the name the check reports under is a job that exists"
# NOT DECORATION. scripts/landing-gate.sh reads PAST this one name when it asks
# whether the other checks are green, and the header of ci.yml says how a job
# there comes to be required at all -- so a string, and nothing else, joins the
# three. Rename the job and everything still runs: the gate stops recognising
# the check, counts it as an ordinary failure, and goes quiet for the one state
# it exists to catch. That is the silent half this repository keeps finding, so
# it is pinned rather than remembered.
CHECK=$(python3 "$READER" --check-name)
JOBS=$(python3 "$HERE/ci-check-names.py" "$HERE/../.github/workflows/ci.yml")
if printf '%s\n' "$JOBS" | grep -qxF "$CHECK"; then
  pass "review_cycle.CHECK names a job in ci.yml"
else
  fail_case "review_cycle.CHECK names a job in ci.yml" \
    "$(printf '       %q is not among the job names ci.yml reports under:\n%s' "$CHECK" "$JOBS")"
fi

echo
echo "the machine-readable form agrees with the report"
out=$(printf '{}' | python3 "$READER" --json 2>&1); status=$?
assert "$out" "$status" 1 '"missing"' "--json names what is missing"
assert "$out" "$status" 1 '"code-review"' "and which stages they are"

verdict
