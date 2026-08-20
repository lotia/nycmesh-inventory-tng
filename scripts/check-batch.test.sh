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

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CHECK="$HERE/check-batch.sh"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

passed=0
failed=0

scene() {
  rm -rf "$WORK/repo"
  mkdir -p "$WORK/repo"
  cd "$WORK/repo" || exit 1
  git init -q -b main .
  git config user.email test@example.invalid
  git config user.name Test
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

# expect <exit status> <substring> <what this case is called>
expect() {
  local want_status=$1 want_text=$2 name=$3 output status
  output=$("$CHECK" base..HEAD 2>&1)
  status=$?
  if [[ "$status" -eq "$want_status" && "$output" == *"$want_text"* ]]; then
    printf '  ok   %s\n' "$name"
    passed=$((passed + 1))
  else
    printf '  FAIL %s\n' "$name"
    printf '       wanted exit %s and %q\n' "$want_status" "$want_text"
    printf '       got exit %s:\n%s\n' "$status" "$output"
    failed=$((failed + 1))
  fi
}

tracker() {
  mkdir -p "$WORK/repo/.beads"
  cat > "$WORK/repo/.beads/issues.jsonl"
}

echo "check-batch.sh"

scene
land a 'aaa: Extract the decode loop

Closes inventory-tng-aaa'
land b 'bbb: Read the label map from the cache

Closes inventory-tng-bbb'
expect 0 "2 commits, one issue each" "one issue each, in order, is a batch"

# An issue is allowed more than one commit, so long as they are its own.
scene
land a 'aaa: Move the loop before rewriting it

Refs inventory-tng-aaa'
land b 'aaa: Rewrite the loop now that it has moved

Closes inventory-tng-aaa'
land c 'bbb: Read the label map from the cache

Closes inventory-tng-bbb'
expect 0 "3 commits, one issue each" "an issue may take two commits"

scene
land a 'aaa: Extract the decode loop

Closes inventory-tng-aaa
Refs inventory-tng-bbb'
expect 1 "the trailers name" "a commit naming two issues is refused"

# The rule is delegated, so it must be objected to once rather than in two
# wordings -- which is what happened while this script parsed trailers too.
scene
land a 'aaa: Extract the decode loop

Closes inventory-tng-aaa
Refs inventory-tng-bbb'
output=$(cd "$WORK/repo" && "$CHECK" base..HEAD 2>&1)
if [[ "$(grep -c 'belongs to one issue' <<<"$output")" -eq 1 ]]; then
  printf '  ok   %s\n' "one violation is one objection"
  passed=$((passed + 1))
else
  printf '  FAIL %s\n' "one violation is one objection"
  printf '       got:\n%s\n' "$output"
  failed=$((failed + 1))
fi

scene
land a 'aaa: Extract the decode loop

Closes inventory-tng-aaa'
land b 'aaa: Extract it again

Closes inventory-tng-aaa'
expect 1 "closed by 2 commits" "closing one issue twice is refused"

scene
land a 'aaa: Extract the decode loop

Refs inventory-tng-aaa'
land b 'bbb: Read the label map from the cache

Closes inventory-tng-bbb'
land c 'aaa: Finish the decode loop

Closes inventory-tng-aaa'
expect 1 "picked up again after" "interleaving two issues is refused"

scene
land a 'Extract the decode loop with no trailer at all'
expect 1 "found none" "a commit belonging to nothing is refused"

# The message rules have one home. This is only checking that a branch does not
# get to skip them by being read here instead of at commit time.
scene
land a 'aaa: Extracted the decode loop

Closes inventory-tng-aaa'
expect 1 "not the imperative" "a message that check-commit.sh would refuse is refused here too"

# See check-batch.sh on why a pending fixup is refused rather than noted.
scene
land a 'aaa: Extract the decode loop

Closes inventory-tng-aaa'
land b 'fixup! aaa: Extract the decode loop'
expect 1 "have not been folded in" "an unfolded fixup is refused"

scene
land a 'aaa: Extract the decode loop

Closes inventory-tng-aaa'
land b 'amend! aaa: Extract the decode loop

Closes inventory-tng-aaa'
expect 1 "is an amend! commit" "an amend! commit is refused rather than miscounted"

scene
land a 'aaa: Extract the decode loop

Closes inventory-tng-aaa'
output=$(cd "$WORK/repo" && "$CHECK" deadbeef..HEAD 2>&1)
if [[ $? -ne 0 && "$output" == *"Cannot read"* ]]; then
  printf '  ok   %s\n' "an unresolvable range is refused, not called empty"
  passed=$((passed + 1))
else
  printf '  FAIL %s\n' "an unresolvable range is refused, not called empty"
  printf '       got: %s\n' "$output"
  failed=$((failed + 1))
fi

scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-epic","status":"open"}
{"_type":"issue","id":"inventory-tng-aaa","status":"closed","dependencies":[{"issue_id":"inventory-tng-aaa","depends_on_id":"inventory-tng-epic","type":"parent-child"}]}
{"_type":"issue","id":"inventory-tng-bbb","status":"closed","dependencies":[{"issue_id":"inventory-tng-bbb","depends_on_id":"inventory-tng-epic","type":"parent-child"}]}
JSONL
land a 'aaa: Extract the decode loop

Closes inventory-tng-aaa'
land b 'bbb: Read the label map from the cache

Closes inventory-tng-bbb'
expect 0 "batch epic: inventory-tng-epic" "the batch is read from the committed tracker"

scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-epic","status":"open"}
{"_type":"issue","id":"inventory-tng-aaa","status":"closed","dependencies":[{"issue_id":"inventory-tng-aaa","depends_on_id":"inventory-tng-epic","type":"parent-child"}]}
{"_type":"issue","id":"inventory-tng-bbb","status":"open","dependencies":[{"issue_id":"inventory-tng-bbb","depends_on_id":"inventory-tng-epic","type":"parent-child"}]}
JSONL
land a 'aaa: Extract the decode loop

Closes inventory-tng-aaa'
expect 1 "in the batch but not landed here: inventory-tng-bbb" "a batch half landed is refused"

# An issue shipping on its own belongs to no batch and needs no epic.
scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","status":"closed"}
JSONL
land a 'aaa: Extract the decode loop

Closes inventory-tng-aaa'
expect 0 "One commit, one issue" "a lone issue needs no epic"

# See batch-membership.py on why a malformed row is stepped over.
scene
tracker <<'JSONL'
not json at all
{"_type":"issue","id":"inventory-tng-aaa","status":"closed"}
{"_type":"issue","id":"inventory-tng-bbb","dependencies":[{"type":"parent-child"}]}
JSONL
land a 'aaa: Extract the decode loop

Closes inventory-tng-aaa'
expect 0 "One commit, one issue" "a malformed tracker line is stepped over"

scene
expect 0 "Nothing to check" "an empty range is not a failure"

echo
if [[ "$failed" -eq 0 ]]; then
  echo "$passed passed."
  exit 0
fi
echo "$failed failed, $passed passed."
exit 1
