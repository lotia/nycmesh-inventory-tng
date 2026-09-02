#!/usr/bin/env bash
# How the checkers say what they found.
#
# Sourced, never run. Three scripts printed the same two glyphs from three
# copies of the same two functions, so the vocabulary was already shared in
# fact and only the definition was not. check-batch.sh once read
# check-commit.sh's output by looking for one of them; it calls the rules
# directly now, and no script reads another's.
#
# Usage, from a script beside this file:
#
#   . "$(dirname "${BASH_SOURCE[0]}")/report.sh"
#   fail "what is wrong"
#   note "something worth saying that is not wrong"
#   verdict "Nothing to object to." landing     # exits 0, or 1 having counted

# The mark a failure is printed with. Named rather than typed in two places.
MARK_FAIL='✗'
MARK_NOTE='·'

problems=0

# Set by a caller reporting on more than one thing -- check-batch.sh sets it to
# a short sha -- so an objection says which one it belongs to. Prefixing here
# rather than at the call site is what lets a rule be written once and reported
# by both callers: the rule says what is wrong, the caller says where.
#
# Failures only. The notes after a failure belong to it and are indented to say
# so; stamping them too would repeat the sha on every continuation line and
# push that indent out of the left margin, which is the readability this is for.
REPORT_PREFIX=""

fail() {
  printf '  %s %s%s\n' "$MARK_FAIL" "${REPORT_PREFIX:+$REPORT_PREFIX }" "$1"
  problems=$((problems + 1))
}

note() {
  printf '  %s %s\n' "$MARK_NOTE" "$1"
}

# dispatch <what a reader printed>
#
# A reader says what it found as `fail <line>` and `note <line>`; this turns
# that back into calls. Three scripts had a byte-identical copy of the loop --
# check-docs.sh, check-telemetry.sh and check-batch.sh -- which is the same
# drift the header above describes about `fail` and `note` themselves, one
# level up: the vocabulary was shared and the reading of it was not.
dispatch() {
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    case "$line" in
      fail\ *) fail "${line#fail }" ;;
      note\ *) note "${line#note }" ;;
    esac
  done <<<"$1"
}

# relay <command...>
#
# Run a reader and dispatch what it printed, or say that nothing was checked
# and stop.
#
# THE GUARD IS THE POINT, and it is the half that cannot be tested by reading:
# a reader that crashed prints nothing, so without this the dispatch below sees
# an empty string, reports nothing, and `verdict` prints the all-clear over a
# check that never ran. Exits 2 rather than 1, so a caller can tell "it looked
# and objected" from "it could not look".
#
# The command is taken already built, because callers pass different
# environments to their readers and `env VAR=value ...` in the argument list is
# how they say so.
#
# The caller is not asked its own name. `BASH_SOURCE[1]` inside a function
# defined in this sourced file is the script that called it, so a checker does
# not write its own filename out a second time -- which is the same literal,
# and the same way of going stale, that `ReportingCommand.named` exists to
# abolish on the Python side.
relay() {
  local name findings
  name=${BASH_SOURCE[1]##*/}
  findings=$("$@") || {
    echo "$name: the reader failed, so nothing was checked." >&2
    exit 2
  }
  dispatch "$findings"
}

# stop <what the fixing is before>
#
# For where a failure has just been counted and there is no all-clear left to
# print. `verdict` takes the sentence for the good case first, so a caller with
# nothing to say there had to invent one and assert it could never be reached --
# which spelled a SUCCESS sentence, and exit 0, onto the failure path of scripts
# that must not report success over an unfinished job. Six call sites did that,
# in three different wordings.
#
# Having no zero-problems branch is the whole point: the invariant is the
# function's rather than each caller's to argue.
stop() {
  problems=$((problems > 0 ? problems : 1))
  verdict "unreachable: stop is only called having already failed" "$1"
}

# verdict <sentence when there is nothing wrong> <what the fixing is before>
verdict() {
  echo
  if [[ "$problems" -eq 0 ]]; then
    echo "$1"
    exit 0
  fi
  if [[ "$problems" -eq 1 ]]; then
    echo "One thing to fix before $2."
  else
    echo "$problems things to fix before $2."
  fi
  exit 1
}
