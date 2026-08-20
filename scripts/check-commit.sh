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

# The summary names its issue and then describes the change. The limit is on
# the description: the identifier is addressing, not prose, and charging the
# line for it would shorten every summary in the repository to pay for
# something the reader gains nothing from reading.
#
# Only the distinguishing part appears here -- "c6j.6", not
# "inventory-tng-c6j.6" -- because the repository prefix is the same on every
# bead and would spend 14 of the 50 characters saying so. The trailer carries
# the full identifier, which is what a machine reads.
#
# What makes it an identifier is that the trailer agrees: a prefix is only
# waved through if the issue this commit belongs to actually ends with it.
# Anything else -- a "Fix:" habit, a colon that happens to fall early -- is
# prose and is charged for. Guessing from the shape of the token instead does
# not work, because a bead identifier is arbitrary and need not contain a
# digit: this repository has swr and jro as well as c6j and 2dg.
# Read once, here, and used by both the summary check and the trailer rules
# below. Three greps over the same array was three definitions of what a
# trailer is, and they could disagree on a malformed one.
trailers=()
closing=()
for line in "${lines[@]}"; do
  case "$line" in
    Closes\ *)
      trailers+=("$line")
      closing+=("$line")
      ;;
    Refs\ *) trailers+=("$line") ;;
  esac
done

issue_of() {
  local t=${1#* }
  printf '%s' "${t%%[[:space:]]*}"
}

trailer_issue=""
[[ ${#trailers[@]} -gt 0 ]] && trailer_issue=$(issue_of "${trailers[0]}")

prose=$summary
if [[ "$summary" =~ ^([^[:space:]]+):[[:space:]](.*)$ ]]; then
  # *"$marker" covers the exact match too: [[ abc == *abc ]] is true.
  [[ -n "$trailer_issue" && "$trailer_issue" == *"${BASH_REMATCH[1]}" ]] &&
    prose=${BASH_REMATCH[2]}
fi

if [[ ${#prose} -gt $SUMMARY_LIMIT ]]; then
  fail "the summary is ${#prose} characters, over $SUMMARY_LIMIT"
  note "  usually the issue was too big rather than the line too short"
fi

if [[ "$summary" == *. ]]; then
  fail "the summary line ends in a full stop"
fi

# Imperative mood, as far as a machine can tell: the past tense and the gerund
# are what gets written instead. The exceptions are imperatives that simply end
# that way; extend the list when a real commit trips it.
first=${prose%% *}
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

# Every trailer names an issue, and they all name the same one. That is what
# "one issue per commit" reduces to in a message: an issue may take more than
# one commit, so `Refs` exists for the ones that advance it without finishing
# it, but no commit may name two issues whatever the verb.
if [[ ${#trailers[@]} -eq 0 ]]; then
  fail "expected a 'Closes <issue>' or 'Refs <issue>' trailer, found none"
elif [[ ${#closing[@]} -gt 1 ]]; then
  fail "${#closing[@]} 'Closes' trailers. One issue, one commit."
else
  named=$trailer_issue
  for trailer in "${trailers[@]:1}"; do
    other=$(issue_of "$trailer")
    if [[ "$other" != "$named" ]]; then
      fail "the trailers name $named and $other. A commit belongs to one issue."
      break
    fi
  done

  # A commit that only advances an issue closes nothing, so there is no
  # closure to cross-check and none to object to.
  if [[ ${#closing[@]} -eq 0 ]]; then
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
