#!/usr/bin/env bash
# The tracker and the GitHub issue list, reconciled in the safe order.
#
# Four steps, and the order is the point:
#
#   1. find issues nobody local has heard of, and pull them   (pull-new-issues.sh)
#   2. refresh the ones already linked                        (bd, pull half)
#   3. SHOW WHAT ARRIVED
#   4. push what is only here                                 (bd, push half)
#
# WHY STEP 3 IS THE PROTECTION, and why nothing invokes this on a schedule, are
# both settled in ../docs/decisions/0031-the-issue-list-is-a-window-on-the-tracker.md
# -- points 6 and 7. In one line each so that reading this file is enough to run
# it: the flag is not what makes the conflict policy survivable, the committed
# export is; and a hook or a CI job would each break in its own way.
#
# Usage: sync-issues.sh [--dry-run] [--no-push]
#        sync-issues.sh --check
#
# --check RECONCILES NOTHING. It asks the three questions that together mean
# "are the two lists out of step", and REFUSES when any of them says yes, which
# is what a scheduled job wants: red while it is true, green the moment somebody
# has run the reconciliation. It needs no `bd` and writes nothing.
#
# WHY ALL THREE LIVE HERE, and not one per script. Two of them already had a
# --check of their own, on the scripts that own those questions, and this calls
# both rather than repeating them. The third -- whether the two sides DISAGREE
# about work they both know -- belongs to neither, because it is about
# reconciliation itself, which is this script's subject.
#
# AND ALL THREE ARE ASKED WHATEVER THE OTHERS SAY, which is why they are one
# script rather than three workflow steps -- 0031's paragraph on the standing
# signal has the argument.

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/report.sh"
. "$HERE/repository.sh"

REPO_ROOT=$(git -C "$HERE" rev-parse --show-toplevel) || exit 1
EXPORT=".beads/issues.jsonl"

#: How many issues `gh issue list` is asked for at once, named so the guard
#: below can compare against it. export-issues.sh says why it is named rather
#: than written twice. That both scripts now need one is inventory-tng-cwpa.13's
#: case for a single listing they share.
LIMIT=1000

dry_run=false
push=true
check=false
for argument in "$@"; do
  case "$argument" in
    --dry-run) dry_run=true ;;
    --no-push) push=false ;;
    --check) check=true ;;
    *) refuse "unknown argument $argument" ;;
  esac
done

# === IS ANYTHING OUT OF STEP? ===
#
# Answered and returned before anything below, because reconciling and asking
# whether reconciliation is needed want completely different things: this needs
# no `bd`, no token to write with, and nothing that could change either side.
if [[ "$check" == true ]]; then
  need_tools gh python3
  resolve_repository ""

  # Stderr goes here rather than into the values below. On a failure path it is
  # the message; on a SUCCESS path a warning `gh` printed would otherwise become
  # a line of what is read as data, and drifted.py would refuse a listing that
  # was actually fine. export-issues.sh carries the same arrangement, and for
  # the same reason.
  gaps=$(mktemp) || exit 2
  trap 'rm -f "$gaps"' EXIT

  # A QUESTION THAT COULD NOT BE ASKED IS NOT A QUESTION ANSWERED "NO", and the
  # difference is what this variable carries to the end. It is not derivable
  # from report.sh's `problems`, which counts objections without distinguishing
  # them from the questions nobody got to put.
  unanswerable=0

  # answer <status> <what it found> <what it could not ask>
  #
  # A delegate's three-valued exit, folded into this script's report. Written
  # once because the two call sites differ only in their two sentences, and a
  # protocol decoded twice is one that can drift in a single copy.
  answer() {
    case $1 in
      0) ;;
      1) fail "$2" ;;
      *) fail "$3"; unanswerable=1 ;;
    esac
  }

  # NUMBERED, because two of the three answers come from scripts that report in
  # their own voice and end with their own verdict. Without a heading over each,
  # "One thing to fix before exporting" lands in the middle of this one's report
  # looking like its conclusion.
  #
  # AND NONE OF THE THREE SHORT-CIRCUITS THE REST, which is the whole reason
  # they are here rather than in three workflow steps -- including when one
  # cannot be asked. Question 2 needs no `gh` and no network at all, so an
  # unreachable GitHub must not take it down with question 1. A bare `$?` on its
  # own line, because `cmd || [[ $? -eq 1 ]]` reads the status of the test.
  echo "1. Issues on GitHub that no bead points at"
  "$HERE/pull-new-issues.sh" --check "$REPOSITORY"
  unpulled=$?

  echo
  echo "2. Beads with no issue on GitHub"
  "$HERE/export-issues.sh" --check
  unfiled=$?

  echo
  echo "3. Work they both know, described differently"
  # inventory-tng-cwpa.11. The third question, and the one nothing asked: the
  # first two are about work one side has never heard of. drifted.py says why it
  # reports rather than fixes.
  if ! live=$(gh issue list --repo "$REPOSITORY" --state all --limit "$LIMIT" \
    --json number,state --jq '.[] | "\(.number)\t\(.state)"' 2>"$gaps"); then
    fail "could not ask GitHub what state its issues are in:"
    note "$(tail -2 "$gaps")"
    unanswerable=1
  elif [[ "$(count_lines "$live")" -ge "$LIMIT" ]]; then
    # A LISTING CUT SHORT READS AS AGREEMENT, which is why this cannot be left
    # to be noticed. drifted.py passes over a bead whose issue is not in the
    # listing -- it has no state to compare -- so every issue past the limit is
    # silently declared in step, and the run goes green over the part it never
    # looked at. export-issues.sh guards its own listing the same way.
    fail "GitHub returned the full $LIMIT issues, so the comparison would be cut short."
    note "Everything past that would be passed over and reported as agreeing."
    note "Raise LIMIT in this script and re-run."
    unanswerable=1
  elif ! disagreeing=$(printf '%s\n' "$live" \
    | python3 "$HERE/drifted.py" "$REPO_ROOT/$EXPORT" 2>"$gaps"); then
    fail "could not compare the two sides:"
    note "$(tail -2 "$gaps")"
    unanswerable=1
  elif [[ -n "$disagreeing" ]]; then
    fail "$(count_lines "$disagreeing") bead(s) and their issues disagree about being closed:"
    while IFS= read -r line; do
      [[ -n "$line" ]] && note "  $line"
    done <<<"$disagreeing"
    note "Nothing here decides that -- an issue may be open because somebody"
    note "REOPENED it. Settle it with scripts/sync-issues.sh, or by hand."
  else
    note "Every bead and its issue agree."
  fi

  echo
  # The two delegated questions were reported by the scripts that own them, so
  # their answers are added back here rather than re-derived.
  answer "$unpulled" "issues on GitHub that no bead points at (above)." \
    "whether GitHub holds an unlinked issue could not be asked (above)."
  answer "$unfiled" "beads with no GitHub issue (above)." \
    "whether a bead has no issue could not be asked (above)."

  # Exit 2, not 1, when any of the three could not be asked -- report.sh's
  # header says why the two are not interchangeable.
  [[ "$unanswerable" -eq 0 ]] ||
    refuse "at least one question could not be asked, so this is not an answer."

  verdict "The tracker and the issue list are in step." reconciling
fi

# `bd` IS THIS SCRIPT'S OWN REQUIREMENT, and asking for it here is a repair.
# Steps 2 and 4 invoke it directly, but nothing checked: pull-new-issues.sh
# refused without it on every path, so step 1 did the refusing. It no longer
# does on a dry run -- that question is answered from the committed export --
# which left `sync-issues.sh --dry-run` walking all four steps on a machine with
# no `bd` at all and ending "The tracker and the issue list agree."
# inventory-tng-qnxb.
need_tools bd

run() {
  if [[ "$dry_run" == true ]]; then
    note "would run: $*"
    return 0
  fi
  "$@"
}

echo "1. Issues nobody here has heard of"
if [[ "$dry_run" == true ]]; then
  "$HERE/pull-new-issues.sh" --dry-run || exit $?
else
  # A PROBLEM IS CARRIED ON FROM; A REFUSAL IS NOT, and the two were one branch
  # until the pull half grew a stale-checkout guard. Exit 1 is "a pull failed",
  # and step 3 still has something worth printing. EXIT 2 is "it could not look
  # at all" -- an unreachable remote, or a checkout behind its upstream -- and
  # carrying on from that walks into step 4, which pushes: `bd github sync
  # --push-only` files an issue for every bead carrying no `external_ref`, and
  # on a stale checkout that is precisely the set whose refs are in a commit
  # this clone has not fetched. That is the duplicate the guard exists to stop,
  # made permanent, because an ordinary token cannot delete a GitHub issue.
  "$HERE/pull-new-issues.sh"
  pulled=$?
  if [[ $pulled -eq 2 ]]; then
    refuse "the pull half could not run, so nothing here may push. Nothing was done."
  fi
  [[ $pulled -eq 0 ]] ||
    note "pull-new-issues reported a problem; carrying on to say what changed"
fi

echo
echo "2. Refreshing the issues already linked"
run bd github sync --pull-only --prefer-newer || note "the pull half reported a problem"

echo
echo "3. What arrived"
# Against HEAD rather than the index, so a change already staged is still shown:
# somebody running this twice should see the same thing the second time.
if arrived=$(git -C "$REPO_ROOT" diff --stat HEAD -- "$EXPORT") && [[ -n "$arrived" ]]; then
  note "$arrived"
  note "Read it before pushing: git -C $REPO_ROOT diff HEAD -- $EXPORT"
  note "Undo the lot with:      git -C $REPO_ROOT checkout HEAD -- $EXPORT"
else
  note "Nothing came back from GitHub that the tracker did not already have."
fi

if [[ "$push" == false ]]; then
  echo
  verdict "Pulled only, as asked. Nothing was pushed." syncing
fi

echo
echo "4. Pushing what is only here"
run bd github sync --push-only --prefer-newer || fail "the push half did not finish"

echo
note "Pushing writes external_ref onto every bead it files an issue for, so"
note "$EXPORT has almost certainly changed again. Commit it with the next"
note "piece of work rather than leaving it behind."

verdict "The tracker and the issue list agree." syncing
