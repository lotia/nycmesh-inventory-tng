#!/usr/bin/env bash
# What say-bead.sh writes to a public repository, and what it leaves alone.
#
# Every case here is about restraint, because the subject writes to issues
# strangers read. What it must never do is add a second comment where it already
# has one, write again when nothing has changed, or leave a comment standing
# after the bead behind it stopped saying anything.
#
# The `gh` stub records what it was asked to do rather than answering
# realistically, because the assertions are all about which calls happen: this
# script's whole behaviour is a choice between PATCH, POST, DELETE and doing
# nothing.
#
# Usage: scripts/say-bead.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/testlib.sh"
workspace

# Unset for the reason pull-new-issues.test.sh gives about the same variable.
unset GITHUB_REPOSITORY

# Checked against the script rather than guessed. `sleep` is in the write loop
# and `awk` is behind --only.
#
# `python3` IS NOT BORROWED, and that is not an omission: the scene wraps it
# below, and a borrowed tool is a SYMLINK TO THE REAL PROGRAM -- so `cat >` over
# one writes straight through it and truncates the interpreter on whatever
# machine is running the suite. Anything this file stubs must be absent from
# this list, which is why `gh` is missing from it too.
BORROWED=(bash readlink dirname git wc tr tail awk mktemp rm sleep jq)

# Where the real one is, read before the scene narrows PATH to $BIN.
REAL_PYTHON3=$(command -v python3)

BIN="$WORK/bin"
REPO="$WORK/repo"

new_repo "$REPO"
mkdir -p "$REPO/scripts" "$REPO/.beads"
cp "$HERE/say-bead.sh" "$HERE/unsaid.py" "$HERE/unsynced.py" "$HERE/report.sh" \
   "$HERE/repository.sh" "$REPO/scripts/"
borrow "$BIN" "${BORROWED[@]}"

EXPORT="$REPO/.beads/issues.jsonl"
OURS="https://github.com/o/r"

# A `gh` that answers the one listing from a file and writes every other call
# down. $GH_COMMENTS holds the API's own shape, so the jq in the script under
# test is exercised rather than stubbed past.
cat > "$BIN/gh" <<'STUB'
#!/usr/bin/env bash
case "$*" in
  *"repo view"*) echo "o/r"; exit 0 ;;
esac
printf '%s\n' "$*" >> "$GH_CALLS"
# $GH_DEAD fails EVERY call including the listing, which is the point: a stub
# whose listing always answers cannot exercise what the script does when GitHub
# will not say what is already there.
[[ -s "$GH_DEAD" ]] && exit 1
case "$*" in
  # `$(<file)` rather than `cat`, so BORROWED above stays a true statement about
  # what the SCRIPT reaches for rather than a list padded out for the harness.
  *"issues/comments --paginate"*) printf '%s\n' "$(<"$GH_COMMENTS")" ;;
  *) echo '{}' ;;
esac
STUB
chmod +x "$BIN/gh"
export GH_COMMENTS="$WORK/comments" GH_CALLS="$WORK/calls" GH_DEAD="$WORK/dead"

# A `sleep` THAT DOES NOT. The script waits a second between writes for GitHub's
# secondary limits and the cases below make dozens, so a scene that really
# waited would put minutes into a CI step to prove something no case asserts.
#
# WRITTEN ELSEWHERE AND LINKED OVER, WHICH IS NOT FUSSINESS. `borrow` leaves a
# SYMLINK to the real program, so `> "$BIN/sleep"` opens it, follows the link and
# TRUNCATES THE PROGRAM ITSELF -- outside the workspace, permanently, and
# silently if the file happens to be writable. It cost a 32MB interpreter in one
# session. `ln -sf` over the top replaces the link and touches nothing it points
# at, which is what borrow's own header says to do.
printf '#!/usr/bin/env bash\nexit 0\n' > "$WORK/no-sleep"
chmod +x "$WORK/no-sleep"
ln -sf "$WORK/no-sleep" "$BIN/sleep"

# A `python3` that is CHATTY ON STDERR AND STILL SUCCEEDS -- a deprecation
# warning, a locale grumble -- which is the input that decides whether stderr
# can reach a comment body. sync-issues.test.sh carries the same knob for `gh`
# and for the same reason: neither script's success path was reachable with it
# before, so every case passed on output the real thing does not always produce.
#
# `$(<file)` and no `cat`, so BORROWED above stays a true statement about what
# the SCRIPT reaches for, the same way the `gh` stub does.
cat > "$BIN/python3" <<STUB
#!/usr/bin/env bash
[[ -s "\$PY_NOISE" ]] && printf '%s\n' "\$(<"\$PY_NOISE")" >&2
exec "$REAL_PYTHON3" "\$@"
STUB
chmod +x "$BIN/python3"
export PY_NOISE="$WORK/py-noise"
: > "$PY_NOISE"

# already <issue number> <comment id> <first line>... -- what GitHub is holding,
# in the shape --paginate --slurp returns: a list of pages, each a list.
already() {
  python3 - "$@" <<'PY' > "$GH_COMMENTS"
import json, sys
out = []
args = sys.argv[1:]
for i in range(0, len(args), 3):
    number, cid, line = args[i], args[i + 1], args[i + 2]
    out.append({"id": int(cid),
                "issue_url": f"https://api.github.com/repos/o/r/issues/{number}",
                "body": line + "\nbody text"})
print(json.dumps([out]))
PY
}

# marker_for <bead> -- the first line unsaid.py would write for it, which is
# what a comment already saying the same thing would carry.
marker_for() {
  (cd "$REPO" && PATH="$BIN" python3 scripts/unsaid.py .beads/issues.jsonl o/r "$1" | head -1)
}

say() { : > "$GH_CALLS"; (cd "$REPO" && PATH="$BIN" ./scripts/say-bead.sh "$@") 2>&1; }
calls() { cat "$GH_CALLS"; }
check say

echo "putting it there the first time"

rich inventory-tng-aaa '{"acceptance_criteria":"The suite is green."}' "$OURS/issues/60" >"$EXPORT"
already
out=$(say); status=$?
assert "$out" "$status" 0 "1 written" "a bead with something to say gets a comment"
assert "$(calls)" 0 0 "POST repos/o/r/issues/60/comments" "posted to its own issue, and created rather than edited"

echo
echo "and never a second one"
# THE FAILURE THIS EXISTS TO STOP. Run twice with no marker to find, this would
# leave two comments on every issue, then three.

already 60 900 "$(marker_for inventory-tng-aaa)"
out=$(say); status=$?
assert "$out" "$status" 0 "already saying it" "a comment that already says this is left alone"
refute "$(calls)" 0 0 "POST" "so nothing is posted a second time"
refute "$(calls)" 0 0 "PATCH" "and nothing is rewritten either"

echo
echo "when the bead changes"

already 60 900 "<!-- bead-fields 00000000 -->"
out=$(say); status=$?
assert "$out" "$status" 0 "1 written" "a comment saying something else is brought up to date"
assert "$(calls)" 0 0 "PATCH repos/o/r/issues/comments/900" "in place, by its own comment id"
refute "$(calls)" 0 0 "POST" "rather than added beside the old one"

echo
echo "when the bead stops saying anything"
# The case that decided unsaid.py lists the silent beads at all: without this
# the comment stands for ever, saying what the tracker no longer does.

rich inventory-tng-aaa '{}' "$OURS/issues/60" >"$EXPORT"
already 60 900 "<!-- bead-fields 00000000 -->"
out=$(say); status=$?
assert "$out" "$status" 0 "1 removed" "the comment comes down"
assert "$(calls)" 0 0 "DELETE repos/o/r/issues/comments/900" "by its own id, and nothing else is touched"

already
out=$(say); status=$?
refute "$(calls)" 0 0 "DELETE" "and a silent bead with no comment is simply passed over"
assert "$out" "$status" 0 "0 written, 0 removed" "with nothing reported as done"

echo
echo "a dry run"

rich inventory-tng-aaa '{"notes":"n"}' "$OURS/issues/60" >"$EXPORT"
already
out=$(say --dry-run); status=$?
assert "$out" "$status" 0 "would write a comment on #60" "says what it would write"
assert "$out" "$status" 0 "this was a dry run" "and says that it wrote nothing"
refute "$(calls)" 0 0 "POST" "having written nothing"

already 60 900 "<!-- bead-fields 00000000 -->"
assert "$(say --dry-run)" 0 0 "would rewrite the comment on #60" "and distinguishes a rewrite from a first posting"

echo
echo "a mirror is not an update"
# inventory-tng-cwpa.15. Step 5 of the reconciliation keeps a handful of
# comments current, and its FIRST run would create one on every issue with
# something to say -- on a repository strangers read. Asked for rather than
# done, and the number is read out of the script so raising it moves these
# cases with it.

LIMIT=$(sed -n 's/^NEW_WITHOUT_ASKING=//p' "$HERE/say-bead.sh")

# many <count> -- that many beads, each with something to say and its own issue.
many() {
  local n
  : >"$EXPORT"
  for ((n = 1; n <= $1; n++)); do
    rich "inventory-tng-b$n" '{"notes":"n"}' "$OURS/issues/$((100 + n))" >>"$EXPORT"
  done
}

many $((LIMIT + 1))
already
out=$(say); status=$?
assert "$out" "$status" 1 "which is a mirror rather than an update" \
  "a run that would create more than it asks for refuses"
assert "$out" "$status" 1 "say-bead.sh --confirm" "and says what to type"
refute "$(calls)" 0 0 "POST" "having written nothing at all"

out=$(say --confirm); status=$?
assert "$out" "$status" 0 "$((LIMIT + 1)) written" "and --confirm lets the same run through"

out=$(say --dry-run); status=$?
assert "$out" "$status" 0 "this was a dry run" "a dry run over the line is not refused -- it writes nothing either way"

many "$LIMIT"
already
out=$(say); status=$?
assert "$out" "$status" 0 "$LIMIT written" "and a run exactly at the line goes through unasked"

# REWRITES ARE NOT CREATIONS, which is the whole reason only one of them is
# counted: correcting comments it already made is this script keeping its own
# work honest, and it is bounded by what is already there.
many $((LIMIT + 1))
args=()
for ((n = 1; n <= LIMIT + 1; n++)); do args+=("$((100 + n))" "$((900 + n))" "<!-- bead-fields 00000000 -->"); done
already "${args[@]}"
out=$(say); status=$?
assert "$out" "$status" 0 "$((LIMIT + 1)) written" "a pass that only rewrites is never refused, however large"
refute "$(calls)" 0 0 "POST" "because none of it is a new comment"

echo
echo "one bead at a time"
# How a person reads the wording before letting it loose on four hundred issues.

{
  rich inventory-tng-aaa '{"notes":"n"}' "$OURS/issues/60"
  rich inventory-tng-bbb '{"notes":"n"}' "$OURS/issues/61"
} >"$EXPORT"
already
out=$(say --only inventory-tng-bbb); status=$?
assert "$(calls)" 0 0 "POST repos/o/r/issues/61/comments" "only the bead named is written to"
refute "$(calls)" 0 0 "issues/60/comments" "and the rest of the tracker is left alone"

out=$(say --only inventory-tng-never); status=$?
assert "$out" "$status" 2 "no issue of o/r" "a bead with no issue is refused rather than silently doing nothing"

# A WHOLE ID, NOT A PREFIX. Two beads whose ids share a tail are different work,
# and a substring match would act on whichever the listing happened to hold.
out=$(say --only tng-bbb); status=$?
assert "$out" "$status" 2 "no issue of o/r" "and a fragment of a real id is not that id"
refute "$(calls)" 0 0 "POST" "so nothing is written for a bead nobody named"

echo
echo "a warning is not part of what gets written"
# ON THE SUCCESS PATH, which is the half nothing else here would notice. Folded
# into the rendering, a line python wrote to stderr becomes the FIRST line of
# the comment -- displacing the marker, so nothing this script wrote can be
# found again, the digest never matches, and every later run rewrites every
# comment on every issue. It reaches the listing the same way, where it is a row
# naming a bead that does not exist.

rich inventory-tng-aaa '{"notes":"n"}' "$OURS/issues/60" >"$EXPORT"
already
printf 'DeprecationWarning: something python still ran\n' > "$PY_NOISE"
out=$(say); status=$?
assert "$out" "$status" 0 "1 written" "a chatty python still gets the comment written"
refute "$(calls)" 0 0 "DeprecationWarning" "and nothing it printed to stderr reaches the issue"
assert "$(calls)" 0 0 "body=<!-- bead-fields " "so the comment still opens with the marker"
: > "$PY_NOISE"

echo
echo "when it cannot look"
# AN UNREACHABLE GITHUB MUST NOT READ AS A REPOSITORY WITH NO COMMENTS ON IT,
# which is the one failure that would make this write to every issue again.

echo 1 > "$GH_DEAD"
: > "$GH_COMMENTS"
out=$(say); status=$?
assert "$out" "$status" 2 "could not read the comments" "it refuses rather than assuming there are none"
refute "$(calls)" 0 0 "POST" "and writes nothing at all"
: > "$GH_DEAD"

echo
echo "arguments, and what it needs"

already
expect --nonsense -- 2 "unknown flag" "an unknown flag is refused"
out=$(say --only); status=$?
assert "$out" "$status" 2 "needs a bead" "and --only without one says so"

rm -f "$EXPORT"
out=$(say); status=$?
assert "$out" "$status" 2 "does not exist" "a missing export is refused rather than read as nothing to say"

verdict
