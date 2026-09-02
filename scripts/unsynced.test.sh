#!/usr/bin/env bash
# What unsynced.py must get right, and the ways it could be silently wrong.
#
# Usage: unsynced.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/testlib.sh"

workspace

OURS="https://github.com/lotia/nycmesh-inventory-tng"

# offer <number>...: what `gh issue list` is asked to print, for our repository.
offer() {
  local n
  for n in "$@"; do printf '%s\t%s/issues/%s\n' "$n" "$OURS" "$n"; done
}

export EXPORT="$WORK/issues.jsonl"
{
  bead inventory-tng-aaa "" "$OURS/issues/60"
  bead inventory-tng-bbb
  bead inventory-tng-ccc "" "$OURS/issues/12"
} >"$EXPORT"

check() { python3 "$HERE/unsynced.py" "$EXPORT"; }

assert "$(offer 60 61 12 62 | check)" 0 0 "61" "an issue no bead points at is offered"
assert "$(offer 60 12 | check)" 0 0 "" "issues already linked are not offered again"

out=$(offer 60 61 12 62 | check)
assert "$out" 0 0 "62" "every unlinked issue is offered, not just the first"
refute "$out" 0 0 "60" "a linked issue is never offered"
refute "$out" 0 0 "12" "a linked issue is never offered, whatever its position"

assert "$(offer 62 61 62 | check)" 0 0 "$(printf '62\n61')" "GitHub's order is kept, and a repeat is dropped"
assert "$(offer 99 | check)" 0 0 "99" "a bead with no external_ref links nothing"

# ---------------------------------------------------------------------------
# The comparison is on the whole URL, which is the point of the rewrite
# ---------------------------------------------------------------------------

# Issue numbers are per repository. An earlier version matched a pattern that
# accepted any repository's issue, so another project's 7 hid ours.
printf '%s' "$(bead inventory-tng-ddd "" 'https://github.com/someone/else/issues/7')" >"$WORK/other.jsonl"
assert "$(offer 7 | python3 "$HERE/unsynced.py" "$WORK/other.jsonl")" 0 0 "7" \
  "another repository's issue number does not mask ours"

# A pull request shares the number space with issues, and its URL is different.
printf '%s' "$(bead inventory-tng-eee "" "$OURS/pull/60")" >"$WORK/pr.jsonl"
assert "$(offer 60 | python3 "$HERE/unsynced.py" "$WORK/pr.jsonl")" 0 0 "60" \
  "a bead linked to a pull request does not claim the issue of the same number"

# ---------------------------------------------------------------------------
# Refusing rather than guessing
# ---------------------------------------------------------------------------

# The failure that would duplicate everything: bd changing how it records a
# link, so no ref matches any URL and every issue reads as never pulled.
printf '%s' "$(bead inventory-tng-fff "" 'lotia/nycmesh-inventory-tng#60')" >"$WORK/shorthand.jsonl"
outcome=$(offer 60 | python3 "$HERE/unsynced.py" "$WORK/shorthand.jsonl" 2>&1)
assert "$outcome" "$?" 1 "does not recognise" "a ref that is not a GitHub URL stops it rather than being skipped"

outcome=$(printf 'sixty\thttps://x/y\n' | check 2>&1)
assert "$outcome" "$?" 1 "number<TAB>url" "a line whose number is not a number is refused"

outcome=$(printf '60\n' | check 2>&1)
assert "$outcome" "$?" 1 "number<TAB>url" "a line with no URL is refused rather than half-read"

outcome=$(offer 60 | python3 "$HERE/unsynced.py" "$WORK/absent.jsonl" 2>&1)
assert "$outcome" "$?" 1 "does not exist" "a missing export is refused rather than read as empty"

assert "$(printf '' | check)" 0 0 "" "no input offers nothing, and does not fail"

verdict
