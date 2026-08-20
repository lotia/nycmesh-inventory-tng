#!/usr/bin/env bash
# What check-commit.sh must say, and about what.
#
# A throwaway repository per case, because the thing under test reads a real
# staged diff and a real tracker file -- and because a test that ran against
# this repository would be one `bd close` away from meaning something else.
#
# Usage: scripts/check-commit.test.sh

set -uo pipefail

CHECK=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/check-commit.sh
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

passed=0
failed=0

# A repository holding two issues in progress and one closed long ago, so that
# a rewritten closed row has something to be distinguished from.
scene() {
  rm -rf "$WORK/repo"
  mkdir -p "$WORK/repo/.beads"
  cd "$WORK/repo" || exit 1
  git init -q .
  git config user.email test@example.invalid
  git config user.name Test
  cat > .beads/issues.jsonl <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","title":"one","status":"in_progress"}
{"_type":"issue","id":"inventory-tng-bbb","title":"two","status":"in_progress"}
{"_type":"issue","id":"inventory-tng-old","title":"done long ago","status":"closed"}
JSONL
  git add -A
  git commit -q -m "scene"
}

tracker() {
  cat > "$WORK/repo/.beads/issues.jsonl"
  git -C "$WORK/repo" add -A
}

message() {
  cat > "$WORK/repo/message"
}

# expect [--amend] <exit status> <substring> <what this case is called>
expect() {
  local flag=()
  while [[ "$1" == --* ]]; do
    flag+=("$1")
    shift
  done
  local want_status=$1 want_text=$2 name=$3 output status
  output=$("$CHECK" "${flag[@]}" "$WORK/repo/message" 2>&1)
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

good_message() {
  message <<'MSG'
Extract the decode loop into its own module

Moves the 5 Hz loop out of the component and into decodeLoop.ts.

Closes inventory-tng-aaa
MSG
}

echo "check-commit.sh"

scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","title":"one","status":"closed"}
{"_type":"issue","id":"inventory-tng-bbb","title":"two","status":"in_progress"}
{"_type":"issue","id":"inventory-tng-old","title":"done long ago, and commented on","status":"closed"}
JSONL
good_message
expect 0 "Nothing to object to" "one closure, and an already-closed row rewritten, is one commit"

scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","title":"one","status":"closed"}
{"_type":"issue","id":"inventory-tng-bbb","title":"two","status":"closed"}
{"_type":"issue","id":"inventory-tng-old","title":"done long ago","status":"closed"}
JSONL
good_message
expect 1 "2 issues are closed here" "two closures are refused"

scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","title":"one","status":"closed"}
{"_type":"issue","id":"inventory-tng-bbb","title":"two","status":"in_progress"}
{"_type":"issue","id":"inventory-tng-old","title":"done long ago","status":"closed"}
{"_type":"issue","id":"inventory-tng-new","title":"noticed on the way","status":"open"}
JSONL
good_message
expect 0 "Nothing to object to" "raising a follow-up alongside one closure is allowed"

scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","title":"one","status":"closed"}
{"_type":"issue","id":"inventory-tng-bbb","title":"two","status":"in_progress"}
{"_type":"issue","id":"inventory-tng-old","title":"done long ago","status":"closed"}
JSONL
message <<'MSG'
Extract the decode loop into its own module

Closes inventory-tng-bbb
MSG
expect 1 "closes inventory-tng-bbb but the staged tracker closes inventory-tng-aaa" \
  "a trailer naming another issue is refused"

scene
good_message
expect 1 "nothing staged closes inventory-tng-aaa" "a trailer with nothing staged is refused"

scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","title":"one","status":"closed"}
{"_type":"issue","id":"inventory-tng-bbb","title":"two","status":"in_progress"}
{"_type":"issue","id":"inventory-tng-old","title":"done long ago","status":"closed"}
JSONL
message <<'MSG'
Refactored the decode loop so that it could be tested properly.
The body starts here with no blank line, and runs on well past seventy-two columns.

Closes inventory-tng-aaa
MSG
expect 1 "is not the imperative" "the past tense is refused"
expect 1 "over 50" "an over-long summary is refused"
expect 1 "ends in a full stop" "a full stop is refused"
expect 1 "must be blank" "a missing blank second line is refused"
expect 1 "over 72" "an over-long body line is refused"

scene
message <<'MSG'
Read the label map from the cache

Closes inventory-tng-aaa
MSG
expect 1 "nothing staged closes" "an imperative that ends in -ed is not mistaken for a tense"
output=$("$CHECK" "$WORK/repo/message" 2>&1)
if [[ "$output" == *"not the imperative"* ]]; then
  printf '  FAIL %s\n' "\"Read\" was flagged as a tense"
  failed=$((failed + 1))
else
  printf '  ok   %s\n' "\"Read\" is left alone"
  passed=$((passed + 1))
fi

# Replacing the last commit: what lands is the staged changes and that one's,
# so a closure it already carries still counts, exactly once.
scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","title":"one","status":"closed"}
{"_type":"issue","id":"inventory-tng-bbb","title":"two","status":"in_progress"}
{"_type":"issue","id":"inventory-tng-old","title":"done long ago","status":"closed"}
JSONL
git -C "$WORK/repo" commit -q -m landed
good_message
expect 1 "nothing staged closes inventory-tng-aaa" "an amend without the flag cannot see the closure"
expect --amend 0 "closes inventory-tng-aaa" "an amend sees the closure its commit already carries"

# --- reading a commit that has already landed ------------------------------
#
# check-batch.sh asks for this over history, where there is no staged diff for
# the tracker half to read. It must be the message rules and nothing else.

scene
good_message
expect --message-only 0 "Nothing to object to" "the message rules pass with nothing staged"

scene
message <<'MSG'
Extracted the decode loop

Closes inventory-tng-aaa
MSG
expect --message-only 1 "not the imperative" "and the message rules still apply"

# The filter this replaced discarded any objection whose wording mentioned
# staging, so an objection that happens to use the word must survive.
scene
message <<'MSG'
aaa: Note what the tracker says about staged work here

Closes inventory-tng-aaa
Closes inventory-tng-bbb
MSG
expect --message-only 1 "2 'Closes' trailers" "an objection whose wording mentions staging survives"

scene
message <<'MSG'
Merge branch 'batch/catalogue-write-api'
MSG
expect 0 "Nothing to check" "a merge is not somebody's issue being landed"

scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","title":"one","status":"closed"}
{"_type":"issue","id":"inventory-tng-bbb","title":"two","status":"in_progress"}
{"_type":"issue","id":"inventory-tng-old","title":"done long ago","status":"closed"}
JSONL
message <<'MSG'
Extract the decode loop into its own module

Closes #123
MSG
expect 0 "not a bead" "a GitHub issue is accepted without a tracker to check"

scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","title":"one","status":"closed"}
{"_type":"issue","id":"inventory-tng-bbb","title":"two","status":"in_progress"}
{"_type":"issue","id":"inventory-tng-old","title":"done long ago","status":"closed"}
JSONL
message <<'MSG'
Extract the decode loop into its own module

No trailer at all.
MSG
expect 1 "found none" "a message naming no issue is refused"

# --- the identifier in the summary, and issues that take more than one commit

scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","title":"one","status":"closed"}
{"_type":"issue","id":"inventory-tng-bbb","title":"two","status":"in_progress"}
{"_type":"issue","id":"inventory-tng-old","title":"done long ago","status":"closed"}
JSONL
message <<'MSG'
aaa: Extract the decode loop into its own module

Closes inventory-tng-aaa
MSG
expect 0 "Nothing to object to" "an identifier is not charged against the 50"

scene
message <<'MSG'
Fix: the decode loop, out where it can be tested, and then some more
MSG
expect 1 "over 50" "a prefix no trailer confirms is prose, and is charged for"

# What makes a prefix an identifier is that the trailer agrees. The same line
# is under the limit when it names the issue it belongs to and over it when it
# names something else, which is the whole distinction in one pair of cases.
scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","title":"one","status":"closed"}
{"_type":"issue","id":"inventory-tng-bbb","title":"two","status":"in_progress"}
{"_type":"issue","id":"inventory-tng-old","title":"done long ago","status":"closed"}
JSONL
message <<'MSG'
aaa: Extract the decode loop into its own new module

Closes inventory-tng-aaa
MSG
expect 0 "Nothing to object to" "a prefix its trailer confirms is not charged for"

scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","title":"one","status":"closed"}
{"_type":"issue","id":"inventory-tng-bbb","title":"two","status":"in_progress"}
{"_type":"issue","id":"inventory-tng-old","title":"done long ago","status":"closed"}
JSONL
message <<'MSG'
bbb: Extract the decode loop into its own new module

Closes inventory-tng-aaa
MSG
expect 1 "over 50" "a prefix naming another issue is prose, and is charged for"

scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","title":"one","status":"closed"}
{"_type":"issue","id":"inventory-tng-bbb","title":"two","status":"in_progress"}
{"_type":"issue","id":"inventory-tng-old","title":"done long ago","status":"closed"}
JSONL
message <<'MSG'
aaa: Extract the decode loop into a module that is far too long to fit

Closes inventory-tng-aaa
MSG
expect 1 "over 50" "an over-long summary is still refused with an identifier"

# An issue may take more than one commit, so a message that only advances one
# closes nothing and has nothing to cross-check.
scene
message <<'MSG'
aaa: Move the loop before rewriting it

Refs inventory-tng-aaa
MSG
expect 0 "names inventory-tng-aaa without closing it" "Refs advances an issue without closing it"

scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","title":"one","status":"closed"}
{"_type":"issue","id":"inventory-tng-bbb","title":"two","status":"in_progress"}
{"_type":"issue","id":"inventory-tng-old","title":"done long ago","status":"closed"}
JSONL
message <<'MSG'
aaa: Move the loop before rewriting it

Refs inventory-tng-aaa
MSG
expect 1 "closes nothing but the staged tracker closes" "Refs while the tracker closes something is refused"

scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","title":"one","status":"closed"}
{"_type":"issue","id":"inventory-tng-bbb","title":"two","status":"in_progress"}
{"_type":"issue","id":"inventory-tng-old","title":"done long ago","status":"closed"}
JSONL
message <<'MSG'
aaa: Extract the decode loop

Closes inventory-tng-aaa
Refs inventory-tng-bbb
MSG
expect 1 "A commit belongs to one issue" "trailers naming two issues are refused"

scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","title":"one","status":"closed"}
{"_type":"issue","id":"inventory-tng-bbb","title":"two","status":"closed"}
{"_type":"issue","id":"inventory-tng-old","title":"done long ago","status":"closed"}
JSONL
message <<'MSG'
aaa: Extract the decode loop

Closes inventory-tng-aaa
Closes inventory-tng-bbb
MSG
expect 1 "2 'Closes' trailers" "two closing trailers are refused"

# The inline program is passed to `python3 -c`, where a leading indent is an
# IndentationError on every version before 3.14 -- and 3.14 accepts it, so a
# developer on a current Python cannot see the breakage their CI will. Indenting
# it is what a tidy-up of the surrounding bash does by accident.
if python3 - "$CHECK" <<'GUARD'; then
import ast, pathlib, re, sys

source = pathlib.Path(sys.argv[1]).read_text()
for program in re.findall(r"python3 -c '(.*?)'\)", source, re.S):
    first = program.lstrip("\n").split("\n")[0]
    if first.startswith((" ", "\t")):
        raise SystemExit("an embedded program is indented: " + repr(first[:40]))
    ast.parse(program)
GUARD
  printf '  ok   %s\n' "the embedded program is not indented, and parses"
  passed=$((passed + 1))
else
  printf '  FAIL %s\n' "the embedded program is not indented, and parses"
  failed=$((failed + 1))
fi

echo
if [[ "$failed" -eq 0 ]]; then
  echo "$passed passed."
  exit 0
fi
echo "$failed failed, $passed passed."
exit 1
