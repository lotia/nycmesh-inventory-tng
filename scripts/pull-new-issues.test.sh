#!/usr/bin/env bash
# What pull-new-issues.sh needs, and what it refuses.
#
# The script talks to `gh` and to `bd`, which is why it had no suite: most of
# what it does cannot be exercised without both. What CAN be is the half a
# scheduled job depends on -- which of them it insists upon, and whether
# `--check` refuses. The dependency loop it pins says why `bd` is conditional;
# what this file adds is that the rewritten issue-sync workflow exited 2 on its
# first dispatched run, which is how the requirement was found to be wrong.
# inventory-tng-qnxb.
#
# Usage: scripts/pull-new-issues.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/testlib.sh"
workspace

# THE RUNNER'S OWN ENVIRONMENT IS NOT THE SCENE, and this suite runs as a CI
# step where both of these are set. GITHUB_OUTPUT would collect a `pulled=` line
# from every case that does not set its own -- appended to the step's real
# output file -- and GITHUB_REPOSITORY would answer, from outside, the question
# the `gh` stub is here to answer.
unset GITHUB_OUTPUT GITHUB_REPOSITORY

# EXACTLY WHAT THE SCRIPT REACHES FOR, which is the whole point of the scene:
# "bd is absent" only means something if the list is honest. Checked against the
# script rather than guessed -- `tail` is in the pull loop, and `sed`, `grep`
# and `cat` are not used at all. `command` is a bash builtin and needs nothing.
BORROWED=(bash readlink dirname git wc tr tail mktemp rm python3)

BIN="$WORK/bin"
REPO="$WORK/repo"
REMOTE="$WORK/remote"

# A REMOTE THE SCENE CAN MOVE, because a run that writes refuses from a checkout
# that is behind and the question is asked of git itself. testlib's, shared with
# export-issues.test.sh.
#
# The scripts have to live INSIDE the checkout they read: pull-new-issues.sh
# resolves the export from its own location, not the caller's directory.
tracking_repo "$REPO" "$REMOTE" || exit 1
mkdir -p "$REPO/scripts" "$REPO/.beads"
cp "$HERE/pull-new-issues.sh" "$HERE/unsynced.py" "$HERE/report.sh" \
   "$HERE/repository.sh" "$REPO/scripts/"
# One bead, linked to issue 1, so "everything is already linked" is the answer
# unless a case says otherwise.
printf '%s\n' '{"_type":"issue","id":"inventory-tng-aaa","external_ref":"https://github.com/o/r/issues/1"}' \
  > "$REPO/.beads/issues.jsonl"
borrow "$BIN" "${BORROWED[@]}"

# A `gh` that reads its answer from a file, the way repo-settings.test.sh and
# landing-gate.test.sh do theirs. Written once; the cases rewrite the fixture.
#
# BUILT-INS ONLY, so that BORROWED above stays a true statement about the script
# rather than a list padded out with whatever the harness happens to need:
# `$(<file)` is bash reading the file itself, where `cat` would be a tool the
# scene had to grant and the script never asks for.
cat > "$BIN/gh" <<'STUB'
#!/usr/bin/env bash
# $GH_NOISE makes it chatty on stderr while still SUCCEEDING, which is what the
# real thing does with a new-release notice. A stub tidier than reality tests a
# program that does not exist.
[[ -n "${GH_NOISE:-}" ]] && echo "$GH_NOISE" >&2
case "$*" in
  *"repo view"*)   echo "o/r" ;;
  *"issue list"*)  printf '%s\n' "$(<"$GH_ISSUES")" ;;
  *) exit 1 ;;
esac
STUB
chmod +x "$BIN/gh"
export GH_ISSUES="$WORK/issues"

# issues_are <numbers> -- what GitHub is holding, as `number<TAB>url` per line.
issues_are() {
  : > "$GH_ISSUES"
  local n
  for n in $1; do
    printf '%s\t%s\n' "$n" "https://github.com/o/r/issues/$n" >> "$GH_ISSUES"
  done
}

with_bd() { printf '#!/usr/bin/env bash\nexit 0\n' > "$BIN/bd"; chmod +x "$BIN/bd"; }
no_bd() { rm -f "$BIN/bd"; }

pull() { (cd "$REPO" && PATH="$BIN" ./scripts/pull-new-issues.sh "$@") 2>&1; }
check pull

# The count a caller acts on, read from a file this writes fresh each time --
# GITHUB_OUTPUT is APPENDED to, so a shared one would let a case pass on a line
# an earlier case left behind.
published() {
  : > "$WORK/out"
  GITHUB_OUTPUT="$WORK/out" pull "$@" >/dev/null 2>&1
  cat "$WORK/out"
}

echo "a dry run answers on its own"

no_bd
issues_are "1"
expect --dry-run -- 0 "already linked" "a dry run needs no bd on the path at all"

issues_are "1 2"
expect --dry-run -- 0 "no bead points at: 2" "and names what is waiting"

assert "$(published --dry-run)" 0 0 "pulled=1" "and publishes the count for a caller to act on"

issues_are "1"
assert "$(published --dry-run)" 0 0 "pulled=0" "including when nothing is waiting, which is what keeps a check from being permanently red"

echo
echo "--check is that, and refuses"
# The behaviour a scheduled job depends on, which the flag's own comment argues
# for and this pins.

issues_are "1 2"
expect --check -- 1 "have no bead" "an issue with no bead refuses"
expect --check -- 1 "scripts/untriaged.py" "and the refusal names what to do about it"
expect --check -- 1 "nothing to close" "and says it clears itself, because a person will wonder"

issues_are "1"
expect --check -- 0 "already linked" "and it is green when the tracker has heard of everything"

# Same as a dry run in what it needs: nothing is pulled either way.
assert "$(published --check)" 0 0 "pulled=0" "--check publishes the count too"

echo
echo "a real pull needs more"
# The other half, and the reason the requirement was there at all: pulling
# invokes bd per issue, so its absence refuses once rather than per issue.

issues_are "1 2"
expect 2 "bd is needed" "a pull that would write refuses without bd"

with_bd
issues_are "1"
expect 0 "already linked" "and is content once bd is there"

echo
echo "a gh that talks while it works"
# inventory-tng-p8q4.1. Folding stderr into the listing made a new-release
# notice a line unsynced.py had to parse, and it refused a listing that was fine.

issues_are "1 2"
export GH_NOISE="A new release of gh is available: 2.40.0"
expect --dry-run -- 0 "no bead points at: 2" "a warning on stderr does not become an issue record"
refute "$(pull --dry-run)" 0 0 "new release" "and never reaches the reader as data"
issues_are "1"
expect --dry-run -- 0 "already linked" "and a clean listing is still read correctly"
unset GH_NOISE

# The owner and the repository are case-insensitive on GitHub, and the URLs it
# returns are canonically cased, so a repository named in another casing is the
# same repository -- refusing its own listing would be the guard firing on what
# it exists to permit.
printf '%s\t%s\n' 1 "https://github.com/o/r/issues/1" > "$WORK/cased"
out=$(pull --check --listing "$WORK/cased" O/R); status=$?
assert "$out" "$status" 0 "already linked" \
  "a repository named in another casing is still the same repository"
refute "$out" "$status" 0 "not an issue of" "and its own listing is not refused as somebody else's"

echo
echo "a checkout that is not current"
# The other half of inventory-tng-cwpa.10. A stale checkout cannot see that an
# issue is already linked, so it pulls a SECOND bead for work that has one --
# and nothing else in this script could notice, because the precondition that
# would have failed is satisfied by the very ref that is missing.

with_bd
issues_are "1 2"
fall_behind "$REMOTE"
out=$(pull); status=$?
assert "$out" "$status" 2 "behind origin/main" "a pull that would write refuses from a stale checkout"
assert "$out" "$status" 2 "pull-new-issues.sh:" "and the refusal names the command that was run"

# The half that must not refuse: these answer from the committed export and
# pull nothing, and --check runs in CI where the head has no upstream at all.
expect --dry-run -- 0 "no bead points at: 2" "a dry run is not refused for being behind"
expect --check -- 1 "have no bead" "and neither is --check, which files nothing either"

catch_up "$REPO"
issues_are "1"
expect 0 "already linked" "and a current checkout pulls as before"

echo
echo "a listing handed in rather than fetched"
# inventory-tng-cwpa.13. The flag's own suite, and under the borrowed PATH above
# -- which is the assertion that matters here as much as the answers: a listing
# read with `cat` would need a program this scene does not grant, and the read
# failing left `offered` empty, which this script reports as "GitHub has no
# issues" and exits 0 for.

# Neither `gh` nor `bd` is reached on this path, so nothing stubs them: the
# listing is the whole input.
printf '1\t%s\n2\t%s\n' "https://github.com/o/r/issues/1" "https://github.com/o/r/issues/2" \
  > "$WORK/listing"
out=$(pull --check --listing "$WORK/listing"); status=$?
assert "$out" "$status" 1 "no bead points at: 2" "the answer comes from the file, not from gh"

# A LISTING THAT COULD NOT BE READ IS NOT AN EMPTY ONE. `-r` alone is true of a
# directory, and every unread listing looks exactly like a repository with no
# issues -- which this reports as nothing to pull, and exits 0 for.
out=$(pull --check --listing "$WORK"); status=$?
assert "$out" "$status" 2 "cannot read the listing" "a directory is refused rather than read as an empty listing"
refute "$out" "$status" 2 "Nothing to pull" "and it never reports success over a listing it did not read"

out=$(pull --check --listing "$WORK/never-written"); status=$?
assert "$out" "$status" 2 "cannot read the listing" "and so is a file that is not there"

# THE GUARD FIRING. The rule is unsynced.py's, and unsynced.test.sh pins both
# its wording and what it costs to skip. What this case pins is the wiring: a
# file handed in through --listing is actually put past it, on the one path
# where nothing else has settled whose issues these are.
# inventory-tng-cwpa.12.
printf '%s\t%s\n' 99 "https://github.com/someone/else/issues/99" > "$WORK/stray"
out=$(pull --check --listing "$WORK/stray"); status=$?
assert "$out" "$status" 2 "not an issue of o/r" "a listing from another repository is refused, not read"
refute "$out" "$status" 2 "no bead points at" "and none of it is offered for pulling"
# AND IT SAYS WHOSE REFUSAL IT IS. The rule is python's and prints a bare
# sentence; under sync-issues.sh --check that lands beneath a numbered heading
# with three scripts running under it and nothing saying which one objected.
assert "$out" "$status" 2 "pull-new-issues.sh:" "and the refusal names the command that was run"

verdict
