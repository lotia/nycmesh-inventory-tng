#!/usr/bin/env bash
# What a commit says it belongs to.
#
# Sourced, never run. check-commit.sh reads trailers off a message being
# prepared and check-batch.sh off one already landed; if the two ever disagreed
# about what a trailer is, they would disagree about which issue a commit
# belongs to.

# trailers_of <message>: the trailer lines, one per line, on stdout.
trailers_of() {
  printf '%s\n' "$1" | grep -E '^(Closes|Refs) '
}

# issue_of <trailer line>: the issue it names.
issue_of() {
  local rest=${1#* }
  printf '%s' "${rest%%[[:space:]]*}"
}
