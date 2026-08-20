#!/usr/bin/env bash
# One issue, one commit -- read what is staged and say whether it is that.
#
# The rules are in DEVELOPERS.md "Commits", which also says how to run this and
# how to have it run every time. This only enforces the parts a machine can see.
#
# Usage: check-commit.sh [--amend] <message-file>
#
# --amend when you are replacing the last commit rather than adding one: what
# lands is then the staged changes *and* that commit's, and the issue it closes
# is usually already in it.

set -uo pipefail

BASE=HEAD
if [[ "${1:-}" == "--amend" ]]; then
  BASE=HEAD~1
  shift
fi
MESSAGE=${1:?usage: check-commit.sh [--amend] <message-file>}
REPO_ROOT=$(git rev-parse --show-toplevel) || exit 1
ISSUES=".beads/issues.jsonl"

SUMMARY_LIMIT=50
BODY_LIMIT=72

problems=0

fail() {
  printf '  ✗ %s\n' "$1"
  problems=$((problems + 1))
}

note() {
  printf '  · %s\n' "$1"
}

# Comments are git's own and never reach the stored message.
mapfile -t lines < <(grep -v '^#' "$MESSAGE")
summary=${lines[0]:-}

# A merge, a revert and a cherry-pick are not somebody's issue being landed:
# git writes their messages itself, and as a commit-msg hook this would refuse
# every one of them.
if [[ -f "$REPO_ROOT/.git/MERGE_HEAD" || -f "$REPO_ROOT/.git/CHERRY_PICK_HEAD" ]] ||
  [[ "$summary" =~ ^(Merge|Revert)\  ]]; then
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
mapfile -t closed < <(git diff --cached "$BASE" -- "$ISSUES" | python3 -c '
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
        (now if line[0] == "+" else was).add(issue["id"])

for issue_id in sorted(now - was):
    print(issue_id)
')

staged_tracker=$(git diff --cached --name-only "$BASE" -- "$ISSUES")

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

# --- the message -----------------------------------------------------------

echo "Message:"
note "\"$summary\""

if [[ -z "$summary" ]]; then
  fail "the summary line is empty"
fi

if [[ ${#summary} -gt $SUMMARY_LIMIT ]]; then
  fail "the summary line is ${#summary} characters, over $SUMMARY_LIMIT"
  note "  usually the issue was too big rather than the line too short"
fi

if [[ "$summary" == *. ]]; then
  fail "the summary line ends in a full stop"
fi

# Imperative mood, as far as a machine can tell: the past tense and the gerund
# are what gets written instead. The exceptions are imperatives that simply end
# that way; extend the list when a real commit trips it.
first=${summary%% *}
shopt -s nocasematch
if [[ "$first" =~ (ed|ing)$ ]] &&
  [[ ! "$first" =~ ^(Bring|Embed|Exceed|Feed|Proceed|Read|Seed|Shed|Speed|Spread|Succeed)$ ]]; then
  fail "\"$first\" is not the imperative: write \"Extract\", not \"Extracted\" or \"Extracting\""
fi
shopt -u nocasematch

if [[ ${#lines[@]} -gt 1 && -n "${lines[1]}" ]]; then
  fail "the line after the summary must be blank"
fi

# Everything after the summary, rather than everything after the blank line:
# a message that forgot the blank line still has a body, and it is still too
# wide.
for line in "${lines[@]:1}"; do
  # A line with nowhere to break -- a long URL, pasted output -- is left alone.
  if [[ ${#line} -gt $BODY_LIMIT && "$line" == *" "* ]]; then
    fail "a body line is ${#line} characters, over $BODY_LIMIT: ${line:0:40}..."
    break
  fi
done

mapfile -t trailers < <(printf '%s\n' "${lines[@]}" | grep '^Closes ')

if [[ ${#trailers[@]} -ne 1 ]]; then
  fail "expected exactly one 'Closes <issue>' trailer, found ${#trailers[@]}"
else
  named=${trailers[0]#Closes }
  named=${named%%[[:space:]]*}
  # A contributor who does not use beads names a GitHub issue instead, and
  # there is no tracker file to compare it against. See DEVELOPERS.md.
  if [[ "$named" == \#* ]]; then
    note "names $named, which is not a bead: nothing here to cross-check"
  elif [[ ${#closed[@]} -eq 1 && "$named" != "${closed[0]}" ]]; then
    fail "the message closes $named but the staged tracker closes ${closed[0]}"
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

echo
if [[ "$problems" -eq 0 ]]; then
  echo "One issue, one commit. Nothing to object to."
  exit 0
fi

if [[ "$problems" -eq 1 ]]; then
  echo "One thing to fix before landing."
else
  echo "$problems things to fix before landing."
fi
exit 1
