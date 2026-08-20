#!/usr/bin/env bash
# The harness the checkers' test suites share.
#
# Sourced, never run. Each suite keeps what is its own -- how it builds a scene,
# and what it asserts -- and gets the counting, the reporting and the verdict
# from here, so that a change to any of those is made once instead of four
# times and forgotten in three.
#
# Usage, from a suite beside this file:
#
#   . "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/testlib.sh"
#   workspace                       # $WORK, removed on exit
#   check "$WORK/thing" arg...      # the command every `expect` runs
#   expect 0 "substring" "what this case is called"
#   expect --flag -- 1 "substring" "a flag, and -- when it takes a value"
#   assert "$out" "$status" 1 "substring" "a case the command shape does not fit"
#   refute "$out" "$status" 0 "substring" "a case that must NOT say something"
#   verdict                         # prints the tally and exits

passed=0
failed=0

# The command under test and the arguments every case passes it. `expect`
# inserts its own flags between the two, which is where --amend, --words and
# --epic go.
CHECK_CMD=()
CHECK_ARGS=()

check() {
  CHECK_CMD=("$1")
  shift
  CHECK_ARGS=("$@")
}

# A throwaway directory, removed however the suite exits.
workspace() {
  WORK=$(mktemp -d)
  # shellcheck disable=SC2064  # $WORK is wanted now, not when the trap fires
  trap "rm -rf '$WORK'" EXIT
  export WORK
}

# A git repository with an identity, which several suites need before they can
# commit anything.
new_repo() {
  local dir=$1 branch=${2:-main}
  rm -rf "$dir"
  mkdir -p "$dir"
  git -C "$dir" init -q -b "$branch" .
  git -C "$dir" config user.email test@example.invalid
  git -C "$dir" config user.name Test
}

# pass/fail_case <what this case is called> [<what was got>]
pass() {
  printf '  ok   %s\n' "$1"
  passed=$((passed + 1))
}

fail_case() {
  printf '  FAIL %s\n' "$1"
  [[ $# -gt 1 ]] && printf '%s\n' "$2"
  failed=$((failed + 1))
  return 0
}

# assert <output> <status> <want-status> <want-substring> <name>
#
# For a case whose command does not fit the shape `check` describes -- a second
# range, a negative assertion -- so that it still reports like every other one
# instead of being hand-rolled.
assert() {
  local output=$1 status=$2 want_status=$3 want_text=$4 name=$5
  if [[ "$status" -eq "$want_status" && "$output" == *"$want_text"* ]]; then
    pass "$name"
  else
    fail_case "$name" "$(printf '       wanted exit %s and %q\n       got exit %s:\n%s' \
      "$want_status" "$want_text" "$status" "$output")"
  fi
}

# expect [--flag...] <exit status> <substring> <what this case is called>
expect() {
  # A `--` ends the flags, so one that takes a value can be expressed:
  # `expect --words 8 -- 1 "..." "..."`. Without a sentinel only bare flags are
  # taken, which is the common case. Scanning for --* alone could not carry a
  # value at all -- it would be read as the wanted exit status, which is why
  # neither --words nor --epic had a case until now.
  local flags=() args=("$@") i sentinel=-1
  for ((i = 0; i < $#; i++)); do
    if [[ "${args[i]}" == "--" ]]; then
      sentinel=$i
      break
    fi
  done
  if ((sentinel >= 0)); then
    flags=(${args[@]:0:sentinel})
    set -- "${args[@]:sentinel + 1}"
  else
    while [[ "${1:-}" == --* ]]; do
      flags+=("$1")
      shift
    done
  fi
  local output status
  output=$("${CHECK_CMD[@]}" ${flags+"${flags[@]}"} ${CHECK_ARGS+"${CHECK_ARGS[@]}"} 2>&1)
  status=$?
  assert "$output" "$status" "$1" "$2" "$3"
}

# refute <output> <status> <want-status> <unwanted substring> <name>
#
# The one assertion the suites kept writing by hand, and the one most easily
# written backwards: two of the three hand-rolled copies put pass and fail on
# opposite branches from the third.
refute() {
  local output=$1 status=$2 want_status=$3 unwanted=$4 name=$5
  if [[ "$status" -eq "$want_status" && "$output" != *"$unwanted"* ]]; then
    pass "$name"
  else
    fail_case "$name" "$(printf '       wanted exit %s and no %q\n       got exit %s:\n%s' \
      "$want_status" "$unwanted" "$status" "$output")"
  fi
}

verdict() {
  echo
  if [[ "$failed" -eq 0 ]]; then
    echo "$passed passed."
    exit 0
  fi
  echo "$failed failed, $passed passed."
  exit 1
}
