#!/usr/bin/env bash
# What sync-issues.sh --check asks, and what it refuses.
#
# The script had no suite: its reconciling half drives `bd` against a real
# GitHub and most of it cannot be exercised without both. `--check` can be,
# and it is the half a scheduled job depends on -- so what is pinned here is
# that all three questions get asked whatever the others say, that a red one
# is reported rather than swallowed, and that asking needs no `bd` at all.
#
# Usage: sync-issues.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/testlib.sh"
workspace

# The runner sets these, and either would answer from outside a question the
# stubs are here to answer.
unset GITHUB_OUTPUT GITHUB_REPOSITORY

BIN="$WORK/bin"
REPO="$WORK/repo"
OURS="https://github.com/o/r"

# What --check reaches for, checked against the scripts rather than guessed.
BORROWED=(bash readlink dirname git wc tr tail cut awk grep mktemp rm cat python3)

new_repo "$REPO"
mkdir -p "$REPO/scripts" "$REPO/.beads"
cp "$HERE/sync-issues.sh" "$HERE/pull-new-issues.sh" "$HERE/export-issues.sh" \
   "$HERE/unsynced.py" "$HERE/unexported.py" "$HERE/drifted.py" \
   "$HERE/report.sh" "$HERE/repository.sh" "$REPO/scripts/"
borrow "$BIN" "${BORROWED[@]}"

EXPORT="$REPO/.beads/issues.jsonl"

# A `gh` answering both listings from files the cases rewrite. The two differ:
# --json number,url for the unlinked question, --json number,state for the
# disagreement one, and a stub that returned one shape for both would let a
# reader pass on input it will never actually see.
cat > "$BIN/gh" <<'STUB'
#!/usr/bin/env bash
# EVERY KNOB IS A FILE THE SCENE OWNS, like the two listings, so that `scene`
# resets them all in the line it already resets those and no case has to
# remember an `unset`. $GH_NOISE makes it chatty on stderr while still
# SUCCEEDING -- what the real thing does with a release notice -- and $GH_DEAD
# makes the listings fail. Neither was reachable before, so every case passed
# on input `gh` does not always produce.
#
# `repo view` answers before the dead check, because resolve_repository must
# still work for a case about a listing that does not.
case "$*" in *"repo view"*) echo "o/r"; exit 0 ;; esac
cat "$GH_NOISE" >&2
[[ -s "$GH_DEAD" ]] && exit 1
case "$*" in
  *"number,state"*) cat "$GH_STATES" ;;
  *"issue list"*)   cat "$GH_ISSUES" ;;
  *) exit 1 ;;
esac
STUB
chmod +x "$BIN/gh"
export GH_ISSUES="$WORK/issues" GH_STATES="$WORK/states"
export GH_NOISE="$WORK/noise" GH_DEAD="$WORK/dead"

# `bd` must not be needed. It exists only so a case can prove that.
printf '#!/usr/bin/env bash\nexit 0\n' > "$WORK/bd.keep"
chmod +x "$WORK/bd.keep"
with_bd() { cp "$WORK/bd.keep" "$BIN/bd"; }
no_bd() { rm -f "$BIN/bd"; }
no_bd

# scene <"issues as number:state"> <"beads as id:status[:number]">
scene() {
  : > "$GH_ISSUES"; : > "$GH_STATES"; : > "$EXPORT"; : > "$GH_NOISE"; : > "$GH_DEAD"
  local entry n state id status number
  for entry in $1; do
    IFS=: read -r n state <<<"$entry"
    printf '%s\t%s/issues/%s\n' "$n" "$OURS" "$n" >> "$GH_ISSUES"
    printf '%s\t%s\n' "$n" "$state" >> "$GH_STATES"
  done
  for entry in $2; do
    IFS=: read -r id status number <<<"$entry"
    if [[ -n "$number" ]]; then
      bead "$id" "$status" "$OURS/issues/$number" >> "$EXPORT"
    else
      bead "$id" "$status" >> "$EXPORT"
    fi
  done
}

sync() { (cd "$REPO" && PATH="$BIN" ./scripts/sync-issues.sh "$@") 2>&1; }
check sync

echo "when the two lists are in step"

scene "60:OPEN" "inventory-tng-aaa:open:60"
expect --check -- 0 "in step" "everything agreeing is green"
expect --check -- 0 "Every bead and its issue agree" "and the third question says so in its own words"

echo
echo "each question on its own"

scene "60:OPEN 61:OPEN" "inventory-tng-aaa:open:60"
expect --check -- 1 "no bead points at" "an issue with no bead is red"

scene "60:OPEN" "inventory-tng-aaa:open:60 inventory-tng-bbb:open"
expect --check -- 1 "have no GitHub issue" "a bead with no issue is red"

scene "60:OPEN" "inventory-tng-aaa:closed:60"
out=$(sync --check); status=$?
assert "$out" "$status" 1 "disagree about being closed" "a bead and its issue disagreeing is red"
assert "$out" "$status" 1 "inventory-tng-aaa" "and the bead is named"
assert "$out" "$status" 1 "#60" "and so is the issue"
assert "$out" "$status" 1 "REOPENED" "and it says why nothing here decides it"

echo
echo "all three are asked whatever the others say"
# THE REASON THIS IS ONE SCRIPT AND NOT THREE WORKFLOW STEPS. A step that fails
# stops the job, so a red first question would leave the rest unasked and
# somebody fixing it would learn about the others tomorrow.

scene "60:OPEN 61:OPEN" "inventory-tng-aaa:closed:60 inventory-tng-bbb:open"
out=$(sync --check); status=$?
assert "$out" "$status" 1 "no bead points at" "the first question is asked"
assert "$out" "$status" 1 "have no GitHub issue" "the second is asked even though the first was red"
assert "$out" "$status" 1 "disagree about being closed" "and so is the third"
assert "$out" "$status" 1 "3 things to fix" "and all three are counted, not just the first"

echo
echo "a gh that is chatty, and a gh that is dead"

# STDERR MUST NOT BECOME DATA. gh prints notices on a call that succeeded, and
# folding them into the value makes drifted.py refuse a listing that was fine.
scene "60:OPEN" "inventory-tng-aaa:open:60"
echo "A new release of gh is available" > "$GH_NOISE"
expect --check -- 0 "in step" "a warning on stderr does not become a line of the listing"

# AND A QUESTION THAT COULD NOT BE ASKED IS NOT ONE ANSWERED "NO". The script's
# own comment says which question survives an unreachable GitHub, and why.
scene "60:OPEN" "inventory-tng-aaa:open:60 inventory-tng-bbb:open"
echo 1 > "$GH_DEAD"
out=$(sync --check); status=$?
assert "$out" "$status" 2 "could not be asked" "a gh that cannot answer exits 2, not 1"
assert "$out" "$status" 2 "have no GitHub issue" "and question 2 is still asked, because it needs no gh"
assert "$out" "$status" 2 "not an answer" "and the run says plainly that it is not one"
refute "$out" "$status" 2 "in step" "and never claims the two lists agree"

echo
echo "a listing cut short is not agreement"
# THE SILENCE READS AS A YES, which is the whole hazard and which the script
# spells out at its own LIMIT guard. What is pinned here is that a full page is
# refused rather than reported as agreement.
scene "60:OPEN" "inventory-tng-aaa:open:60"
for n in {1..1000}; do printf '%s\tOPEN\n' "$n"; done > "$GH_STATES"
out=$(sync --check); status=$?
assert "$out" "$status" 2 "cut short" "a listing filling the limit is refused, not read as agreement"
refute "$out" "$status" 2 "in step" "and the run never claims the two lists agree"

echo
echo "a pull half that could not look stops the run"
# EXIT 1 IS CARRIED ON FROM; EXIT 2 IS NOT, and what step 4 would do from a
# stale checkout is on the script at that branch. This scene's checkout tracks
# nothing, which is one of the two things the pull half's guard refuses on.
with_bd
scene "60:OPEN" "inventory-tng-aaa:open:60"
out=$(sync); status=$?
assert "$out" "$status" 2 "nothing here may push" "a refusal from step 1 stops the run"
refute "$out" "$status" 2 "Pushing what is only here" "so step 4 is never reached"

echo
echo "what asking needs"

scene "60:OPEN" "inventory-tng-aaa:open:60"
no_bd
expect --check -- 0 "in step" "asking needs no bd on the path at all"

with_bd
expect --dry-run -- 0 "would run" "while reconciling still does"
no_bd
expect --dry-run -- 2 "bd is needed" "and refuses without it"

echo
echo "arguments"

expect --nonsense -- 2 "unknown argument" "an unknown argument is refused"

verdict
