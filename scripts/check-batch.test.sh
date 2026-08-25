#!/usr/bin/env bash
# What check-batch.sh must say, and about what.
#
# A throwaway repository per case, for the same reason check-commit.test.sh
# builds one: the thing under test reads real commits, and a test that ran
# against this repository would mean something different after every merge.
#
# No .beads/issues.jsonl is created, so the membership check sits out. That is
# the path an outside contributor takes too -- the structural rules are the
# ones that hold without a tracker, and they are what these cases are about.
#
# Usage: scripts/check-batch.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
CHECK="$HERE/check-batch.sh"
. "$HERE/testlib.sh"
workspace

scene() {
  new_repo "$WORK/repo"
  cd "$WORK/repo" || exit 1
  echo base > file
  git add -A
  git commit -q -m "base"
  git branch -f base HEAD
}

# land <file-content> <message>
land() {
  local content=$1 message=$2
  echo "$content" >> file
  git add -A
  git commit -q -F - <<<"$message"
}

tracker() {
  mkdir -p "$WORK/repo/.beads"
  cat > "$WORK/repo/.beads/issues.jsonl"
}

check "$CHECK" base..HEAD

echo "check-batch.sh"

scene
land a 'aaa: Extract the decode loop

Closes: inventory-tng-aaa'
land b 'bbb: Read the label map from the cache

Closes: inventory-tng-bbb'
expect 0 "2 commits, one issue each" "one issue each, in order, is a batch"

# An issue is allowed more than one commit, so long as they are its own.
scene
land a 'aaa: Move the loop before rewriting it

Refs: inventory-tng-aaa'
land b 'aaa: Rewrite the loop now that it has moved

Closes: inventory-tng-aaa'
land c 'bbb: Read the label map from the cache

Closes: inventory-tng-bbb'
expect 0 "3 commits, one issue each" "an issue may take two commits"

scene
land a 'aaa: Extract the decode loop

Closes: inventory-tng-aaa
Refs: inventory-tng-bbb'
expect 1 "the trailers name" "a commit naming two issues is refused"

# The rule is delegated, so it must be objected to once rather than in two
# wordings -- which is what happened while this script parsed trailers too.
scene
land a 'aaa: Extract the decode loop

Closes: inventory-tng-aaa
Refs: inventory-tng-bbb'
output=$("$CHECK" base..HEAD 2>&1)
if [[ "$(grep -c 'belongs to one issue' <<<"$output")" -eq 1 ]]; then
  pass "one violation is one objection"
else
  fail_case "one violation is one objection" "$output"
fi

# --epic, which no case could express until `expect` learned to carry a value.
scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-epic","status":"open"}
{"_type":"issue","id":"inventory-tng-aaa","status":"closed","dependencies":[{"issue_id":"inventory-tng-aaa","depends_on_id":"inventory-tng-epic","type":"parent-child"}]}
{"_type":"issue","id":"inventory-tng-bbb","status":"open","dependencies":[{"issue_id":"inventory-tng-bbb","depends_on_id":"inventory-tng-epic","type":"parent-child"}]}
JSONL
land a 'aaa: Extract the decode loop

Closes: inventory-tng-aaa'
expect --epic inventory-tng-epic -- 1 "in the batch but not landed here" "the batch can be named rather than inferred"

scene
land a 'aaa: Extract the decode loop

Closes: inventory-tng-aaa'
land b 'aaa: Extract it again

Closes: inventory-tng-aaa'
expect 1 "closed by 2 commits" "closing one issue twice is refused"

scene
land a 'aaa: Extract the decode loop

Refs: inventory-tng-aaa'
land b 'bbb: Read the label map from the cache

Closes: inventory-tng-bbb'
land c 'aaa: Finish the decode loop

Closes: inventory-tng-aaa'
expect 1 "picked up again after" "interleaving two issues is refused"

scene
land a 'Extract the decode loop with no trailer at all'
expect 1 "found none" "a commit belonging to nothing is refused"

# The message rules have one home. This is only checking that a branch does not
# get to skip them by being read here instead of at commit time.
scene
land a 'aaa: Extracted the decode loop

Closes: inventory-tng-aaa'
expect 1 "not the imperative" "a message that check-commit.sh would refuse is refused here too"

# See check-batch.sh on why a pending fixup is refused rather than noted.
scene
land a 'aaa: Extract the decode loop

Closes: inventory-tng-aaa'
land b 'fixup! aaa: Extract the decode loop'
expect 1 "waiting to be folded in" "an unfolded fixup is refused"
# The advice has to be runnable by whoever is being advised, and this repository
# lets an agent do the collapse unattended: `-i` opens an editor that never
# returns, leaving the branch mid-rebase so the retry fails too. AGENTS.md
# "Shell" is the rule; inventory-tng-4md is where it was not being kept.
expect 1 "core.editor=true" "and told how to fold them in without an editor"
output=$("${CHECK_CMD[@]}" "${CHECK_ARGS[@]}" 2>&1)
status=$?
refute "$output" "$status" 1 "rebase -i" "never with the interactive flag"
refute "$output" "$status" 1 "sequence.editor" "and not the editor a non-interactive rebase never opens"

# The case that showed the advice was wrong. A fixup! needs no editor whatever
# is configured, so it could not tell the two settings apart; a squash! asks
# for the combined message and fails without one. `absorbed` counts both, so
# both are advised, and the advice has to work for the harder of them.
scene
land a 'aaa: Extract the decode loop

Closes: inventory-tng-aaa'
land b 'squash! aaa: Extract the decode loop'
expect 1 "core.editor=true" "a pending squash! is advised the same way"
# And that the advice does what it says on this git, rather than only reading
# as though it does: run it, with no editor reachable by any other route.
(
  cd "$WORK/repo" &&
    env -u GIT_EDITOR -u EDITOR -u VISUAL \
      git -c core.editor=true rebase --autosquash base >/dev/null 2>&1
)
status=$?
output=$(git -C "$WORK/repo" log --oneline base..HEAD)
assert "$output" "$status" 0 "aaa: Extract the decode loop" \
  "and running it folds the squash! in without stopping"

# amend! carries the original message, trailers and all. Resolved to its target
# like a fixup, it neither loses the commit's issue nor closes it twice.
scene
land a 'aaa: Extract the decode loop

Closes: inventory-tng-aaa'
land b 'amend! aaa: Extract the decode loop

Closes: inventory-tng-aaa'
output=$("$CHECK" base..HEAD 2>&1)
assert "$output" $? 1 "waiting to be folded in" "an amend! is a commit waiting to be folded in"
refute "$output" 1 1 "closed by 2 commits" "and its issue is not counted twice"

scene
land a 'aaa: Extract the decode loop

Closes: inventory-tng-aaa'
output=$("$CHECK" deadbeef..HEAD 2>&1)
assert "$output" $? 1 "Cannot read" "an unresolvable range is refused, not called empty"

scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-epic","status":"open"}
{"_type":"issue","id":"inventory-tng-aaa","status":"closed","dependencies":[{"issue_id":"inventory-tng-aaa","depends_on_id":"inventory-tng-epic","type":"parent-child"}]}
{"_type":"issue","id":"inventory-tng-bbb","status":"closed","dependencies":[{"issue_id":"inventory-tng-bbb","depends_on_id":"inventory-tng-epic","type":"parent-child"}]}
JSONL
land a 'aaa: Extract the decode loop

Closes: inventory-tng-aaa'
land b 'bbb: Read the label map from the cache

Closes: inventory-tng-bbb'
expect 0 "batch epic: inventory-tng-epic" "the batch is read from the committed tracker"

scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-epic","status":"open"}
{"_type":"issue","id":"inventory-tng-aaa","status":"closed","dependencies":[{"issue_id":"inventory-tng-aaa","depends_on_id":"inventory-tng-epic","type":"parent-child"}]}
{"_type":"issue","id":"inventory-tng-bbb","status":"open","dependencies":[{"issue_id":"inventory-tng-bbb","depends_on_id":"inventory-tng-epic","type":"parent-child"}]}
JSONL
land a 'aaa: Extract the decode loop

Closes: inventory-tng-aaa'
expect 1 "in the batch but not landed here: inventory-tng-bbb" "a batch half landed is refused"

# See batch-membership.py on why no epic is not a disagreement.
scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","status":"closed"}
JSONL
land a 'aaa: Extract the decode loop

Closes: inventory-tng-aaa'
expect 0 "One commit, one issue" "a lone issue needs no epic"

# The other half of the rule above: more than one issue does need an epic.
scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","status":"closed"}
{"_type":"issue","id":"inventory-tng-bbb","status":"closed"}
JSONL
land a 'aaa: Extract the decode loop

Closes: inventory-tng-aaa'
land b 'bbb: Move the detector behind an interface

Closes: inventory-tng-bbb'
expect 1 "2 issues landed together with no epic" "a batch without an epic is refused"

# git writes a revert's message itself, so none of the rules apply to it. It
# used to be skipped for free, by check-commit.sh refusing it at its own door.
scene
land a 'aaa: Extract the decode loop

Closes: inventory-tng-aaa'
git revert --no-edit HEAD >/dev/null 2>&1
expect 0 "2 commits, one issue each" "a revert is not somebody's issue being landed"

# Both checkers read the same message. A body line git would drop must not be
# a rule to one of them and invisible to the other.
scene
land a 'aaa: Extract the decode loop

# This comment line is far longer than seventy-two characters and git drops it.

Closes: inventory-tng-aaa'
expect 0 "One commit, one issue" "a comment line is not part of the message"

# The remedy for one commit closing four issues is to split it, not to make an
# epic, and the squash objection already says so.
scene
land a 'aaa: Extract the decode loop

Closes: inventory-tng-aaa
Closes: inventory-tng-bbb'
expect 1 "2 'Closes' trailers" "one commit closing two is a squash, not a batch"

# The objection says which commit it belongs to. Deleting the prefix must fail
# a case, or the feature is untested.
scene
land a 'aaa: Extracted the decode loop

Closes: inventory-tng-aaa'
sha=$(git rev-parse --short=8 HEAD)
expect 1 "$sha \"Extracted\" is not the imperative" "an objection names its commit"

# See batch-membership.py on why a malformed row is stepped over.
scene
tracker <<'JSONL'
not json at all
{"_type":"issue","id":"inventory-tng-aaa","status":"closed"}
{"_type":"issue","id":"inventory-tng-bbb","dependencies":[{"type":"parent-child"}]}
JSONL
land a 'aaa: Extract the decode loop

Closes: inventory-tng-aaa'
expect 0 "One commit, one issue" "a malformed tracker line is stepped over"

scene
expect 0 "Nothing to check" "an empty range is not a failure"

# See check-batch.sh on --draft.
scene
land a 'aaa: Extract the decode loop

Closes: inventory-tng-aaa'
land b 'fixup! aaa: Extract the decode loop'
expect --draft -- 0 "which is fine while reviewing" "a draft may carry commits waiting to be folded in"

# But a structural fault still fails, draft or not.
scene
land a 'aaa: Extract the decode loop

Closes: inventory-tng-aaa
Refs: inventory-tng-bbb'
expect --draft -- 1 "belongs to one issue" "a draft does not excuse a commit holding two issues"

# See check-batch.sh on why the delegation loop must skip what was absorbed.
scene
land a 'aaa: Extract the decode loop into its own new module

Closes: inventory-tng-aaa'
land b 'amend! aaa: Extract the decode loop into its own new module

Closes: inventory-tng-aaa'
output=$("$CHECK" --draft base..HEAD 2>&1)
refute "$output" $? 0 "over 50" "an amend! subject is not charged its own prefix"

# See absorbed in check-batch.sh on a prefix that names nothing.
scene
land a 'fixup! '
output=$("$CHECK" base..HEAD 2>&1)
refute "$output" $? 1 "waiting to be folded in" "a bare prefix is not a commit waiting for anything"
# --list, which is what says the batch's contents on the pull request.
scene
land a 'aaa: Extract the decode loop

Closes: inventory-tng-aaa'
land b 'bbb: Read the label map from the cache

Closes: inventory-tng-bbb'
output=$("$CHECK" --list base..HEAD 2>&1)
if [[ "$(wc -l <<<"$output")" -eq 2 && "$output" == *$'\t'"inventory-tng-aaa"$'\t'* ]]; then
  pass "--list gives one tab-separated row per commit"
else
  fail_case "--list gives one tab-separated row per commit" "$output"
fi
refute "$output" 0 0 "Nothing to object to" "and says nothing else"

# Nothing on stdout for an empty range in --list mode: say-batch.sh reads rows.
scene
output=$("$CHECK" --list HEAD..HEAD 2>/dev/null)
if [[ -z "$output" ]]; then
  pass "--list says nothing at all about an empty range"
else
  fail_case "--list says nothing at all about an empty range" "$output"
fi

scene
land a 'aaa: Extract the decode loop

Closes: inventory-tng-aaa'
output=$("$CHECK" --list --draft base..HEAD 2>&1)
assert "$output" $? 2 "do not apply" "--list refuses a flag that cannot matter"

# The squash tripwire: one question of landed history, readable whatever
# convention a message was written under. See check-batch.sh on --squashed.
scene
land a 'aaa: Extract the decode loop

Closes: inventory-tng-aaa'
land b 'bbb: Read the label map

Closes: inventory-tng-bbb'
expect --squashed 0 "No commit here closes more than one issue" "one issue each passes the tripwire"

# What a squash merge composes: every squashed message in one commit.
scene
land a 'A batch, flattened

Closes: inventory-tng-aaa
Closes: inventory-tng-bbb'
expect --squashed 1 "closes 2 issues" "a squashed batch is caught"

# It asks nothing else, so history written before today's message rules -- a
# trailer with no colon -- does not fail it forever.
scene
land a 'Extracted the decode loop.

Closes inventory-tng-aaa'
expect --squashed 0 "No commit here closes more than one issue" "older conventions are not re-litigated"

verdict
