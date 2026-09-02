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
#
# Also the point at which the machine stops being an input. A `git init` here
# still reads the person's own global and system configuration, so a setting
# any of them carries -- `core.hooksPath` is the one that caught this -- turns
# a suite red on their laptop and stays green on a bare runner, which is the
# worst way round. /dev/null reads as a configuration with nothing in it.
workspace() {
  WORK=$(mktemp -d)
  # shellcheck disable=SC2064  # $WORK is wanted now, not when the trap fires
  trap "rm -rf '$WORK'" EXIT
  export WORK
  export GIT_CONFIG_GLOBAL=/dev/null
  export GIT_CONFIG_SYSTEM=/dev/null
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

# bead <id> [<status>] [<external_ref>] -- one line of a `bd export`.
#
# Here rather than in each suite because the shape of an export row -- `_type`,
# `id`, `title`, `status`, `external_ref` -- reached five copies across four
# files, and it is precisely the shape unsynced.py stops the world over when it
# changes. Five fixtures that quietly kept an old shape would go green against
# a reader that no longer accepts it, which is the failure the refusal exists to
# make loud.
#
# The status and the ref are optional because a bead really can lack either, and
# a reader that treats a missing one differently from an empty one is a reader
# worth being able to test.
bead() {
  local id=$1 status=${2:-} ref=${3:-}
  printf '{"_type":"issue","id":"%s","title":"t"' "$id"
  [[ -n "$status" ]] && printf ',"status":"%s"' "$status"
  [[ -n "$ref" ]] && printf ',"external_ref":"%s"' "$ref"
  printf '}\n'
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

# refuse_empty <the substring> <case name>
#
# THE DETERMINISTIC HALF OF inventory-tng-twew, and the reason it is here rather
# than in a review checklist. `assert` matches with `== *"$want_text"*`, which is
# true of every string when the substring is empty, and `refute` is then false of
# every string. Neither is ever what somebody meant: an assertion that cannot
# fail passes over the very thing it was written to catch, and one that cannot
# pass is noticed immediately.
#
# It reached four files before anything spotted it, including a case whose own
# comment called it "the case that matters" and which would have stayed green
# with the guard it named deleted. A rule written down would have been read by
# whoever was already being careful.
#
# Checking only the exit status is a real thing to want -- `exits` below is how
# to say it, and saying it that way makes the intent legible instead of hiding
# it in an argument that looks like an oversight.
refuse_empty() {
  [[ -n "$1" ]] && return 0
  # Which function is being misused is not asked for: FUNCNAME[1] inside a
  # function called by `assert` is `assert`, the same trick report.sh's `relay`
  # uses so that a caller does not write its own name out a second time.
  fail_case "$2" "$(printf '       %s was given an empty substring, so it could not fail. Use `exits`\n       if only the exit status is meant to be checked.' "${FUNCNAME[1]}")"
  return 1
}

# exits <status> <want-status> <name>
#
# For a case whose subject produces no output worth reading -- a `grep -q`, a
# command run for its exit status alone. Separate from `assert` so that "there
# is nothing to match here" is written rather than expressed as an empty
# expectation, which is indistinguishable from having forgotten one.
exits() {
  local status=$1 want_status=$2 name=$3
  if [[ "$status" -eq "$want_status" ]]; then
    pass "$name"
  else
    fail_case "$name" "$(printf '       wanted exit %s, got exit %s' "$want_status" "$status")"
  fi
}

# assert <output> <status> <want-status> <want-substring> <name>
#
# For a case whose command does not fit the shape `check` describes -- a second
# range, a negative assertion -- so that it still reports like every other one
# instead of being hand-rolled.
assert() {
  local output=$1 status=$2 want_status=$3 want_text=$4 name=$5
  refuse_empty "$want_text" "$name" || return 0
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
  refuse_empty "$unwanted" "$name" || return 0
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
