#!/usr/bin/env bash
# The tracker and the GitHub issue list, reconciled in the safe order.
#
# Five steps, and the order is the point:
#
#   1. find issues nobody local has heard of, and pull them   (pull-new-issues.sh)
#   2. refresh the ones already linked                        (bd, pull half)
#   3. SHOW WHAT ARRIVED
#   4. push what is only here                                 (bd, push half)
#   5. say what a body cannot carry                           (say-bead.sh)
#
# --no-say leaves step 5 out, for a reconciliation somebody wants without it.
#
# WHY STEP 3 IS THE PROTECTION, and why nothing invokes this on a schedule, are
# both settled in ../docs/decisions/0031-the-issue-list-is-a-window-on-the-tracker.md
# -- points 6 and 7. In one line each so that reading this file is enough to run
# it: the flag is not what makes the conflict policy survivable, the committed
# export is; and a hook or a CI job would each break in its own way.
#
# Usage: sync-issues.sh [--dry-run] [--no-push] [--no-say]
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

dry_run=false
push=true
say=true
check=false
for argument in "$@"; do
  case "$argument" in
    --dry-run) dry_run=true ;;
    --no-push) push=false ;;
    --no-say) say=false ;;
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
  # One trap over all of them: a second `trap ... EXIT` REPLACES the first, so
  # two of them thirty lines apart is one list that silently leaks whatever was
  # added to the wrong one.
  gaps=$(mktemp) || exit 2
  urls=$(mktemp) || exit 2
  trap 'rm -f "$gaps" "$urls"' EXIT

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

  # ONE LISTING, ASKED FOR ONCE. Questions 1 and 3 both want what GitHub is
  # holding; asked separately that was two round trips for one extra JSON field
  # -- measured at 2.6s of a 5.9s run, on the daily schedule.
  #
  # THE BETTER REASON IS NOT SPEED. Two listings are two snapshots, and an issue
  # closed between them is described by one and not the other, with nothing to
  # say which. One fetch means the three questions are answered about the same
  # GitHub. inventory-tng-cwpa.13.
# AND FAILING TO GET IT DOES NOT END THE RUN. Questions 1 and 3 read this
  # listing and question 2 does not -- it needs no `gh`, no token and no network
  # -- so an unreachable GitHub must not take it down with the other two. That
  # is the same rule the three questions already follow among themselves.
  #
  # ONE REASON, NOT TWO FLAGS. Whether the listing can be used, and why it
  # cannot, are one fact: as two booleans they had an unreachable combination a
  # reader had to rule out at each of two sites, and a failed SPLIT reported
  # itself as GitHub not answering -- which it had, perfectly well.
  #
  # AND CUT SHORT IS SETTLED HERE, NOT AT THE ONE QUESTION THAT USED TO NOTICE.
  # A page filling the limit is now read by TWO questions, so leaving the guard
  # at question 3 had question 1 say "every issue on GitHub is already linked"
  # as a fact, under its own heading, about a listing this script had already
  # worked out it could not see the end of.
  unusable=""
  full_page=false
  if ! live=$(gh issue list --repo "$REPOSITORY" --state all --limit "$ISSUE_LIMIT" \
    --json number,url,state --jq '.[] | "\(.number)\t\(.url)\t\(.state)"' 2>"$gaps"); then
    # tail -2, as everywhere else here: `gh` usually fails in two lines, and on
    # a scheduled run this note is the whole debugging surface.
    fail "GitHub could not be listed:"
    note "$(tail -2 "$gaps")"
    unusable="GitHub would not give the listing (above)."
  elif listing_cut_short "$live"; then
    full_page=true
    unusable="the listing filled the limit, so it stops short of the end (see 3)."
  # ONE COLUMN PAIR IS A FILE because --listing takes a path; the other stays a
  # pipe, which is what it always was. A split that did not happen would leave
  # an empty file, and an empty listing reads as "GitHub is holding nothing" --
  # the silent green every other guard in this family exists to stop -- so the
  # one that IS a file is checked.
  elif ! printf '%s\n' "$live" | cut -f1,2 > "$urls"; then
    unusable="the listing could not be split into the columns each question reads."
    note "$unusable"
  fi

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
  if [[ -n "$unusable" ]]; then
    note "Not asked: $unusable"
    unpulled=2
  else
    "$HERE/pull-new-issues.sh" --check --listing "$urls" "$REPOSITORY"
    unpulled=$?
  fi

  echo
  echo "2. Beads with no issue on GitHub"
  "$HERE/export-issues.sh" --check
  unfiled=$?

  echo
  echo "3. Work they both know, described differently"
  # inventory-tng-cwpa.11. The third question, and the one nothing asked: the
  # first two are about work one side has never heard of. drifted.py says why it
  # reports rather than fixes.
  if [[ -n "$unusable" && "$full_page" == false ]]; then
    fail "whether the two sides disagree could not be asked: $unusable"
    unanswerable=1
  elif [[ "$full_page" == true ]]; then
    # WHAT IT COSTS HERE, the rule itself being repository.sh's: drifted.py has
    # no state to compare for a bead whose issue is not in the listing, so it
    # passes over it, and everything past the limit is declared in step by a run
    # that never looked at it.
    fail "GitHub returned the full $ISSUE_LIMIT issues, so the comparison would be cut short."
    note "Everything past that would be passed over and reported as agreeing."
    note "$ISSUE_LIMIT_ADVICE"
    unanswerable=1
  # THE REPOSITORY IS HANDED OVER, because the listing arrives as numbers and
  # states with nothing in it to say whose. drifted.py reads a number out of
  # every ref in the export to look it up here, and a number means nothing until
  # the ref it came from is known to be this repository's. inventory-tng-cwpa.12.
  elif ! disagreeing=$(printf '%s\n' "$live" | cut -f1,3 \
    | python3 "$HERE/drifted.py" "$REPO_ROOT/$EXPORT" "$REPOSITORY" 2>"$gaps"); then
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
need_tools bd gh

# AND THE REPOSITORY IS SETTLED HERE TOO, not only in the --check branch above,
# which returns before ever reaching this half. Step 5 hands the answer down --
# one resolution rather than one per script, which is what repository.sh's
# header is for -- and `set -u` does not let an unresolved one degrade: it ends
# the run at the last step with "unbound variable", AFTER step 4 has pushed. So
# it is asked before anything is written, where a repository nobody can name is
# a refusal rather than a half-finished reconciliation.
resolve_repository ""

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
echo "5. And what a body cannot carry"
# HERE RATHER THAN ANYWHERE ELSE. Step 4 has just written every bead's
# description onto its issue, and this writes what that description cannot hold
# onto the same issue in the same pass -- which is the whole of why it is a step
# of this script rather than a command beside it. 0031's amendment argues it;
# say-bead.sh says what it costs and why it has no --check.
#
# A PROBLEM HERE IS NOT A REASON TO DISOWN THE PUSH. Step 4 has already changed
# GitHub by the time this runs, so a failure is counted and reported rather than
# refused over -- the run still has to say what it did.
if [[ "$say" == false ]]; then
  note "Left out, as asked. The comments are whatever the last run made them."
elif [[ "$dry_run" == true ]]; then
  # ASKED RATHER THAN NAMED, unlike the steps above. Those delegate to `bd`,
  # which has nothing to ask; this one has a dry run of its own that costs about
  # five requests, writes nothing, and names every comment it would create --
  # which is the number somebody deciding whether to type --confirm needs.
  "$HERE/say-bead.sh" --dry-run "$REPOSITORY" || fail "the last step could not say what it would do"
else
  # NOT --confirm, EVER, FROM HERE. That flag exists so that mirroring the whole
  # tracker onto a public repository is something a person types, and passing it
  # on their behalf would be this script deciding it for them. A first run
  # refuses and says what to type; every run after it has nothing to ask about.
  "$HERE/say-bead.sh" "$REPOSITORY" || fail "not every issue could be told what its bead holds"
fi

echo
note "Pushing writes external_ref onto every bead it files an issue for, so"
note "$EXPORT has almost certainly changed again. Commit it with the next"
note "piece of work rather than leaving it behind."

verdict "The tracker and the issue list agree." syncing
