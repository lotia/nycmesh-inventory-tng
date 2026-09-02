#!/usr/bin/env bash
# The tracker, mirrored into GitHub issues, once.
#
# `scripts/sync-issues.sh` keeps two lists in step. This is the thing that runs
# before there are two lists: 415 of 417 beads had never been pushed, so the
# issue list was not a view of the tracker at all. Run once, it becomes one;
# afterwards this has nothing left to do and sync-issues.sh does the keeping.
#
# The arrangement, and the reasoning for every rule below, is
# ../docs/decisions/0031-the-issue-list-is-a-window-on-the-tracker.md.
#
# Usage: export-issues.sh [--confirm] [--batch N] [<repository>]
#
# A DRY RUN UNLESS `--confirm` IS PASSED, which is the wrong way round for most
# scripts here and the right way round for this one: the ordinary invocation is
# irreversible in the way that matters. GitHub will not delete an issue for an
# ordinary token, so a run that should not have happened leaves several hundred
# of them to be closed by hand.
#
# === IT CANNOT OVERWRITE ANYTHING, AND THAT IS STRUCTURAL ===
#
# `bd` chooses between filing a new issue and PATCHing an existing one on
# whether the bead carries an `external_ref`, and the PATCH sends the bead's
# description as the issue body, replacing whatever a person had written there.
# unexported.py names only beads that carry no ref, so the update path is not
# reachable from here -- not avoided, not guarded against, absent. The list is
# the proof, and it can be read: `unexported.py .beads/issues.jsonl`.
#
# THE OTHER LOSS IS NOT A WRITE, and 0031 point 4 has it: filing across an
# issue no bead knows about duplicates it instead. So this will not begin until
# pull-new-issues.sh --check is content.
#
# === WHY IT CLOSES THINGS AFTERWARDS ===
#
# Creation carries no state, so every issue arrives open however long ago its
# bead finished, and leaving them that way makes the next reconciliation
# propose the very rewrite this file stays away from. 0031 point 5.
#
# So the run asks the tracker afterwards what actually became of each bead it
# named -- `issue-numbers.py`, which answers both halves of that from one read --
# and closes the finished ones with `gh issue close`, which sends a state and
# nothing else. It is the narrow instrument on purpose.
#
# === RE-RUNNING IS THE RECOVERY ===
#
# Every issue filed writes an `external_ref` back onto its bead, so a run that
# stops half way -- a rate limit, a dropped connection, a token that expired --
# leaves the finished half out of the next run's input. Re-run it. Nothing is
# filed twice, and the closing pass asks GitHub which issues are open rather
# than assuming, so it resumes on the same terms.

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/report.sh"
. "$HERE/repository.sh"

REPO_ROOT=$(git -C "$HERE" rev-parse --show-toplevel) || exit 1
EXPORT="$REPO_ROOT/.beads/issues.jsonl"

#: How many issues `gh issue list` is asked for at once. Named rather than
#: written twice, because the guard below compares against it -- a limit and
#: the number it is checked against drifting apart is how that guard would
#: stop working silently.
LIMIT=1000

confirm=false
batch=25
repository="${GITHUB_REPOSITORY:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --confirm) confirm=true ;;
    # How many ids go into one `bd github sync --issues`. Small enough that a
    # failure names a short list rather than the whole tracker, large enough
    # that the run is not four hundred process starts.
    # Refused here rather than by `${2:?...}`, which exits 1. Every other way
    # of calling this wrongly exits 2, and a caller that reads 1 as "it looked
    # and objected" would take a missing value for a refusal.
    --batch)
      [[ $# -ge 2 ]] || {
        echo "export-issues: --batch needs a number" >&2
        exit 2
      }
      batch=$2
      shift
      ;;
    -*) echo "export-issues: unknown flag $1" >&2; exit 2 ;;
    *) repository="$1" ;;
  esac
  shift
done

if ! [[ "$batch" =~ ^[1-9][0-9]*$ ]]; then
  echo "export-issues: --batch takes a positive number, not $batch" >&2
  exit 2
fi

# `bd` only when something will actually be filed, the same division
# pull-new-issues.sh makes: a dry run answers from the committed export and
# needs nothing but a reader.
tools=(gh python3)
[[ "$confirm" == false ]] || tools+=(bd)
need_tools "${tools[@]}"

resolve_repository "$repository"
repository="$REPOSITORY"

# === THE CHECKOUT HAS TO BE CURRENT BEFORE ANYTHING IS FILED ===
#
# inventory-tng-cwpa.10. The guard is repository.sh's, because the hazard is the
# whole family's; the reasoning is 0031's paragraph on what "every clone has a
# complete map" is worth, including why the unlinked-issue precondition below
# cannot stand in for this one.
#
# ONLY A RUN THAT FILES is held to it, for the reason 0031 gives.
[[ "$confirm" == true ]] && require_current_checkout "$REPO_ROOT"

echo "1. Is anything on GitHub still unlinked?"
# Asked by running the script that owns the question rather than by repeating
# it. Exit 1 means something is waiting, and it has already said which; exit 2
# means it could not ask at all, and carrying on from that would be filing
# issues without knowing what is there.
"$HERE/pull-new-issues.sh" --check "$repository"
waiting=$?
if [[ $waiting -eq 2 ]]; then
  echo "export-issues: could not find out what GitHub is holding, so nothing was filed." >&2
  exit 2
fi
if [[ $waiting -ne 0 ]]; then
  echo
  echo "export-issues: bring those in first. Filing over them would make a second"
  echo "issue for work that already has one, which nothing afterwards can tell apart."
  exit 1
fi

echo
echo "2. Beads with no issue"
pending=$(python3 "$HERE/unexported.py" "$EXPORT") || exit 2

if [[ -z "$pending" ]]; then
  verdict "Every bead already has an issue. Nothing to export." exporting
fi

total=$(printf '%s\n' "$pending" | wc -l | tr -d ' ')
shut=$(printf '%s\n' "$pending" | awk -F'\t' '$2 == "closed"' | wc -l | tr -d ' ')
note "$total bead(s) have never been filed, of which $shut are closed and will be closed again on GitHub."

if [[ "$confirm" == false ]]; then
  echo
  note "Nothing was filed. This was a dry run; pass --confirm to do it."
  note "What would be filed, in full:  python3 scripts/unexported.py $EXPORT"
  verdict "Nothing filed: this was a dry run." exporting
fi

echo
echo "3. Filing them"
# Ids only. The status column is for the closing pass and `bd` has no use for
# it.
#
# THE COUNT BELOW IS WHAT WAS ASKED FOR, NOT WHAT ARRIVED, and it is deliberately
# not dressed up as the latter. `bd github sync` warns and carries on when one
# reference in a batch will not go -- which is why pull-new-issues.sh hands them
# over one at a time -- so a batch can report success having skipped part of
# itself. Step 4 is what turns that into a fact.
mapfile -t identifiers < <(printf '%s\n' "$pending" | cut -f1)
asked=0
for ((start = 0; start < ${#identifiers[@]}; start += batch)); do
  slice=("${identifiers[@]:start:batch}")
  ids=$(IFS=,; echo "${slice[*]}")
  if output=$(bd github sync --push-only --prefer-newer --issues "$ids" 2>&1); then
    asked=$((asked + ${#slice[@]}))
    note "$asked/$total -- $(printf '%s' "$output" | tail -1)"
  else
    fail "a batch of ${#slice[@]} beginning ${slice[0]} did not file:"
    note "$(printf '%s' "$output" | tail -3)"
    note "Re-run to carry on. What is already filed is out of the next run's input."
    stop exporting
  fi
done

# Scratch files for the answers below, cleaned however this exits.
listing=$(mktemp) || exit 2
gaps=$(mktemp) || exit 2
exported=$(mktemp) || exit 2
trap 'rm -f "$listing" "$gaps" "$exported"' EXIT

echo
echo "4. What became of every bead named"
# THE QUESTION STEP 3 CANNOT ANSWER, asked of the tracker as it stands rather
# than of the exit status of the thing that was meant to change it. `bd export`
# on stdout rather than the committed file, because bd's auto-export is not
# synchronous with the push and the file on disk can still be describing the
# tracker as it was a minute ago.
if ! bd export >"$exported" 2>"$gaps"; then
  fail "the issues are filed, and the tracker could not be read back:"
  note "$(tail -2 "$gaps")"
  note "Nothing was closed. Re-run once bd answers."
  stop exporting
fi

printf '%s\n' "$pending" > "$listing"

# ONE READER, TWO ANSWERS. Which issues to close comes back on stdout; what it
# could not account for comes back as `fail` lines this dispatches. Both are the
# same question about the same file -- and a bead the push passed over is the
# answer to both -- so asking them apart meant recovering the open ones by
# intersecting sets in shell, at the point where nothing can be undone.
#
# THE FAILURE CHECK IS THE POINT and it is the shape `relay` exists for: a
# reader that dies prints nothing, so a caller that only reads its output sees
# no findings and goes on to print the all-clear. Not `relay` itself, because
# that dispatches stdout and stdout here carries the numbers.
if ! to_close=$(python3 "$HERE/issue-numbers.py" "$listing" <"$exported" 2>"$gaps"); then
  fail "the issues are filed, and nothing could work out which to close:"
  note "$(tail -2 "$gaps")"
  stop exporting
fi
dispatch "$(cat "$gaps")"

echo
echo "5. Closing the ones that are closed here"
# ASKED, NOT ASSUMED. GitHub is asked which issues are open, so a re-run closes
# nothing twice.
#
# WHAT LEAVES A REOPENED ISSUE ALONE IS NOT THAT QUERY, and reading it that way
# round is the trap: an issue somebody reopened by hand IS open, so it would
# match below. The thing that spares it is issue-numbers.py keeping to the beads
# THIS run filed -- one filed by an earlier run already carries a ref, so
# unexported.py never names it and it never reaches here.

# STDERR KEPT OUT OF THE VALUE. On the failure path it is the message; on the
# success path a warning `gh` printed would otherwise become a line in
# $open_now, which the closing loop reads as an issue number.
if ! open_now=$(gh issue list --repo "$repository" --state open --limit "$LIMIT" \
  --json number --jq '.[].number' 2>"$gaps"); then
  fail "could not ask which issues are open, so none were closed:"
  note "$(tail -2 "$gaps")"
  note "The issues are filed. Re-run to finish the closing pass."
  stop exporting
fi

# A FULL PAGE IS NOT AN ANSWER. `gh` stops at --limit and says nothing about it,
# so a list exactly that long is one that may have been cut off -- and a closed
# bead whose issue fell off the end is silently left open. Refused rather than
# guessed at, because the failure is invisible from the output.
open_count=0
declare -A still_open=()
while IFS= read -r number; do
  [[ -n "$number" ]] || continue
  still_open[$number]=1
  open_count=$((open_count + 1))
done <<<"$open_now"

if [[ "$open_count" -ge "$LIMIT" ]]; then
  fail "GitHub returned the full $LIMIT open issues, so the list may be cut short."
  note "The issues are filed. Raise LIMIT in this script and re-run to close them."
  stop exporting
fi

# Membership from the map built above, for the reason step 4's own history
# gives: a `grep` per issue is a process per issue and a scan of the whole list
# each time, which is the only cost here that grows with the square of the
# tracker.
closed_count=0
while IFS= read -r number; do
  [[ -n "$number" ]] || continue
  [[ -n "${still_open[$number]:-}" ]] || continue
  if gh issue close "$number" --repo "$repository" >/dev/null 2>&1; then
    closed_count=$((closed_count + 1))
  else
    fail "#$number would not close"
  fi
done <<<"$to_close"

note "$closed_count issue(s) closed, so the list says what the tracker says."

verdict "The issue list is now a view of the tracker." exporting
