#!/usr/bin/env bash
# Where this repository keeps its hooks, and whether git is pointed at them.
#
# Sourced, never run. scripts/bootstrap-dev.sh and scripts/check-setup.sh both
# ask this, and they used to ask it in their own words -- the same worktree
# resolution and the same scope handling written out twice, with a comment in
# one of them saying that the two must not answer differently. Two spellings of
# one predicate is how they come to. scripts/message-rules.sh is the same idea
# for what a commit message must be, and scripts/report.sh, scripts/trailers.sh
# and scripts/testlib.sh are the same idea again. inventory-tng-pspr.
#
# WHAT IS SHARED IS THE QUESTION, NOT THE ANSWER. bootstrap repairs what it
# finds and check-setup deliberately does not, so this classifies and sets
# variables and reports nothing. Source it, then:
#
#   hooks_state
#   case "$HOOKS_STATE" in ... esac
#
# Reads git's configuration and the filesystem, and writes neither.

#: .beads/hooks, because beads already owns that directory and already keeps
#: five hooks of its own in it. DEVELOPERS.md#checking-it says why a second
#: directory is not an option, and says that those five start running too --
#: one pointer is what arms all six.
HOOKS=.beads/hooks

#: The hook itself, and what it must lead to. Here for the same reason HOOKS is:
#: bootstrap-dev.sh INSTALLS this link and check-setup.sh REPORTS on it, so a
#: rename that reached only one of them would have the first installing what the
#: second calls wrong -- which is the disagreement this file exists to prevent,
#: one derivation below where it first drew the line.
HOOK=$HOOKS/commit-msg
CHECKER=scripts/check-commit.sh

# The hooks directory of the MAIN checkout. One directory serves every worktree
# of a repository, because core.hooksPath is shared with them all, so in a
# linked worktree this is the one git will really run and naming it is being
# wired.
hooks_main() {
  printf '%s/%s\n' \
    "$(dirname "$(readlink -f "$(git rev-parse --git-common-dir)")")" "$HOOKS"
}

# What git is holding, as HOOKS_STATE, with HOOKS_CONFIGURED and HOOKS_SCOPE
# set beside it so a caller can phrase its own message:
#
#   unset    nothing set core.hooksPath, so none of the hooks run
#   ours     it names this repository's hooks, here or in the main checkout
#   missing  it names a directory that is not there
#   theirs   it names somebody else's, and only one can be in force at a time
#
# EVERY SCOPE, not the local one alone. A local value overrides a global one,
# so reading only local means writing over somebody's global hooks -- signing,
# secret scanning, an employer's policy -- and reporting that as success. It
# would also call a clone unwired while its hooks were running.
#
# COMPARED BY WHERE THE PATHS LEAD rather than by how they are spelled, because
# beads writes an absolute one and bootstrap writes a relative one and the two
# name the same directory.
#
# `missing` is an answer of its own because two identical spellings compare
# unequal under -ef when neither can be stat'd, and reporting that as a clash
# explains a conflict between a path and itself.
#
# TWO READS AND NOT ONE, deliberately. `--show-scope` emits the scope and the
# value as one tab-separated row, so a single call could set both -- but it is
# an option git only grew in 2.26, and where it is not understood that row is
# empty. The value would then read as unset, which is the one answer that makes
# bootstrap-dev.sh WRITE: it would take somebody's configured hooks path for
# nothing being configured and overwrite it. The plain --get is the older
# spelling and the load-bearing one, so it is asked on its own and the scope --
# which only decorates a refusal -- is asked separately and allowed to be empty.
#
# `|| true` on both: git exits non-zero when nothing is set, and a caller
# running under `set -e` would otherwise be killed by the ordinary case.
hooks_state() {
  HOOKS_CONFIGURED=$(git config --get core.hooksPath || true)
  HOOKS_SCOPE=$(git config --show-scope --get core.hooksPath 2>/dev/null | cut -f1 || true)
  if [[ -z "$HOOKS_CONFIGURED" ]]; then
    HOOKS_STATE="unset"
  elif [[ "$HOOKS_CONFIGURED" -ef "$HOOKS" || "$HOOKS_CONFIGURED" -ef "$(hooks_main)" ]]; then
    HOOKS_STATE="ours"
  elif [[ ! -d "$HOOKS_CONFIGURED" ]]; then
    HOOKS_STATE="missing"
  else
    HOOKS_STATE="theirs"
  fi
}
