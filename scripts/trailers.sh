#!/usr/bin/env bash
# What a commit says it belongs to.
#
# Sourced, never run. check-commit.sh reads trailers off a message being
# prepared and check-batch.sh off one already landed; if the two ever disagreed
# about what a trailer is, they would disagree about which issue a commit
# belongs to.

# The colon, and the trailers being the last paragraph, are what git requires;
# DEVELOPERS.md#commits says why. The colonless form is still read here,
# because history before that convention is full of it and `git log` should not
# go blind halfway back.

# trailers_of <message>: the trailer lines, one per line, on stdout.
trailers_of() {
  printf '%s\n' "$1" | grep -E '^(Closes|Refs):? '
}

# issue_of <trailer line>: the issue it names.
issue_of() {
  local rest=${1#* }
  printf '%s' "${rest%%[[:space:]]*}"
}

# parses_as_trailer <trailer line>: whether the line has the shape git wants.
parses_as_trailer() {
  [[ "$1" =~ ^(Closes|Refs):[[:space:]] ]]
}

# trailers_are_last <message>: whether the final paragraph is nothing but
# trailers, which is git's other requirement. A colon on a line git has already
# decided is prose buys nothing.
trailers_are_last() {
  local paragraph=() line
  while IFS= read -r line; do
    [[ -z "$line" ]] && { paragraph=(); continue; }
    paragraph+=("$line")
  done <<<"$1"

  [[ ${#paragraph[@]} -gt 0 ]] || return 1
  for line in "${paragraph[@]}"; do
    parses_as_trailer "$line" || return 1
  done
}
