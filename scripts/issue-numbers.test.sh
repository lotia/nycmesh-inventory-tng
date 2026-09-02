#!/usr/bin/env bash
# What issue-numbers.py must get right, and what it must say rather than skip.
#
# This runs after several hundred issues have been filed, at the point where
# nothing can be undone cheaply. Its failure mode is silence: a bead the push
# passed over, or one whose number it could not work out, stays open on GitHub
# looking like live work with nothing saying which it was. So most of what
# follows is about what reaches stderr.
#
# Usage: issue-numbers.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/testlib.sh"

workspace

OURS="https://github.com/lotia/nycmesh-inventory-tng"

# filed <id> <status>...: the list export-issues.sh worked from, in pairs.
filed() {
  : >"$WORK/listing"
  while [[ $# -gt 0 ]]; do
    printf '%s\t%s\n' "$1" "$2" >>"$WORK/listing"
    shift 2
  done
}

EXPORTED="$WORK/exported"
{
  bead inventory-tng-aaa "" "$OURS/issues/60"
  bead inventory-tng-bbb "" "$OURS/issues/61"
  bead inventory-tng-ccc "" "$OURS/issues/62"
  bead inventory-tng-ddd
} >"$EXPORTED"

# The numbers, which are stdout. Findings are checked separately, because the
# whole point of the split is that a caller can read one without the other.
check() { python3 "$HERE/issue-numbers.py" "$WORK/listing" <"$EXPORTED" 2>/dev/null; }
findings() { python3 "$HERE/issue-numbers.py" "$WORK/listing" <"$EXPORTED" 2>&1 >/dev/null; }

echo "which issues to close"

filed inventory-tng-aaa closed inventory-tng-bbb open inventory-tng-ccc closed
out=$(check)
assert "$out" 0 0 "60" "a closed bead's issue number is offered for closing"
assert "$out" 0 0 "62" "and so is every other one, not just the first"
refute "$out" 0 0 "61" "an OPEN bead's issue is never offered, which is the whole distinction"

filed inventory-tng-bbb open
assert "<$(check)>" 0 0 "<>" "a list with nothing closed in it offers nothing"

# Only what this run filed. A closed bead somebody reopened on GitHub is a
# conflict for the ordinary reconciliation to settle, not for an export to
# overrule on its way past.
filed inventory-tng-aaa closed
out=$(check)
assert "$out" 0 0 "60" "the bead in the list is offered"
refute "$out" 0 0 "62" "a closed bead the run did not file is left alone"

echo
echo "what became of the rest"
# THE HALF THAT USED TO BE DONE IN SHELL. An OPEN bead the push passed over is
# in no closing pass, so if this reader does not mention it nothing ever will.

filed inventory-tng-ddd open
out=$(findings)
assert "$out" 0 0 "fail inventory-tng-ddd was passed over" \
  "an OPEN bead with no reference is a failure, not a skipped line"
assert "<$(check)>" 0 0 "<>" "and no number is invented for it"

filed inventory-tng-ddd closed
assert "$(findings)" 0 0 "fail inventory-tng-ddd was passed over" \
  "a CLOSED bead with no reference is the same failure, said the same way"

filed inventory-tng-zzz closed
assert "$(findings)" 0 0 "fail inventory-tng-zzz was named for filing and is not in the tracker" \
  "an id the export does not hold at all is named too"

filed inventory-tng-aaa closed inventory-tng-bbb open
assert "$(findings)" 0 0 "note All 2 named have an issue." \
  "a run with no gaps says so, so the caller has something to print"

# report.sh's vocabulary, because the caller dispatches these rather than
# deciding line by line what each one means.
filed inventory-tng-ddd open
refute "$(findings)" 0 0 "note inventory-tng-ddd" "a gap is never reported as a note"

echo
echo "reading a reference"
# The shape is unsynced.number_of's to know. What is pinned here is that this
# reader asks it rather than holding a second opinion.

printf '%s' "$(bead inventory-tng-hhh "" "$OURS/pull/60")" >"$EXPORTED"
filed inventory-tng-hhh closed
assert "$(findings)" 0 0 "names no issue to close" \
  "a ref that is not an issue reference yields no number, and says so"
assert "<$(check)>" 0 0 "<>" "and nothing is closed on the strength of it"

# The failure the anchoring exists for: a trailing number that is not an issue.
printf '%s' "$(bead inventory-tng-iii "" "$OURS/issues/60#issuecomment-99")" >"$EXPORTED"
filed inventory-tng-iii closed
refute "$(check)" 0 0 "99" "a comment fragment's number is never mistaken for the issue's"

echo
echo "refusing rather than guessing"

: >"$EXPORTED"
outcome=$(python3 "$HERE/issue-numbers.py" "$WORK/absent" <"$EXPORTED" 2>&1)
assert "$outcome" "$?" 1 "does not exist" "a missing list is refused rather than read as empty"

outcome=$(python3 "$HERE/issue-numbers.py" 2>&1)
assert "$outcome" "$?" 1 "usage" "and so is being given no list at all"

# The guard is unsynced.ref_of's, and this pins that it is reached from here --
# reading an unrecognised ref as absent is how a bead gets a second issue.
printf '%s' "$(bead inventory-tng-jjj "" 'lotia/nycmesh-inventory-tng#60')" >"$EXPORTED"
filed inventory-tng-jjj closed
outcome=$(python3 "$HERE/issue-numbers.py" "$WORK/listing" <"$EXPORTED" 2>&1)
assert "$outcome" "$?" 1 "does not recognise" "an unrecognised ref stops it rather than being read as missing"

verdict
