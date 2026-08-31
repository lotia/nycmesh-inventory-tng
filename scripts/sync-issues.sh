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
# WHY STEP 3 IS THE PROTECTION. The conflict policy is `--prefer-newer`, so an
# edit made on GitHub replaces the bead's description when it is the more
# recent of the two. That is the project owner's choice while it is still
# unknown whether people will use beads, and it is safe here for a reason that
# has nothing to do with the flag: `.beads/issues.jsonl` is committed, so
# anything arriving from GitHub lands as a reviewable git diff before this
# pushes anything, and `git checkout .beads/issues.jsonl` undoes it whole. The
# tracker has version history. Pulling first and looking is what turns that
# from a property into a practice.
#
# NOT A PRE-PUSH HOOK, and that was tried before it was rejected. Pushing sets
# `external_ref` on every bead it creates an issue for, which rewrites the
# committed export -- measured, not supposed. So a hook on `git push` would
# leave the tracker dirty immediately after every push, which is precisely how
# tracker state gets stranded and lost. It would also make every contributor
# need a GitHub token and slow every push for the benefit of promptness alone.
# `inventory-tng-cwpa.3` runs this in CI instead, where the export change is
# committed rather than abandoned.
#
# Usage: sync-issues.sh [--dry-run] [--no-push]

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/report.sh"

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
