#!/usr/bin/env bash
# Will a commit made here be read before it becomes one?
#
# Two questions, and only one of them has an answer that means anything in CI.
# Which is which is the whole design of this script, so it is stated rather
# than left to be worked out from the flag.
#
# THE HALF THAT TRAVELS. `.beads/hooks/commit-msg` is a tracked symlink to
# `scripts/check-commit.sh`, so the hook arrives with the clone and goes on
# leading to the checker as the checker changes. That is a fact about the tree,
# true in any checkout of it, and it is asked on every run.
#
# THE HALF THAT DOES NOT. `core.hooksPath` is written in `.git/config`, and no
# clone copies that file. A runner checks out afresh and never commits, so it
# never has one set: asking there would fail every build and would be reporting
# the absence of something nothing needed. So it is asked of a clone somebody
# works in, and `--shipped-only` is how a caller says it is not one.
#
# What sets both is scripts/bootstrap-dev.sh, which this deliberately does not
# do for you: a checker that repaired what it found would report nothing and
# there would be no way to find out a clone had gone unwired.
#
# Usage: check-setup.sh [--shipped-only]

set -uo pipefail

# Before the cd, and not after it: BASH_SOURCE holds whatever the caller typed,
# so resolving it from anywhere but the caller's own directory finds a
# `report.sh` belonging to somebody else or to nobody.
HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")

REPO_ROOT=$(git rev-parse --show-toplevel) || exit 1
cd "$REPO_ROOT" || exit 1

. "$HERE/report.sh"
. "$HERE/hooks-path.sh"

SHIPPED_ONLY=0
# Every argument, not the leading flags alone: this script takes no positional,
# so a loop that stopped at the first bare word would discard a mistyped flag
# in silence and check something other than what it was asked to.
while [[ $# -gt 0 ]]; do
  case "$1" in
    --shipped-only)
      SHIPPED_ONLY=1
      shift
      ;;
    *)
      echo "unknown option: $1" >&2
      exit 2
      ;;
  esac
done

BOOTSTRAP="scripts/bootstrap-dev.sh, or mise run setup"

# One chain rather than four questions asked in a row, because these are four
# ways of holding the same thing wrong: a hook stored as a copy also fails the
# test below it, and reporting both would say "2 things to fix" about one.
#
# `head -n1` because `git ls-files -s` prints a row per stage, and during a
# conflict on this path there are three of them -- which read as one string is
# neither empty nor a mode, and had the tracked symlink reported as a copy at
# exactly the moment somebody was mid-rebase and wanting to know.
mode=$(git ls-files -s -- "$HOOK" | head -n1 | cut -d' ' -f1)
if [[ -z "$mode" ]]; then
  fail "$HOOK is not tracked, so nobody who clones this gets one."
  note "Add it with: git add $HOOK"
elif [[ "$mode" != 120000 ]]; then
  fail "$HOOK is stored as a file of its own rather than as a link to $CHECKER."
  note "It will go stale the next time the checker changes, and say nothing."
elif ! [[ "$HOOK" -ef "$CHECKER" ]]; then
  # -ef asks whether two names reach one file, which is the question, and is
  # false when either reaches nothing. `readlink -f` is not asked where a
  # broken link leads, because it answers with a name that reads as a file.
  if [[ -e "$HOOK" ]]; then
    fail "$HOOK does not reach $CHECKER; it reaches $(readlink -f "$HOOK")."
  else
    dangling=$(readlink "$HOOK" 2>/dev/null || true)
    fail "$HOOK reaches nothing${dangling:+, pointing as it does at $dangling}."
  fi
  note "Delete it and run $BOOTSTRAP to have it made again."
elif [[ ! -x "$CHECKER" ]]; then
  fail "$CHECKER cannot be executed, so git would report it as a failed commit."
  note "Fix it with: chmod +x $CHECKER"
fi

# Only the wiring was being asked, and wiring is not the whole of whether a
# commit gets read: the checker reaches the tracker through python3, which a
# hook can easily fail to see for the reason DEVELOPERS.md "Prerequisites"
# gives. Finding that out at the first refused commit is worse than finding it
# out here, so it is asked here too -- inventory-tng-pg63.
if ! command -v python3 >/dev/null 2>&1; then
  fail "python3 is not on this PATH, and $CHECKER reads the tracker through it."
  note "Commits staging the tracker would be refused. See DEVELOPERS.md 'Prerequisites'."
fi

if [[ "$SHIPPED_ONLY" -eq 0 ]]; then
  # Which of the four this is, and how it was worked out, is scripts/hooks-path.sh.
  # This end of it only says what each one means to somebody checking a clone.
  hooks_state
  case "$HOOKS_STATE" in
    unset)
      fail "git has not been told where this clone keeps its hooks, so none of them run."
      note "Run $BOOTSTRAP. It is safe over a clone you are already working in."
      ;;
    ours) ;;
    missing)
      fail "core.hooksPath names $HOOKS_CONFIGURED${HOOKS_SCOPE:+, in your $HOOKS_SCOPE configuration}, and there is no such directory."
      note "Nothing runs out of a directory that is not there, and $BOOTSTRAP will"
      note "not change a setting of yours for you. Point it here with:"
      note "  git config --local core.hooksPath $HOOKS"
      ;;
    *)
      fail "core.hooksPath names $HOOKS_CONFIGURED${HOOKS_SCOPE:+, in your $HOOKS_SCOPE configuration}, and the hooks are in $HOOKS."
      note "Only one of the two can be in force, and it is the one git is holding."
      ;;
  esac
fi

verdict "A commit made here is read before it becomes one." "committing"
