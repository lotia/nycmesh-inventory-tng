#!/usr/bin/env bash
# What export-issues.sh refuses, and what it does when it does not refuse.
#
# The behaviour worth pinning is the order of the guards. This script is the
# one thing here whose mistakes cannot be taken back -- an ordinary token
# cannot delete a GitHub issue -- so "it stopped before doing anything" is the
# assertion, over and over, and the interesting cases are all before step 3.
#
# `gh` and `bd` are stubs. What they are asked is checked; what GitHub would do
# with it is not, and cannot be from here.
#
# Usage: export-issues.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/testlib.sh"
workspace

# The runner sets both of these, and either would answer from outside a
# question the stubs exist to answer.
unset GITHUB_OUTPUT GITHUB_REPOSITORY

BIN="$WORK/bin"
REPO="$WORK/repo"
REMOTE="$WORK/remote"
OURS="https://github.com/o/r"

# What the script itself reaches for, checked against the file rather than
# guessed: `mapfile` and `printf` are builtins, and `sed` is not used.
BORROWED=(bash readlink dirname git wc tr cut awk grep tail mktemp rm cat python3)

# A REMOTE THE SCENE CAN MOVE. The guard added for inventory-tng-cwpa.10 refuses
# to file from a checkout that is behind, so every --confirm case needs a
# checkout that is legitimately current -- which means a real upstream rather
# than a stub, since the question is asked of git itself.
new_repo "$REMOTE"
(cd "$REMOTE" && git commit -q --allow-empty -m "root") || exit 1

new_repo "$REPO"
git -C "$REPO" remote add origin "$REMOTE"
git -C "$REPO" fetch -q origin
git -C "$REPO" reset -q --hard origin/main
git -C "$REPO" branch -q --set-upstream-to=origin/main main
mkdir -p "$REPO/scripts" "$REPO/.beads"
cp "$HERE/export-issues.sh" "$HERE/pull-new-issues.sh" "$HERE/unsynced.py" \
   "$HERE/unexported.py" "$HERE/issue-numbers.py" "$HERE/report.sh" \
   "$HERE/repository.sh" "$REPO/scripts/"
borrow "$BIN" "${BORROWED[@]}"

EXPORT="$REPO/.beads/issues.jsonl"

# A `gh` that reads its answers from files the cases rewrite, and records what
# it was told to close so the assertions can look.
cat > "$BIN/gh" <<'STUB'
#!/usr/bin/env bash
case "$*" in
  *"repo view"*)                echo "o/r" ;;
  *"issue list"*"state open"*)  printf '%s\n' "$(<"$GH_OPEN")" ;;
  *"issue list"*)               printf '%s\n' "$(<"$GH_ISSUES")" ;;
  *"issue close"*)              echo "$2" >> "$GH_CLOSED" ;;
  *) exit 1 ;;
esac
STUB
chmod +x "$BIN/gh"
# Saved and restored the way `bd` is below, rather than by re-running the
# heredoc: one case takes `gh` off the path to show that --check never wanted
# it, and this file already had an idiom for that.
cp "$BIN/gh" "$WORK/gh.keep"
no_gh() { rm -f "$BIN/gh"; }
with_gh() { cp "$WORK/gh.keep" "$BIN/gh"; chmod +x "$BIN/gh"; }
export GH_ISSUES="$WORK/issues" GH_OPEN="$WORK/open" GH_CLOSED="$WORK/closed"

# A `bd` that records every set of ids it was asked to push, and answers
# `export` from a file. It files nothing: what the push DOES is bd's business,
# and the point here is what it is asked.
cat > "$BIN/bd" <<'STUB'
#!/usr/bin/env bash
case "$*" in
  *"github sync"*)
    [[ -f "$BD_REFUSE" ]] && { echo "the push did not finish"; exit 1; }
    for word in "$@"; do
      case "$word" in inventory-tng-*) echo "$word" >> "$BD_PUSHED" ;; esac
    done
    echo "Pushed"
    ;;
  export) printf '%s\n' "$(<"$BD_EXPORT")" ;;
  *) exit 0 ;;
esac
STUB
chmod +x "$BIN/bd"
export BD_PUSHED="$WORK/pushed" BD_EXPORT="$WORK/exported" BD_REFUSE="$WORK/refuse"

no_bd() { rm -f "$BIN/bd"; }
with_bd() { cp "$WORK/bd.keep" "$BIN/bd"; chmod +x "$BIN/bd"; }
cp "$BIN/bd" "$WORK/bd.keep"

# scene <"issues on github"> <"beads, as id:status[:number] triples">
#
# Rebuilds every fixture, so no case can pass on what an earlier one left.
#
# THE TWO EXPORTS ARE DIFFERENT ON PURPOSE, and that difference is the scene.
# `$EXPORT` is the committed file the script reads to decide what to file;
# `$BD_EXPORT` is what `bd export` says AFTERWARDS, once the push has written a
# ref onto everything. A bead named without a number gets one from 900 up,
# which also joins the list of issues GitHub reports as open -- because an
# issue bd has just created IS open, which is the whole reason step 4 exists.
scene() {
  # HOW FAR BEHIND THE CHECKOUT IS is a fixture too, since inventory-tng-cwpa.10
  # made --confirm refuse from a stale one. Left to hand-restoration it was one
  # forgotten line away from silently mis-configuring the next case: the tool
  # check runs before the upstream guard, so the case after would still pass and
  # nothing would be red.
  git -C "$REPO" fetch -q origin
  git -C "$REPO" reset -q --hard origin/main
  git -C "$REPO" branch -q --set-upstream-to=origin/main main 2>/dev/null
  : > "$GH_ISSUES"; : > "$GH_OPEN"; : > "$GH_CLOSED"
  : > "$BD_PUSHED"; : > "$EXPORT"; : > "$BD_EXPORT"
  rm -f "$BD_REFUSE"
  local n entry id status number fresh=900
  for n in $1; do
    printf '%s\t%s/issues/%s\n' "$n" "$OURS" "$n" >> "$GH_ISSUES"
    printf '%s\n' "$n" >> "$GH_OPEN"
  done
  for entry in $2; do
    IFS=: read -r id status number <<<"$entry"
    if [[ "$number" == skip ]]; then
      # bd was asked and passed it over: no reference afterwards, and no issue.
      bead "$id" "$status" >> "$EXPORT"
      bead "$id" "$status" >> "$BD_EXPORT"
      continue
    fi
    if [[ -n "$number" ]]; then
      bead "$id" "$status" "$OURS/issues/$number" >> "$EXPORT"
    else
      bead "$id" "$status" >> "$EXPORT"
      number=$fresh
      fresh=$((fresh + 1))
      printf '%s\n' "$number" >> "$GH_OPEN"
    fi
    bead "$id" "$status" "$OURS/issues/$number" >> "$BD_EXPORT"
  done
}

# fall_behind: a commit on the remote that this checkout has not fetched.
fall_behind() {
  (cd "$REMOTE" && git commit -q --allow-empty -m "somebody else's work")
}
# The script under test fetches immediately before this runs, so the reset alone
# is enough to be current again.
catch_up() { git -C "$REPO" reset -q --hard origin/main; }

export_issues() { (cd "$REPO" && PATH="$BIN" ./scripts/export-issues.sh "$@") 2>&1; }
check export_issues

echo "before anything is filed"

# Every issue on GitHub is linked, so the precondition is met and the only
# thing left to say is the size of the job.
scene "60" "inventory-tng-aaa:open:60 inventory-tng-bbb:open inventory-tng-ccc:closed"
no_bd
out=$(export_issues); status=$?
assert "$out" "$status" 0 "2 bead(s) have never been filed" "a dry run counts what is waiting, with no bd on the path"
assert "$out" "$status" 0 "1 are closed" "and says how many will need closing again afterwards"

# NOUGHT IS A COUNT TOO. count_lines is handed a filtered subset here, and an
# empty one used to report 1 -- `printf '%s\n' ""` emits a newline, so `wc -l`
# counts it. The case above cannot catch that, because its scene has a closed
# bead in it.
scene "60" "inventory-tng-aaa:open:60 inventory-tng-bbb:open"
out=$(export_issues); status=$?
assert "$out" "$status" 0 "of which 0 are closed" "and says nought when none of them are closed"
assert "$out" "$status" 0 "dry run" "and files nothing"
assert "<$(cat "$BD_PUSHED")>" 0 0 "<>" "nothing reached bd"

echo
echo "the guard that comes first"
# An issue on GitHub that no bead points at. Filing over it makes a duplicate,
# and no later run can tell the pair apart.

scene "60 61" "inventory-tng-aaa:open:60 inventory-tng-bbb:open"
out=$(export_issues); status=$?
assert "$out" "$status" 1 "bring those in first" "an unlinked issue stops the export before it counts anything"
refute "$out" "$status" 1 "have never been filed" "and it stops BEFORE saying what it would file"
assert "<$(cat "$BD_PUSHED")>" 0 0 "<>" "nothing reached bd"

# --confirm does not get past it either, which is the case that matters.
with_bd
expect --confirm -- 1 "bring those in first" "--confirm does not override the precondition"
assert "<$(cat "$BD_PUSHED")>" 0 0 "<>" "and still nothing reached bd"

echo
echo "filing"

scene "60" "inventory-tng-aaa:open:60 inventory-tng-bbb:open inventory-tng-ccc:closed"
with_bd
expect --confirm -- 0 "view of the tracker" "a confirmed run finishes"
assert "$(cat "$BD_PUSHED")" 0 0 "inventory-tng-bbb" "the unfiled bead was pushed"
assert "$(cat "$BD_PUSHED")" 0 0 "inventory-tng-ccc" "and so was the closed one"
refute "$(cat "$BD_PUSHED")" 0 0 "inventory-tng-aaa" \
  "THE BEAD THAT ALREADY HAS AN ISSUE WAS NEVER NAMED, so bd could not overwrite its body"

echo
echo "closing what is closed here"

scene "60" "inventory-tng-aaa:open:60 inventory-tng-ccc:closed"
with_bd
out=$(export_issues --confirm)
assert "$out" 0 0 "1 issue(s) closed" "the closed bead's issue is closed on GitHub"

# The issue is not open on GitHub, so there is nothing to close and no second
# attempt at it. This is what makes a re-run safe.
scene "60" "inventory-tng-aaa:open:60 inventory-tng-ddd:closed"
: > "$GH_OPEN"
with_bd
out=$(export_issues --confirm)
assert "$out" 0 0 "0 issue(s) closed" "an issue GitHub says is already closed is left alone"
assert "<$(cat "$GH_CLOSED")>" 0 0 "<>" "and no close was attempted, which is what makes a re-run safe"

# A push that exited 0 having skipped one of its ids. issue-numbers.py says so
# on stderr and exits 0, so without the run counting that, this prints "the
# issue list is now a view of the tracker" over a closed bead that has no issue.
scene "60" "inventory-tng-aaa:open:60 inventory-tng-ccc:closed"
grep -v inventory-tng-ccc "$BD_EXPORT" >"$WORK/trimmed"; mv -f "$WORK/trimmed" "$BD_EXPORT"
with_bd
expect --confirm -- 1 "inventory-tng-ccc" \
  "a closed bead the push silently skipped fails the run rather than passing as a line of noise"

echo
echo "a bead the push passed over"
# `bd github sync` warns and carries on rather than failing the batch, so exit 0
# is not evidence that every id in it was filed. An OPEN bead skipped that way
# has no other way of being noticed: it is not in the closing pass, and the run
# would otherwise print the all-clear.

scene "60" "inventory-tng-aaa:open:60 inventory-tng-bbb:open:skip"
with_bd
out=$(export_issues --confirm)
assert "$out" "$?" 1 "inventory-tng-bbb" "an OPEN bead bd passed over fails the run"
assert "$out" 1 1 "to fix before exporting" "and the run is counted as failed rather than reported clean"
refute "$out" 1 1 "view of the tracker" "and the all-clear is not printed over it"

scene "60" "inventory-tng-aaa:open:60 inventory-tng-ccc:closed"
with_bd
out=$(export_issues --confirm)
assert "$out" "$?" 0 "view of the tracker" "and a run where nothing was passed over reaches the all-clear"

echo
echo "a page that may have been cut short"
# The guard the script argues for at $LIMIT, exercised: a page returned exactly
# full is one this cannot trust, and trusting it loses the closes that fell off
# the end.

scene "60" "inventory-tng-aaa:open:60 inventory-tng-ccc:closed"
seq 1 1000 > "$GH_OPEN"
with_bd
out=$(export_issues --confirm)
assert "$out" "$?" 1 "may be cut short" "a full page refuses rather than closing from it"
assert "<$(cat "$GH_CLOSED")>" 0 0 "<>" "and nothing is closed on a list it does not trust"

echo
echo "when there is nothing to do"

scene "60" "inventory-tng-aaa:open:60"
with_bd
expect --confirm -- 0 "Nothing to export" "a tracker that is already mirrored says so and stops"
assert "<$(cat "$BD_PUSHED")>" 0 0 "<>" "and pushes nothing"

echo
echo "when the push fails"

scene "60" "inventory-tng-aaa:open:60 inventory-tng-bbb:open"
with_bd
touch "$BD_REFUSE"
out=$(export_issues --confirm); status=$?
assert "$out" "$status" 1 "did not file" "a batch that fails is reported"
assert "$out" "$status" 1 "Re-run to carry on" "and the recovery is named, because it is not obvious"

echo
echo "--check, which is what a scheduled job asks"
# inventory-tng-cwpa.9. The signal ran one way only: CI was red while GitHub
# held an issue no bead pointed at, and silent while the tracker held a bead
# GitHub had never heard of.

scene "60" "inventory-tng-aaa:open:60 inventory-tng-bbb:open"
no_bd
out=$(export_issues --check); status=$?
assert "$out" "$status" 1 "1 bead(s) have no GitHub issue" "a bead with no issue refuses"
assert "$out" "$status" 1 "export-issues.sh --confirm" "and the refusal names what to do about it"
assert "$out" "$status" 1 "nothing to close" "and says it clears itself, because a person will wonder"
assert "<$(cat "$BD_PUSHED")>" 0 0 "<>" "and nothing is filed by asking"

scene "60" "inventory-tng-aaa:open:60"
expect --check -- 0 "Every bead has an issue" "and it is green once they all have one"

# THE PROPERTY THAT MAKES IT CHEAP ENOUGH TO ASK EVERY MORNING: the question is
# about the tracker, so it is answered from the committed export and reaches for
# nothing else. A runner with no token, no `bd` and no `gh` can ask it -- which
# is what lets it sit beside the opposite question in one job.
scene "60 61" "inventory-tng-aaa:open:60 inventory-tng-bbb:open"
no_gh
no_bd
out=$(export_issues --check); status=$?
assert "$out" "$status" 1 "have no GitHub issue" "it needs neither gh nor bd on the path"
refute "$out" "$status" 1 "bring those in first" "and does not run the filing precondition, which guards nothing here"
with_gh

echo
echo "a checkout that is not current"
# inventory-tng-cwpa.10. Why this cannot be left to the precondition above, and
# why only a run that files is held to it, are on the script and in 0031.

scene "60" "inventory-tng-aaa:open:60 inventory-tng-bbb:open"
with_bd
fall_behind
out=$(export_issues --confirm); status=$?
assert "$out" "$status" 2 "behind origin/main" "a checkout behind its upstream refuses to file"
assert "$out" "$status" 2 "export-issues.sh:" "and the refusal names the command that was run, not the file it lives in"
assert "$out" "$status" 2 "duplicate what they did" "and says what acting from here would do"
assert "$out" "$status" 2 "git pull --ff-only" "and names the way out"
assert "<$(cat "$BD_PUSHED")>" 0 0 "<>" "nothing reached bd"

# The precondition that exists cannot stand in for this one: it is satisfied,
# because a stale checkout does not know about the other side's issue either.
refute "$out" "$status" 2 "bring those in first" "and it is not the unlinked-issue guard doing the work"

catch_up
expect --confirm -- 0 "view of the tracker" "and it files once the checkout catches up"

# The half that must NOT refuse: refusing a question that files nothing would be
# refusing to answer it.
fall_behind
no_bd
expect 0 "have never been filed" "a dry run is not refused for being behind"

catch_up
scene "60" "inventory-tng-aaa:open:60 inventory-tng-bbb:open"
with_bd
git -C "$REPO" branch -q --unset-upstream
out=$(export_issues --confirm); status=$?
assert "$out" "$status" 2 "tracks nothing" "a branch with no upstream refuses rather than assuming it is current"
assert "<$(cat "$BD_PUSHED")>" 0 0 "<>" "and files nothing"
git -C "$REPO" branch -q --set-upstream-to=origin/main main

echo
echo "arguments"

scene "60" "inventory-tng-aaa:open:60 inventory-tng-bbb:open"
no_bd
expect --batch nought -- 2 "positive number" "--batch refuses something that is not one"
# Exit 2 like every other way of calling this wrongly. `${2:?...}` would exit 1,
# which a caller reads as "it looked and objected".
expect --batch -- 2 "--batch needs a number" "--batch with no value at all is refused the same way"
expect --nonsense -- 2 "unknown flag" "an unknown flag is refused rather than read as a repository"
# The two flags are opposites, and `--check` returning early would silently file
# nothing for somebody who typed --confirm. Refused rather than resolved.
expect --check --confirm -- 2 "opposites" "--check and --confirm together are refused"
assert "<$(cat "$BD_PUSHED")>" 0 0 "<>" "and that refusal files nothing either"
no_bd
expect --confirm -- 2 "bd is needed" "a confirmed run refuses without bd rather than half-doing it"

verdict
