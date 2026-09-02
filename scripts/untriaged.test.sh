#!/usr/bin/env bash
# What untriaged.py must get right about which beads still need a name.
#
# Usage: untriaged.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/testlib.sh"

workspace

URL="https://github.com/lotia/nycmesh-inventory-tng/issues"

export EXPORT="$WORK/issues.jsonl"
check() { python3 "$HERE/untriaged.py" "$EXPORT"; }

# The shape bd gives a pulled bead: prefix, arrival time in milliseconds, a
# counter, and a hex tail.
ARRIVED=inventory-tng-1788200756998-1-26030a28

{
  bead "$ARRIVED" "" "$URL/76" "The catalogue search is slow on my phone"
  bead inventory-tng-nz6t "" "$URL/60" "Take django-cors-headers out"
  bead inventory-tng-abc "" "" "An ordinary bead nobody pulled"
} >"$EXPORT"

out=$(check)
assert "$out" 0 0 "$ARRIVED" "a bead still carrying its arrival name is listed"
refute "$out" 0 0 "inventory-tng-nz6t" "a bead that was pushed, not pulled, is not waiting"
refute "$out" 0 0 "inventory-tng-abc" "a bead with no GitHub link is not waiting"

assert "$out" 0 0 "$URL/76" "the issue it came from is shown, so it can be read"
assert "$out" 0 0 "bd rename $ARRIVED" "the rename is spelled out with the id filled in"

# THE MARKER IS THE NAME, so triage needs no state of its own: renaming is what
# takes a bead off this list, and nothing has to be set or cleared.
{
  bead inventory-tng-slowsearch "" "$URL/76" "The catalogue search is slow on my phone"
} >"$EXPORT"
assert "$(check)" 0 0 "Nothing is waiting" "renaming alone takes a bead off the list"

# A closed arrival is nobody's work, whatever it is called.
{
  bead "$ARRIVED" closed "$URL/76" "Filed and withdrawn"
} >"$EXPORT"
assert "$(check)" 0 0 "Nothing is waiting" "a closed arrival is not waiting to be triaged"

# ---------------------------------------------------------------------------
# The search terms, which are the part that saves reading the whole tracker
# ---------------------------------------------------------------------------

{
  bead "$ARRIVED" "" "$URL/76" "The catalogue search is slow on my phone"
} >"$EXPORT"
out=$(check)
assert "$out" 0 0 "bd search catalogue" "a long, specific word is offered as a search"
refute "$out" 0 0 "bd search the" "a word that would match everything is not offered"
refute "$out" 0 0 "bd search on" "a short word is not offered"

# A title with nothing specific in it offers no searches rather than bad ones.
{
  bead "$ARRIVED" "" "$URL/76" "It does not work"
} >"$EXPORT"
out=$(check)
assert "$out" 0 0 "$ARRIVED" "an arrival with a vague title is still listed"
refute "$out" 0 0 "already tracked?" "and is offered no searches rather than useless ones"

# ---------------------------------------------------------------------------
# Refusing rather than guessing
# ---------------------------------------------------------------------------

outcome=$(python3 "$HERE/untriaged.py" "$WORK/absent.jsonl" 2>&1)
assert "$outcome" "$?" 1 "does not exist" "a missing export is refused rather than read as empty"

: >"$EXPORT"
assert "$(check)" 0 0 "Nothing is waiting" "an empty export is not an error"

verdict
