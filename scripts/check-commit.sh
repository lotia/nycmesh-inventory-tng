#!/usr/bin/env bash
# One issue, one commit -- read what is staged and say whether it is that.
#
# The rules are in DEVELOPERS.md "Commits", which also says how to run this and
# how to have it run every time. This only enforces the parts a machine can see.
#
# Usage: check-commit.sh [--amend] [--message-only] <message-file>
#
# --amend when you are replacing the last commit rather than adding one: what
# lands is then the staged changes *and* that commit's, and the issue it closes
# is usually already in it.
#
# --message-only for a caller reading a commit that has already landed, which
# has no staged diff for the tracker half to read. check-batch.sh asks for it.

set -uo pipefail

BASE=HEAD
MESSAGE_ONLY=0
while [[ "${1:-}" == --* ]]; do
  case "$1" in
    --amend)
      BASE=HEAD~1
      shift
      ;;
    --message-only)
      MESSAGE_ONLY=1
      shift
      ;;
    *)
      echo "unknown option: $1" >&2
      exit 2
      ;;
  esac
done
MESSAGE=${1:?usage: check-commit.sh [--amend] [--message-only] <message-file>}
REPO_ROOT=$(git rev-parse --show-toplevel) || exit 1
ISSUES=".beads/issues.jsonl"

# readlink -f first: DEVELOPERS.md has you install this as a symlink into
# .beads/hooks, and bash reports the link's own path here rather than the
# file's, so "beside me" would be the hooks directory.
_here=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$_here/report.sh"
. "$_here/trailers.sh"
# The rules themselves, so that check-batch.sh can apply the same ones without
# reading this script's output. SUMMARY_LIMIT and BODY_LIMIT come from there.
. "$_here/message-rules.sh"

# Comments are dropped by message-rules.sh, so that this script and
# check-batch.sh see the same message; read here only for the summary line the
# report prints and the merge/revert guard below.
mapfile -t lines < <(grep -v '^#' "$MESSAGE")
summary=${lines[0]:-}

# A merge, a revert and a cherry-pick are not somebody's issue being landed:
# git writes their messages itself, and as a commit-msg hook this would refuse
# every one of them.
if [[ -f "$REPO_ROOT/.git/MERGE_HEAD" || -f "$REPO_ROOT/.git/CHERRY_PICK_HEAD" ]] ||
  message_is_git_own "$summary"; then
  echo "Not an issue being landed (merge, revert or cherry-pick). Nothing to check."
  exit 0
fi

# --- what the staged tracker changes close ---------------------------------
#
# A commit may create and update issues -- raising follow-up work is honest --
# but only one may move to closed, because that is the issue the commit is.
#
# A *move* to closed, not a line that says closed: an issue closed long ago has
# its row rewritten whenever anything about it changes, and counting that as a
# closure would refuse an honest commit for touching history. So both sides of
# the diff are read and the ones already closed are taken back out.
#
# An epic is not counted at all; DEVELOPERS.md#the-message says why. Without
# that, the only ways out were to leave an epic open after its batch merged, or
# to give its closure a commit whose trailer named something nobody built.
closed=()
epics_closed=()
staged_tracker=""

if [[ "$MESSAGE_ONLY" -eq 0 ]]; then
  # Read once and used twice: whether the path was staged at all is the same
  # question as whether this diff is empty, and it runs as a commit-msg hook
  # against a tracker of a hundred-odd rows on every local commit.
  staged_tracker=$(git diff --cached "$BASE" -- "$ISSUES")

  mapfile -t moved < <(printf '%s\n' "$staged_tracker" | python3 -c '
import json, sys

was, now = set(), set()
for line in sys.stdin:
    if line[:1] not in "+-":
        continue
    try:
        issue = json.loads(line[1:])
    except ValueError:
        continue          # a diff header, or a line that is not one issue
    if issue.get("status") == "closed":
        # Tagged rather than dropped: an epic is not work, but a commit may
        # still be the one that closes one left open after its batch merged.
        kind = "epic" if issue.get("issue_type") == "epic" else "work"
        (now if line[0] == "+" else was).add((kind, issue["id"]))

for kind, issue_id in sorted(now - was):
    print(kind, issue_id)
')

  # Partitioned here rather than filtered there, so one read answers both
  # questions: what work this closes, and which epics it tidies up after.
  closed=()
  epics_closed=()
  for entry in ${moved+"${moved[@]}"}; do
    [[ -z "$entry" ]] && continue
    case "$entry" in
      epic\ *) epics_closed+=("${entry#epic }") ;;
      work\ *) closed+=("${entry#work }") ;;
    esac
  done

  echo "Staged:"
  if [[ ${#closed[@]} -eq 0 ]]; then
    note "no issue is closed here"
  else
    for id in "${closed[@]}"; do note "closes $id"; done
  fi

  if [[ ${#closed[@]} -gt 1 ]]; then
    fail "${#closed[@]} issues are closed here. One issue, one commit."
    note "  DEVELOPERS.md 'Commits' has the rule; .agents/skills/commits has the split."
  fi
fi

# --- the message -----------------------------------------------------------

echo "Message:"
note "\"$summary\""

message_rules "$(printf '%s\n' "${lines[@]}")"

named=$MESSAGE_TRAILER_ISSUE
if [[ "$MESSAGE_TRAILER_COUNT" -gt 0 && "$MESSAGE_CLOSES_COUNT" -le 1 ]]; then
  # A commit that only advances an issue closes nothing, so there is no
  # closure to cross-check and none to object to.
  if [[ "$MESSAGE_ONLY" -eq 0 ]]; then
    if [[ "$MESSAGE_CLOSES_COUNT" -eq 0 ]]; then
      note "names $named without closing it"
      if [[ ${#closed[@]} -gt 0 ]]; then
        fail "the message closes nothing but the staged tracker closes ${closed[0]}"
      fi
    # A contributor who does not use beads names a GitHub issue instead, and
    # there is no tracker file to compare it against. See DEVELOPERS.md.
    elif [[ "$named" == \#* ]]; then
      note "names $named, which is not a bead: nothing here to cross-check"
    elif [[ ${#closed[@]} -eq 1 && "$named" != "${closed[0]}" ]]; then
      fail "the message closes $named but the staged tracker closes ${closed[0]}"
    elif [[ ${#closed[@]} -eq 0 && " ${epics_closed[*]-} " == *" $named "* ]]; then
      # An epic is not counted as work, so it never reaches `closed` -- but a
      # commit may still be the one closing it, when it was left open after its
      # batch merged. Saying the tracker does not close it would be false.
      note "closes the epic $named, which is bookkeeping rather than work"
    elif [[ ${#closed[@]} -eq 0 ]]; then
      if [[ -z "$staged_tracker" ]]; then
        # beads exports the tracker on its own schedule, so the close may be
        # recorded and simply not written out yet.
        fail "nothing staged closes $named -- run 'bd close $named', then stage $ISSUES"
      else
        fail "$ISSUES is staged but does not close $named"
      fi
    fi
  fi
fi

verdict "One issue, one commit. Nothing to object to." landing
