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
