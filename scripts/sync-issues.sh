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

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/report.sh"
. "$HERE/repository.sh"

REPO_ROOT=$(git -C "$HERE" rev-parse --show-toplevel) || exit 1
EXPORT=".beads/issues.jsonl"

dry_run=false
push=true
for argument in "$@"; do
  case "$argument" in
    --dry-run) dry_run=true ;;
    --no-push) push=false ;;
    *) echo "sync-issues: unknown argument $argument" >&2; exit 2 ;;
  esac
done

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
  "$HERE/pull-new-issues.sh" || note "pull-new-issues reported a problem; carrying on to say what changed"
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
