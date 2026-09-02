#!/usr/bin/env bash
# What must and must not count as a pull request refusing to be merged.
#
# The second half is the important half. A reader that treats a MENTION of the
# marker as a refusal blocks every pull request that explains the mechanism --
# including this repository's own documentation of it -- and a gate that fires
# on the wrong thing is one people learn to route around. The mirror of the
# failure inventory-tng-egh4.1 taught on the review-cycle markers.
#
# Usage: scripts/do-not-merge.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
READER="$HERE/do_not_merge.py"
. "$HERE/testlib.sh"

MARKER='<!-- do-not-merge -->'

# body <text> -- a pull request whose body is that text, as gh reports it.
# Through argv and json.dumps rather than a heredoc, so a case can hold
# newlines, fences and leading whitespace without the shell eating any of it.
body() {
  BODY=${1-} python3 -c '
import json, os, sys
sys.stdout.write(json.dumps({"body": os.environ["BODY"]}))' | python3 "$READER" 2>&1
}

echo "a pull request that is not marked"

out=$(body "An ordinary batch. Three issues, one commit each."); status=$?
assert "$out" "$status" 0 "not marked do-not-merge" "an ordinary body merges"

out=$(body ""); status=$?
assert "$out" "$status" 0 "not marked do-not-merge" "and so does an empty one"

echo
echo "a pull request that is"

out=$(body "A spike, kept for the meeting.

$MARKER"); status=$?
assert "$out" "$status" 1 "posts the do-not-merge marker" "the marker on a line of its own refuses"

out=$(body "   $MARKER"); status=$?
assert "$out" "$status" 1 "posts the do-not-merge marker" "three spaces is still posted, as Markdown has it"

# The refusal has to say what to do next, or the next person edits the body,
# sees nothing change, and concludes the check is broken.
assert "$out" "$status" 1 "PUSH" "and it says that removing the line needs a push"

echo
echo "writing about the marker is not posting it"
# Every one of these is a shape the body of a real pull request takes when it
# EXPLAINS the mechanism -- starting with the pull request that introduces it,
# which would otherwise have blocked itself.

out=$(body "I took the \`$MARKER\` line out, so this can merge now."); status=$?
assert "$out" "$status" 0 "not marked do-not-merge" "a marker inside a sentence is not a refusal"

# TWO CASES AND NOT NINE. Every shape of quoting -- a sentence, a fence, an
# indent of four spaces or one tab, trailing words -- is already pinned against
# `carries` itself in review-cycle.test.sh, and pinning them a second time here
# means a deliberate change to that rule reds two suites, one of which names the
# wrong subject. What is THIS reader's own business is that it calls `carries`
# rather than testing for a substring, and the nested fence is the case that
# cannot pass any other way: it is also the shape the documentation of this
# feature has to use to quote an example. inventory-tng-1tyo.
out=$(body "Block a pull request by posting this in the body:

\`\`\`\`
\`\`\`
$MARKER
\`\`\`
\`\`\`\`
"); status=$?
assert "$out" "$status" 0 "not marked do-not-merge" "nor one shown inside a fence nested in a longer one"

echo
echo "what it does when it cannot see"
# Refusing differently from a marked pull request, for the reason
# review-cycle.test.sh gives at its own copy of these three cases.

out=$(printf 'not json' | python3 "$READER" 2>&1); status=$?
assert "$out" "$status" 2 "Could not read what gh said" "unreadable input refuses, and says so"

out=$(printf '[]' | python3 "$READER" 2>&1); status=$?
assert "$out" "$status" 2 "Expected an object" "valid JSON of the wrong shape refuses too"

out=$(printf '{}' | python3 "$READER" 2>&1); status=$?
assert "$out" "$status" 0 "not marked do-not-merge" "an answer with no body in it marks nothing"

echo
echo "the marker is the one the documentation tells people to post"
# A string, and nothing else, joins this check to what the documentation
# instructs. If they drift, the rule names a line that stops nothing -- so both
# files that carry it are pinned, not just the one an agent reads.
#
# `grep -qF` and the status, rather than counting: `assert` matches a substring,
# so a count of 1 was satisfied by 10 through 19 as well, and the case did not
# hold what its name said. It also has to survive a second, legitimate mention.
grep -qF -- "$MARKER" "$HERE/../AGENTS.md"; status=$?
exits "$status" 0 "AGENTS.md names the marker the check looks for"

grep -qF -- "$MARKER" "$HERE/../DEVELOPERS.md"; status=$?
exits "$status" 0 "and so does the document that defines it"

echo
echo "the name the check reports under is a job that exists"
# A string, and nothing else, joins scripts/landing-gate.sh's ready arm to the
# job in ci.yml: it tells THIS red check apart from an ordinary one, because the
# advice differs completely. review-cycle.test.sh pins its own name the same way.
named=$(python3 "$READER" --check-name); status=$?
assert "$named" "$status" 0 "Not marked do-not-merge" "the reader answers for the name"

grep -q "name: $named\$" "$HERE/../.github/workflows/ci.yml"; status=$?
exits "$status" 0 "and ci.yml has a job with exactly that name"

verdict
