#!/usr/bin/env bash
# What check-commit.sh must say, and about what.
#
# A throwaway repository per case, because the thing under test reads a real
# staged diff and a real tracker file -- and because a test that ran against
# this repository would be one `bd close` away from meaning something else.
#
# Usage: scripts/check-commit.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
CHECK="$HERE/check-commit.sh"
. "$HERE/testlib.sh"
workspace

# A repository holding two issues in progress and one closed long ago, so that
# a rewritten closed row has something to be distinguished from.
scene() {
  new_repo "$WORK/repo"
  mkdir -p "$WORK/repo/.beads"
  cd "$WORK/repo" || exit 1
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

good_message() {
  message <<'MSG'
aaa: Extract the decode loop into its own module

Moves the 5 Hz loop out of the component and into decodeLoop.ts.

Closes: inventory-tng-aaa
MSG
}

# THE ORDINARY TRACKER for a commit that closes aaa: aaa closed, bbb still in
# progress, and a row closed long before any of this. Twenty cases wanted
# exactly these three rows and each wrote them out, which is sixty lines of
# identical JSON standing between a reader and what each case is actually
# about.
#
# A case that varies a row keeps its own heredoc rather than parameterising
# this one. The difference is then the thing on the page, instead of something
# to be found by diffing two near-identical blocks. inventory-tng-rjga.
closes_aaa() {
  tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","title":"one","status":"closed"}
{"_type":"issue","id":"inventory-tng-bbb","title":"two","status":"in_progress"}
{"_type":"issue","id":"inventory-tng-old","title":"done long ago","status":"closed"}
JSONL
}

check "$CHECK" "$WORK/repo/message"

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
closes_aaa
message <<'MSG'
Extract the decode loop into its own module

Closes: inventory-tng-bbb
MSG
expect 1 "closes inventory-tng-bbb but the staged tracker closes inventory-tng-aaa" \
  "a trailer naming another issue is refused"

scene
good_message
expect 1 "nothing staged closes inventory-tng-aaa" "a trailer with nothing staged is refused"

scene
closes_aaa
message <<'MSG'
Refactored the decode loop so that it could be tested properly.
The body starts here with no blank line, and runs on well past seventy-two columns.

Closes: inventory-tng-aaa
MSG
expect 1 "is not the imperative" "the past tense is refused"
expect 1 "over 50" "an over-long summary is refused"
expect 1 "ends in a full stop" "a full stop is refused"
expect 1 "must be blank" "a missing blank second line is refused"
expect 1 "over 72" "an over-long body line is refused"

scene
message <<'MSG'
Read the label map from the cache

Closes: inventory-tng-aaa
MSG
expect 1 "nothing staged closes" "an imperative that ends in -ed is not mistaken for a tense"
output=$("$CHECK" "$WORK/repo/message" 2>&1)
refute "$output" $? 1 "not the imperative" "\"Read\" is left alone"

# Replacing the last commit: what lands is the staged changes and that one's,
# so a closure it already carries still counts, exactly once.
scene
closes_aaa
git -C "$WORK/repo" commit -q -m landed
good_message
expect 1 "nothing staged closes inventory-tng-aaa" "an amend rewriting the summary is refused"
expect --amend 0 "closes inventory-tng-aaa" "an amend sees the closure its commit already carries"

# --- an amend git cannot tell a commit-msg hook about ----------------------
#
# inventory-tng-h0hr. The flag used above is not available to a hook -- see
# `amends_head` in check-commit.sh for what git does and does not pass one --
# so the shape has to be recognised from HEAD instead. These cases are as much
# about what that must go on refusing as about what it newly accepts.

# The plain shape: nothing staged at all, because the index already equals HEAD.
scene
closes_aaa
git -C "$WORK/repo" commit -q -m "aaa: Extract the decode loop into its own module"
good_message
expect 0 "amends the commit that closed inventory-tng-aaa" "an amend whose summary is HEAD's is read as one"

# The shape that actually bites, and the reason this is P1: beads re-exports
# the tracker on every commit, so the amend stages a tracker that HAS changed
# and closes nothing. The refusal that produced was "issues.jsonl is staged but
# does not close inventory-tng-614", on the commit that had closed it.
scene
closes_aaa
git -C "$WORK/repo" commit -q -m "aaa: Extract the decode loop into its own module"
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","title":"one","status":"closed"}
{"_type":"issue","id":"inventory-tng-bbb","title":"two, and now commented on","status":"in_progress"}
{"_type":"issue","id":"inventory-tng-old","title":"done long ago","status":"closed"}
JSONL
good_message
expect 0 "amends the commit that closed inventory-tng-aaa" "and so is one whose tracker was re-exported without closing anything"

# THE NARROWING, which is the whole reason the summary is compared at all: a
# follow-up that stages nothing and claims the closure the commit before it
# made. Indistinguishable from a reword on the evidence available, so both are
# refused rather than the rule being weakened to admit one of them.
scene
closes_aaa
git -C "$WORK/repo" commit -q -m "aaa: Extract the decode loop into its own module"
message <<'MSG'
aaa: Tidy up after the decode loop extraction

Closes: inventory-tng-aaa
MSG
expect 1 "nothing staged closes inventory-tng-aaa" "a second commit claiming the same closure is still refused"
expect 1 "--fixup=reword:" "and the refusal names the spelling that changes a summary"

# AND THE LIMIT OF THE NARROWING, asserted here so that it is a fact of the
# suite rather than a claim in a comment. The same follow-up, with HEAD's
# subject copied instead of its own, is accepted. check-commit.sh says why
# nothing handed to a commit-msg hook can separate the two, and check-batch.sh
# is what refuses the pair over the range.
scene
closes_aaa
git -C "$WORK/repo" commit -q -m "aaa: Extract the decode loop into its own module"
echo "work of an entirely different kind" > "$WORK/repo/elsewhere.txt"
git -C "$WORK/repo" add -A
good_message
expect 0 "amends the commit that closed inventory-tng-aaa" \
  "a follow-up that copies HEAD's summary is not caught here"

# The other half of "both, never either alone": the summary matches, but the
# issue this message names is not the one HEAD closed.
scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","title":"one","status":"in_progress"}
{"_type":"issue","id":"inventory-tng-bbb","title":"two","status":"closed"}
{"_type":"issue","id":"inventory-tng-old","title":"done long ago","status":"closed"}
JSONL
git -C "$WORK/repo" commit -q -m "aaa: Extract the decode loop into its own module"
good_message
expect 1 "nothing staged closes inventory-tng-aaa" "a matching summary alone is not enough when HEAD closed something else"

# --- when the interpreter is not there -------------------------------------
#
# inventory-tng-pg63. The tracker half is read by python3, and its exit status
# used to be discarded by `mapfile < <(...)`: a broken interpreter produced no
# closures, so an honest commit was told it closed nothing, with python3
# mentioned nowhere. Missing and broken take the same path, so a shim that
# exits non-zero stands for both.

scene
closes_aaa
good_message
expect 0 "closes inventory-tng-aaa" "with a working python3 the closure is read"

mkdir -p "$WORK/broken"
printf '#!/bin/sh\nexit 1\n' > "$WORK/broken/python3"
chmod +x "$WORK/broken/python3"

output=$(PATH="$WORK/broken:$PATH" "$CHECK" "$WORK/repo/message" 2>&1)
status=$?
assert "$output" "$status" 2 "python3 could not read" "a python3 that cannot answer says so"
refute "$output" "$status" 2 "does not close" "and does not accuse the commit instead"

# Nothing staged for the tracker means nothing for python3 to read, so the
# message half stands on its own -- and is not held hostage to an interpreter
# it never needed.
scene
good_message
output=$(PATH="$WORK/broken:$PATH" "$CHECK" "$WORK/repo/message" 2>&1)
assert "$output" $? 1 "nothing staged closes" "a commit staging no tracker change needs no python3"

# The other diff python3 is asked to read is HEAD's, in `amends_head`, and it
# was left running through a pipeline whose status nothing looked at -- the
# same discard, in the same file, as the one above. A broken interpreter made
# an honest amend look like a commit that closed nothing.
scene
closes_aaa
git -C "$WORK/repo" commit -q -m "aaa: Extract the decode loop into its own module"
good_message
output=$(PATH="$WORK/broken:$PATH" "$CHECK" "$WORK/repo/message" 2>&1)
status=$?
assert "$output" "$status" 2 "python3 could not read" "an amend read against a broken python3 says so as well"
refute "$output" "$status" 2 "nothing staged closes" "and is not told it closed nothing instead"

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

Closes: inventory-tng-aaa
MSG
expect --message-only 1 "not the imperative" "and the message rules still apply"

# The filter this replaced discarded any objection whose wording mentioned
# staging, so an objection that happens to use the word must survive.
scene
message <<'MSG'
aaa: Note what the tracker says about staged work here

Closes: inventory-tng-aaa
Closes: inventory-tng-bbb
MSG
expect --message-only 1 "2 'Closes' trailers" "an objection whose wording mentions staging survives"

scene
message <<'MSG'
Merge branch 'batch/catalogue-write-api'
MSG
expect 0 "Nothing to check" "a merge is not somebody's issue being landed"

# --- git's own commits, found by the file rather than by the subject -------
#
# inventory-tng-wr9o. A merge or a revert committed with a custom -m, and any
# conflicted cherry-pick, has a subject nobody can recognise, so the only thing
# that identifies it is the file git leaves beside HEAD. These subjects are
# deliberately NOT ones `message_is_git_own` catches, and carry no trailer, so
# the escape is the only thing that can produce a pass.

# The path is derived from the .git file rather than from `git rev-parse
# --git-path`, which is the thing under test -- a test that asked the same
# question the same way would agree with a wrong answer.
worktree_gitdir() { sed 's/^gitdir: //' "$1/.git"; }

scene
git -C "$WORK/repo" worktree add -q -b side "$WORK/side"
cd "$WORK/side" || exit 1
message <<'MSG'
Bring the catalogue branch in by hand
MSG
expect 1 "trailer, found none" "in a worktree, an ordinary message is still held to the rules"

: > "$(worktree_gitdir "$WORK/side")/MERGE_HEAD"
expect 0 "Nothing to check" "and a conflicted merge in that worktree is git's own"

rm -f "$(worktree_gitdir "$WORK/side")/MERGE_HEAD"
: > "$(worktree_gitdir "$WORK/side")/CHERRY_PICK_HEAD"
expect 0 "Nothing to check" "so is a conflicted cherry-pick there"

# The main checkout, where .git is a directory: the same escape, unregressed.
scene
: > "$WORK/repo/.git/MERGE_HEAD"
message <<'MSG'
Bring the catalogue branch in by hand
MSG
expect 0 "Nothing to check" "and the main checkout still finds it where it always did"
rm -f "$WORK/repo/.git/MERGE_HEAD"

# The subjects git writes for a commit that is not finished being written. Held
# to the rules for a message somebody composed, every one of them is refused --
# no trailer, and a subject six characters longer than one that already fitted
# -- which is the whole of answering a review comment.
scene
message <<'MSG'
fixup! aaa: Extract the decode loop into its own module
MSG
expect 0 "Nothing to check" "a fixup is the commit it will be folded into, not a new one"

scene
message <<'MSG'
squash! aaa: Extract the decode loop into its own module
MSG
expect 0 "Nothing to check" "and so is a squash"

scene
message <<'MSG'
amend! aaa: Extract the decode loop into its own module
MSG
expect 0 "Nothing to check" "and an amend! written by rebase --autosquash"

# Not any line that merely starts with the word. The exemption is for the
# subjects git composes, which carry the bang.
scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","title":"one","status":"in_progress"}
{"_type":"issue","id":"inventory-tng-bbb","title":"two","status":"in_progress"}
{"_type":"issue","id":"inventory-tng-old","title":"done long ago","status":"closed"}
JSONL
message <<'MSG'
fixup the decode loop
MSG
expect 1 "found none" "a summary that merely begins with the word is still somebody's message"

scene
closes_aaa
message <<'MSG'
Extract the decode loop into its own module

Closes: #123
MSG
expect 0 "not a bead" "a GitHub issue is accepted without a tracker to check"

scene
closes_aaa
message <<'MSG'
Extract the decode loop into its own module

No trailer at all.
MSG
expect 1 "found none" "a message naming no issue is refused"

# --- the identifier in the summary, and issues that take more than one commit

scene
closes_aaa
message <<'MSG'
aaa: Extract the decode loop into its own module

Closes: inventory-tng-aaa
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
closes_aaa
message <<'MSG'
aaa: Extract the decode loop into its own new module

Closes: inventory-tng-aaa
MSG
expect 0 "Nothing to object to" "a prefix its trailer confirms is not charged for"

scene
closes_aaa
message <<'MSG'
bbb: Extract the decode loop into its own new module

Closes: inventory-tng-aaa
MSG
expect 1 "over 50" "a prefix naming another issue is prose, and is charged for"

scene
closes_aaa
message <<'MSG'
aaa: Extract the decode loop into a module that is far too long to fit

Closes: inventory-tng-aaa
MSG
expect 1 "over 50" "an over-long summary is still refused with an identifier"

# An issue may take more than one commit, so a message that only advances one
# closes nothing and has nothing to cross-check.
scene
message <<'MSG'
aaa: Move the loop before rewriting it

Refs: inventory-tng-aaa
MSG
expect 0 "names inventory-tng-aaa without closing it" "Refs advances an issue without closing it"

scene
closes_aaa
message <<'MSG'
aaa: Move the loop before rewriting it

Refs: inventory-tng-aaa
MSG
expect 1 "closes nothing but the staged tracker closes" "Refs while the tracker closes something is refused"

scene
closes_aaa
message <<'MSG'
aaa: Extract the decode loop

Closes: inventory-tng-aaa
Refs: inventory-tng-bbb
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

Closes: inventory-tng-aaa
Closes: inventory-tng-bbb
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
  pass "the embedded program is not indented, and parses"
else
  fail_case "the embedded program is not indented, and parses"
fi

# Every case above runs the script where it lives, so none of them can see what
# breaks when it is installed as a hook instead. See check-commit.sh on why the
# path is resolved before anything is sourced from beside it.
scene
ln -sfn "$CHECK" "$WORK/repo/hooked"
good_message
closes_aaa
output=$("$WORK/repo/hooked" "$WORK/repo/message" 2>&1)
assert "$output" $? 0 "Nothing to object to" "it works through a symlink, as the hook install makes it"

# --- a trailer git can read ------------------------------------------------
#
# See trailers.sh for what git requires and DEVELOPERS.md#commits for why.

scene
closes_aaa
message <<'MSG'
aaa: Extract the decode loop

Closes inventory-tng-aaa
MSG
expect 1 "is not a trailer git can read" "a trailer without the colon is refused"

# It is still read, so a range of history written before the convention is not
# invisible to check-batch.sh.
scene
message <<'MSG'
aaa: Extract the decode loop

Closes inventory-tng-aaa
MSG
output=$("$CHECK" --message-only "$WORK/repo/message" 2>&1)
assert "$output" $? 1 "not a trailer git can read" "the colonless form is still recognised as naming an issue"
refute "$output" 1 1 "found none" "and is not mistaken for no trailer at all"

# What the rule is for: git itself must agree that it is a trailer.
scene
closes_aaa
good_message
if [[ "$(git interpret-trailers --parse < "$WORK/repo/message")" == "Closes: inventory-tng-aaa" ]]; then
  pass "git interpret-trailers agrees the message carries the trailer"
else
  fail_case "git interpret-trailers agrees the message carries the trailer" \
    "$(git interpret-trailers --parse < "$WORK/repo/message")"
fi

# See trailers.sh: the colon is only half of it, and a message that says more
# after the trailers yields nothing from git either.
scene
message <<'MSG'
aaa: Extract the decode loop

Closes: inventory-tng-aaa

And then a closing thought that pushes the trailer up the message.
MSG
expect --message-only 1 "not the last paragraph" "a trailer with prose after it is refused"
refute "$(git interpret-trailers --parse < "$WORK/repo/message")" 0 0 "Closes" \
  "and git agrees it finds no trailer there"

# An epic groups a batch and does no work of its own, so its closure riding
# with the last issue of that batch is bookkeeping rather than a second unit.
scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","title":"one","status":"closed"}
{"_type":"issue","id":"inventory-tng-bbb","title":"two","status":"in_progress"}
{"_type":"issue","id":"inventory-tng-old","title":"done long ago","status":"closed"}
{"_type":"issue","id":"inventory-tng-epic","title":"the batch","issue_type":"epic","status":"closed"}
JSONL
good_message
expect 0 "Nothing to object to" "an epic closing alongside its last issue is one commit"

# Two epics is still not two units of work.
scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","title":"one","status":"closed"}
{"_type":"issue","id":"inventory-tng-bbb","title":"two","status":"in_progress"}
{"_type":"issue","id":"inventory-tng-old","title":"done long ago","status":"closed"}
{"_type":"issue","id":"inventory-tng-e1","title":"a batch","issue_type":"epic","status":"closed"}
{"_type":"issue","id":"inventory-tng-e2","title":"another","issue_type":"epic","status":"closed"}
JSONL
good_message
expect 0 "Nothing to object to" "several epics are still not work"

# And two real issues still are.
scene
tracker <<'JSONL'
{"_type":"issue","id":"inventory-tng-aaa","title":"one","status":"closed"}
{"_type":"issue","id":"inventory-tng-bbb","title":"two","status":"closed"}
{"_type":"issue","id":"inventory-tng-old","title":"done long ago","status":"closed"}
{"_type":"issue","id":"inventory-tng-epic","title":"the batch","issue_type":"epic","status":"closed"}
JSONL
good_message
expect 1 "2 issues are closed here" "an epic does not excuse two issues"

# One run, every long line. Stopping at the first meant three runs for three
# lines, and the later fixes were written against a report naming neither.
scene
message <<'MSG'
aaa: Extract the decode loop

This first body line is deliberately far too long to fit inside the limit.

And this second one is as well, which the old checker would never mention.

Closes: inventory-tng-aaa
MSG
output=$("$CHECK" --message-only "$WORK/repo/message" 2>&1)
assert "$output" $? 1 "2 body lines are over 72" "every over-long line is counted, not just the first"
assert "$output" 1 1 "This first body line" "the first long line is named"
assert "$output" 1 1 "And this second one" "and so is the second"

scene
message <<'MSG'
aaa: Extract the decode loop

Only this one body line is far too long to fit inside the limit that is set.

Closes: inventory-tng-aaa
MSG
expect --message-only 1 "one body line is over 72" "one is singular"

verdict
