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
ISSUES=".beads/issues.jsonl"

# Where git keeps the two files that say a commit is one of its own.
#
# This used to be "$REPO_ROOT/.git/MERGE_HEAD", off `--show-toplevel`, and in a
# LINKED WORKTREE that path cannot exist: the worktree root holds a .git FILE
# saying `gitdir: ...`, and the per-worktree state lives where that points. So
# both tests were permanently false and the escape below was unreachable --
# every conflicted cherry-pick in a worktree was refused as somebody's issue
# being landed badly. This repository's agents work in .claude/worktrees/, so
# that was the ordinary case rather than an exotic one: inventory-tng-wr9o.
#
# `--git-path` is the question actually being asked -- where does git keep this
# -- and it answers correctly in a main checkout, in a linked worktree, and
# under a relocated GIT_DIR. It also fails outside a repository, which is the
# guard `--show-toplevel` used to provide here.
MERGE_HEAD_PATH=$(git rev-parse --git-path MERGE_HEAD) || exit 1
CHERRY_PICK_HEAD_PATH=$(git rev-parse --git-path CHERRY_PICK_HEAD) || exit 1

# readlink -f first: .beads/hooks/commit-msg is a symlink to this file and is
# how this normally runs, and bash reports the link's own path here rather than
# the file's, so "beside me" would be the hooks directory.
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
if [[ -f "$MERGE_HEAD_PATH" || -f "$CHERRY_PICK_HEAD_PATH" ]] ||
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

# What a tracker diff on stdin moves to closed, as "<kind> <id>" lines.
#
# A function rather than an inlined heredoc because `amends_head` below asks
# the same question of a different diff -- HEAD's own -- and the two must agree
# exactly. A second copy that drifted would decide that an amend was a new
# commit, which is the bug this is part of fixing.
tracker_closures() {
  python3 -c '
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
'
}

# What to say when the reader above could not run, in the one place both
# callers reach for it: the staged diff below, and HEAD's own in `amends_head`.
# Two call sites and one refusal, because the first version of this said it in
# only one of them and the other went on quietly deciding that a broken
# interpreter meant no closures.
#
# Exit 2, not 1: nothing was checked, which is a different answer from having
# checked and objected. Either way the commit does not proceed.
tracker_unreadable() {
  echo "check-commit.sh: python3 could not read $ISSUES, so nothing was checked." >&2
  echo "  It is one of the three programs an agent session needs, and" >&2
  echo "  DEVELOPERS.md 'Prerequisites' says why a shim is not enough." >&2
  exit 2
}

# Is this message replacing HEAD rather than adding a commit after it?
#
# git hands a commit-msg hook the message path and nothing else, so --amend
# cannot be detected the way it is passed on a command line: it reaches this
# script only from a person at a terminal. During an amend the index already
# equals HEAD's tree, so the closure the commit being replaced already carries
# is invisible to the staged diff and looks missing. That is inventory-tng-h0hr.
#
# TWO CONDITIONS, NEVER EITHER ALONE: HEAD itself closed the issue this message
# names, AND this summary is HEAD's summary. Without the second, every
# follow-up that stages nothing for the tracker and claims `Closes: X` where
# the commit before it closed X would be waved through.
#
# WHAT THAT NARROWS TO, RATHER THAN CLOSES. The follow-up it stops is the one
# that wrote its own summary, which is the shape the mistake actually takes.
# One that repeats HEAD's summary character for character is still accepted --
# measured, not assumed: a new commit with an unrelated file staged, nothing
# moved to closed, and `Closes: X` under HEAD's own subject passes here. No
# commit-msg hook can do better, because git hands it a message path and
# nothing about the tree the message is being committed against, and an amend
# folding in forgotten work stages exactly that unrelated file.
#
# The backstop is a range rather than a commit: check-batch.sh objects that an
# issue is closed by two commits, and a branch does not merge until it stops
# saying so. So the gap is one commit wide and something else is watching it.
#
# The known cost is a reword: changing the summary is indistinguishable from
# that follow-up on the evidence a commit-msg hook is given, so it is refused
# and the refusal says which way is through. Decided on inventory-tng-h0hr.
amends_head() {
  local named=$1 head_summary head_diff head_closures
  head_summary=$(git log -1 --format=%s 2>/dev/null) || return 1
  [[ -n "$head_summary" && "$summary" == "$head_summary" ]] || return 1
  # `git show` rather than a diff against HEAD~1, so that amending the first
  # commit in a repository is read like any other rather than erroring.
  head_diff=$(git show --format= HEAD -- "$ISSUES") || return 1
  # HEAD touched nothing here, so it closed nothing, and there is no reason to
  # start an interpreter to be told so.
  [[ -n "$head_diff" ]] || return 1
  # Read separately from the grep, and the two failures kept apart: a reader
  # that could not run is not a HEAD that closed something else. Piped straight
  # into grep, python3's status went where `mapfile` used to send it -- into a
  # pipeline nothing asked about -- and the answer was the same wrong one, an
  # honest amend told nothing staged closes the issue with python3 named
  # nowhere. This half was left behind when the other was fixed.
  head_closures=$(printf '%s\n' "$head_diff" | tracker_closures) || tracker_unreadable
  grep -qxF "work $named" <<<"$head_closures"
}

# How to land a message the check above cannot accept, said wherever it
# refuses: rewording is the case it cannot see, and this is the way round it.
#
# `reword:`, and not a bare `--fixup`, which is what this said until a review
# tried it. A plain fixup! carries its own message nowhere -- autosquash keeps
# the target's subject -- so following that advice left the summary exactly as
# it was refused. `--fixup=reword:<commit>` writes an amend! whose body is the
# new message, and the fold takes that. Both spellings run this hook when they
# are committed, and both pass it because git wrote the subject line and
# message-rules.sh exempts the three `!` forms; only the rebase that folds them
# in runs no commit-msg hook.
#
# One `note` per line rather than one string with newlines in it, because
# `note` marks the line it is handed and every line after the first would come
# out unmarked and unindented, against the run of the report around it.
note_reword() {
  note "  amending? the summary has to match HEAD's, or this cannot tell an"
  note "  amend from a follow-up claiming the closure made before it."
  note "  To reword: git commit --fixup=reword:<commit>, then git rebase"
  note "  --autosquash. A plain --fixup would leave this summary unchanged."
}

if [[ "$MESSAGE_ONLY" -eq 0 ]]; then
  # Read once and used twice: whether the path was staged at all is the same
  # question as whether this diff is empty, and it runs as a commit-msg hook
  # against a tracker of a hundred-odd rows on every local commit.
  staged_tracker=$(git diff --cached "$BASE" -- "$ISSUES")

  moved=()
  # No tracker change means no closure to find, and no interpreter to start
  # looking for one. Most commits stage nothing here, and this now runs on
  # every one of them: measured on inventory-tng-pg63, the spawn against an
  # empty diff was 18.8 ms of the checker's 30.0 ms, for byte-identical output.
  if [[ -n "$staged_tracker" ]]; then
    # The exit status IS part of the check. `mapfile < <(...)` throws it away,
    # so a python3 that was missing or broken yielded no closures and the
    # script went on to accuse an honest commit of not closing what it had in
    # fact closed -- naming neither python3 nor the real reason. Not a remote
    # case, either: DEVELOPERS.md "Prerequisites" says how a hook comes to be
    # run without the interpreter this needs.
    read_out=$(printf '%s\n' "$staged_tracker" | tracker_closures) || tracker_unreadable
    mapfile -t moved <<<"$read_out"
  fi

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
      # Before either refusal, because both of them describe a commit being
      # added after HEAD and this is the case where it is HEAD being replaced.
      # Both spellings reach here: an amend that stages nothing at all, and the
      # commoner one where beads re-exports the tracker so something IS staged
      # and simply closes nothing.
      if amends_head "$named"; then
        note "amends the commit that closed $named"
      elif [[ -z "$staged_tracker" ]]; then
        # beads exports the tracker on its own schedule, so the close may be
        # recorded and simply not written out yet.
        fail "nothing staged closes $named -- run 'bd close $named', then stage $ISSUES"
        note_reword
      else
        fail "$ISSUES is staged but does not close $named"
        note_reword
      fi
    fi
  fi
fi

verdict "One issue, one commit. Nothing to object to." landing
