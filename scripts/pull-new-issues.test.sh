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
BORROWED=(bash readlink dirname git wc tr tail python3)

BIN="$WORK/bin"
REPO="$WORK/repo"

# The scripts have to live INSIDE the checkout they read: pull-new-issues.sh
# resolves the export from its own location, not the caller's directory.
new_repo "$REPO"
mkdir -p "$BIN" "$REPO/scripts" "$REPO/.beads"
cp "$HERE/pull-new-issues.sh" "$HERE/unsynced.py" "$HERE/report.sh" \
   "$HERE/repository.sh" "$REPO/scripts/"
# One bead, linked to issue 1, so "everything is already linked" is the answer
# unless a case says otherwise.
printf '%s\n' '{"_type":"issue","id":"inventory-tng-aaa","external_ref":"https://github.com/o/r/issues/1"}' \
  > "$REPO/.beads/issues.jsonl"
for tool in "${BORROWED[@]}"; do ln -sf "$(command -v "$tool")" "$BIN/$tool"; done

# A `gh` that reads its answer from a file, the way repo-settings.test.sh and
# landing-gate.test.sh do theirs. Written once; the cases rewrite the fixture.
#
# BUILT-INS ONLY, so that BORROWED above stays a true statement about the script
# rather than a list padded out with whatever the harness happens to need:
# `$(<file)` is bash reading the file itself, where `cat` would be a tool the
# scene had to grant and the script never asks for.
cat > "$BIN/gh" <<'STUB'
#!/usr/bin/env bash
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

verdict
