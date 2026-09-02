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
#
# THE VALUES ARE ESCAPED, because the title is now free text a case chooses --
# untriaged.test.sh's titles are sentences -- and the copy this replaced built
# its row with `json.dumps`. Interpolating a `"` or a `\` straight in emits a
# line no reader can parse, and the case fails somewhere else entirely.
#
# TWO SUBSTITUTIONS RATHER THAN A CALL TO PYTHON, and the honest limit of that:
# a control character in a value would still emit an invalid row. No fixture
# carries one -- these are ids, statuses, URLs and titles a case chose -- and it
# would fail loudly at the reader rather than quietly. A case that genuinely
# needs one should build its row the way landing-gate.test.sh's `body_is` does,
# through `json.dumps`. Done in-place because `bead` is called several hundred
# times in a full run, and a command substitution per field is a fork per field.
json_escape_into() {
  local -n out=$1
  out=${2//\\/\\\\}
  out=${out//\"/\\\"}
}

bead() {
  local id status ref title
  json_escape_into id "$1"
  json_escape_into status "${2:-}"
  json_escape_into ref "${3:-}"
  json_escape_into title "${4:-t}"
  printf '{"_type":"issue","id":"%s","title":"%s"' "$id" "$title"
  [[ -n "$status" ]] && printf ',"status":"%s"' "$status"
  [[ -n "$ref" ]] && printf ',"external_ref":"%s"' "$ref"
  printf '}\n'
}

# tracking_repo <checkout> <remote> -- a checkout that is level with an upstream.
#
# Two suites built this, because two scripts refuse to write from a checkout
# behind its remote and the question is asked of git itself, so a stub cannot
# answer it. The copies had already diverged in `catch_up` -- one fetched and
# one did not -- which is how a copy stops being noticed: neither was wrong.
#
# `catch_up` FETCHES, which settles that difference the smaller way. One caller
# does not need it, because the script under test has just fetched; against a
# local file remote the extra round trip is not worth a flag to turn off.
#
# Sets nothing and prints nothing: the caller already knows both paths, and a
# function whose output is captured cannot report a failure -- see `borrow`.
tracking_repo() {
  local checkout=$1 remote=$2
  new_repo "$remote"
  (cd "$remote" && git commit -q --allow-empty -m "root") || return 1
  new_repo "$checkout"
  git -C "$checkout" remote add origin "$remote"
  git -C "$checkout" fetch -q origin
  git -C "$checkout" reset -q --hard origin/main
  git -C "$checkout" branch -q --set-upstream-to=origin/main main
}

# fall_behind <remote> -- a commit on the remote the checkout has not fetched.
fall_behind() { (cd "$1" && git commit -q --allow-empty -m "somebody else's work"); }

# catch_up <checkout> -- level with the upstream again.
catch_up() {
  git -C "$1" fetch -q origin && git -C "$1" reset -q --hard origin/main
}

# borrow <directory> <tool>... -- a PATH holding exactly the named programs.
#
# Five suites built one of these, in four spellings. What they are for is the
# assertion a stub cannot make: "this script reaches for exactly these", so a
# dependency added without being thought about is caught by the suite rather
# than by somebody's laptop.
#
# REFUSES A TOOL IT CANNOT FIND rather than linking nothing and carrying on.
# Three of the four spellings suppressed that with `2>/dev/null`, which is the
# same shape as every other guard in this repository that turned out to fail
# open: a scene silently missing a program does not fail, it passes for the
# wrong reason -- or worse, exercises the "that program is absent" path while
# claiming to exercise the ordinary one.
#
# A scene that WANTS something absent leaves it out of the list, which says so.
# Overriding one with a stub is `ln -sf` over the top afterwards, which also
# says so.
#
# THE REFUSAL COUNTS, which needs `borrow` NOT to be run in a subshell. Two
# callers used to build the directory inside `$(...)` and print its path, so the
# FAIL landed inside the captured PATH and the count was thrown away with the
# subshell -- both failures silent, which is the thing this exists to stop. They
# set a variable instead now: bash's ordinary way of returning a string, and the
# one that does not put a function's reporting on its return channel.
borrow() {
  local dir=$1 tool found
  shift
  mkdir -p "$dir"
  for tool in "$@"; do
    found=$(command -v "$tool") || found=""
    # AN ABSOLUTE PATH OR NOTHING. `command -v printf` answers `printf`, because
    # the shell has one of its own, and `ln -s printf "$dir/printf"` is a link
    # to itself -- a scene missing the program while looking as though it holds
    # it. A builtin does not belong in a borrowed list at all: the shell under
    # test uses its own whatever the PATH says.
    if [[ "$found" != /* ]]; then
      fail_case "the scene needs $tool and this machine has no such program"
      continue
    fi
    ln -sf "$found" "$dir/$tool"
  done
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

# equals <got> <want> <name>
#
# For a case whose subject is a number rather than output -- a count of how many
# times a stub was called, most of the time. `assert` cannot do it: it matches a
# substring, so "1" passes for 11, 10 and 21. `exits` can, and was used for it,
# but then a genuine regression reports "wanted exit 1, got exit 3" about a
# count nothing exited with -- an assertion whose failure does not describe what
# it checked, which is the same fault `refuse_empty` guards one level up.
equals() {
  local got=$1 want=$2 name=$3
  if [[ "$got" == "$want" ]]; then
    pass "$name"
  else
    fail_case "$name" "$(printf '       wanted %s, got %s' "$want" "$got")"
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
