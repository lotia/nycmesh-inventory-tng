#!/usr/bin/env bash
# What unexported.py must get right, and what it must never quietly do.
#
# Its answer IS the non-clobber guarantee -- export-issues.sh files an issue
# for everything this names, and `bd` overwrites an issue body for anything
# that already has one. So the cases that matter are the ones where a linked
# bead could slip into the list.
#
# Usage: unexported.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/testlib.sh"

workspace

OURS="https://github.com/lotia/nycmesh-inventory-tng"

EXPORT="$WORK/issues.jsonl"
{
  bead inventory-tng-aaa open "$OURS/issues/60"
  bead inventory-tng-bbb open
  bead inventory-tng-ccc closed
  bead inventory-tng-ddd closed "$OURS/issues/12"
  bead inventory-tng-eee deferred
} >"$EXPORT"

check() { python3 "$HERE/unexported.py" "$EXPORT"; }

out=$(check)
assert "$out" 0 0 "$(printf 'inventory-tng-bbb\topen')" "a bead with no ref is named, with its status"
assert "$out" 0 0 "$(printf 'inventory-tng-ccc\tclosed')" "a closed bead is named too, and says so"
assert "$out" 0 0 "$(printf 'inventory-tng-eee\tdeferred')" "so is one in any other state"

# The whole point. A bead that already has an issue must never be named,
# because naming it is what turns a create into a body-replacing PATCH.
refute "$out" 0 0 "inventory-tng-aaa" "a bead that already has an issue is never named"
refute "$out" 0 0 "inventory-tng-ddd" "not even when it is closed and looks like it needs attention"

assert "$(check | wc -l | tr -d ' ')" 0 0 "3" "and nothing else is in the list"

# File order, because a person reading a dry run against `bd list` should not
# have to work out why the two disagree.
assert "$(check | head -1 | cut -f1)" 0 0 "inventory-tng-bbb" "the export's own order is kept"

# ---------------------------------------------------------------------------
# Refusing rather than guessing
# ---------------------------------------------------------------------------

# A ref bd wrote in some shape this does not know is still a ref: reading it as
# absent files a SECOND issue for a bead that has one. The guard is
# unsynced.py's, and this pins that it is actually reached from here.
printf '%s' "$(bead inventory-tng-fff open 'lotia/nycmesh-inventory-tng#60')" >"$WORK/shorthand.jsonl"
outcome=$(python3 "$HERE/unexported.py" "$WORK/shorthand.jsonl" 2>&1)
assert "$outcome" "$?" 1 "does not recognise" "an unrecognised ref stops it rather than being read as unfiled"

# A row nothing could name is a row the export would drop in silence, which
# reads downstream exactly like a bead that already had an issue.
printf '%s\n' '{"_type":"issue","title":"no id","status":"open"}' >"$WORK/nameless.jsonl"
outcome=$(python3 "$HERE/unexported.py" "$WORK/nameless.jsonl" 2>&1)
assert "$outcome" "$?" 1 "no id" "a bead with no id is refused rather than skipped"

outcome=$(python3 "$HERE/unexported.py" "$WORK/absent.jsonl" 2>&1)
assert "$outcome" "$?" 1 "does not exist" "a missing export is refused rather than read as empty"

: >"$WORK/empty.jsonl"
# WRAPPED IN ANGLE BRACKETS, and the real exit status passed rather than a
# literal 0: an empty wanted substring is contained in every string, so the
# case as first written would have passed however much this printed.
outcome=$(python3 "$HERE/unexported.py" "$WORK/empty.jsonl")
assert "<$outcome>" "$?" 0 "<>" "an empty export names nothing, and does not fail"

# A bead bd wrote with no status at all reads as open rather than as nothing,
# so it is filed and left open -- the safe way round, since the closing pass
# only ever acts on the word "closed".
printf '%s\n' '{"_type":"issue","id":"inventory-tng-ggg","title":"t"}' >"$WORK/nostatus.jsonl"
assert "$(python3 "$HERE/unexported.py" "$WORK/nostatus.jsonl")" 0 0 \
  "$(printf 'inventory-tng-ggg\topen')" "a bead with no status is treated as open"

verdict
