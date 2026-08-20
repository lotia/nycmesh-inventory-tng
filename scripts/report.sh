#!/usr/bin/env bash
# How the checkers say what they found.
#
# Sourced, never run. Three scripts printed the same two glyphs from three
# copies of the same two functions, and check-batch.sh reads check-commit.sh's
# output by looking for one of them -- so the vocabulary was already shared in
# fact, and only the definition was not.
#
# Usage, from a script beside this file:
#
#   . "$(dirname "${BASH_SOURCE[0]}")/report.sh"
#   fail "what is wrong"
#   note "something worth saying that is not wrong"
#   verdict "Nothing to object to." landing     # exits 0, or 1 having counted

# The mark a failure is printed with. check-batch.sh greps for it, so it is
# named rather than typed in two places.
MARK_FAIL='✗'
MARK_NOTE='·'

problems=0

fail() {
  printf '  %s %s\n' "$MARK_FAIL" "$1"
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
