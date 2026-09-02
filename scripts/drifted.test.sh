#!/usr/bin/env bash
# What drifted.py must call a disagreement, and what it must not.
#
# It runs on a schedule and its output sends somebody to look, so a false
# positive costs more than most: it is a red check about work that is fine. The
# cases below are mostly about the things that LOOK like drift and are not --
# each of which belongs to a different reader.
#
# Usage: drifted.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/testlib.sh"

workspace

REPOSITORY="lotia/nycmesh-inventory-tng"
OURS="https://github.com/$REPOSITORY"

# live <number> <state>...: what `gh issue list` is asked to print, in pairs.
live() {
  : >"$WORK/live"
  while [[ $# -gt 0 ]]; do
    printf '%s\t%s\n' "$1" "$2" >>"$WORK/live"
    shift 2
  done
}

EXPORT="$WORK/issues.jsonl"
check() { python3 "$HERE/drifted.py" "$EXPORT" "$REPOSITORY" <"$WORK/live"; }

echo "the disagreement itself"

bead inventory-tng-aaa closed "$OURS/issues/60" >"$EXPORT"
live 60 OPEN
out=$(check)
assert "$out" 0 0 "inventory-tng-aaa" "a closed bead whose issue is open is named"
assert "$out" 0 0 "#60" "with the issue number, so it can be acted on"
assert "$out" 0 0 "closed here, open on GitHub" "and which way round it runs"

bead inventory-tng-bbb open "$OURS/issues/61" >"$EXPORT"
live 61 CLOSED
assert "$(check)" 0 0 "open here, closed on GitHub" "and the other direction is a disagreement too"

live 61 OPEN
assert "<$(check)>" 0 0 "<>" "a pair that agrees is not reported"

bead inventory-tng-ccc closed "$OURS/issues/62" >"$EXPORT"
live 62 CLOSED
assert "<$(check)>" 0 0 "<>" "and neither is one that agrees on being closed"

echo
echo "what is somebody else's question"
# Each of these looks like drift and is not. Reporting them would send a person
# to look at something the reader that owns it has already accounted for.

bead inventory-tng-ddd closed >"$EXPORT"
live 60 OPEN
assert "<$(check)>" 0 0 "<>" "a bead with no issue is unfiled, not out of step -- unexported.py owns it"

bead inventory-tng-eee closed "$OURS/issues/60" >"$EXPORT"
live 60 OPEN 99 OPEN
refute "$(check)" 0 0 "#99" "an issue no bead points at is unpulled -- unsynced.py owns it"

# A ref naming an issue this repository has never returned is not drift; it is
# a reference to something that does not exist, and no state can be compared.
bead inventory-tng-fff closed "$OURS/issues/9999" >"$EXPORT"
live 60 OPEN
assert "<$(check)>" 0 0 "<>" "a ref to an issue GitHub did not return is passed over rather than guessed at"

# A pull request shares the number space, and issue_of does not read one.
bead inventory-tng-ggg closed "$OURS/pull/60" >"$EXPORT"
live 60 OPEN
assert "<$(check)>" 0 0 "<>" "a bead linked to a pull request does not claim the issue of that number"

# inventory-tng-cwpa.12, and the reader's own header says why it is the first
# one that had to ask. What is pinned here is the answer: a bead wearing
# somebody else's #60 is not this listing's #60.
bead inventory-tng-lll closed "https://github.com/someone/else/issues/60" >"$EXPORT"
live 60 OPEN
assert "<$(check)>" 0 0 "<>" "another repository's issue of the same number is not this listing's"

# And the same bead against the same listing IS drift once the repository it
# names is the one being compared -- which is what pins the skip above on the
# repository rather than on the ref being unreadable.
assert "$(python3 "$HERE/drifted.py" "$EXPORT" someone/else <"$WORK/live")" 0 0 \
  "closed here, open on GitHub" "and is drift when that repository is the one asked about"

# Case is not part of a repository's identity -- `issue_of` says why. What it
# would cost HERE is a disagreement nobody hears about.
bead inventory-tng-mmm closed "$OURS/issues/60" >"$EXPORT"
live 60 OPEN
assert "$(python3 "$HERE/drifted.py" "$EXPORT" "LOTIA/NYCMesh-Inventory-TNG" <"$WORK/live")" 0 0 \
  "inventory-tng-mmm" "a repository named in another casing is still the same repository"

echo
echo "states GitHub does not have"
# The tracker has four; GitHub has two. Only closed-against-open is a conflict.

for status in open in_progress blocked deferred; do
  bead inventory-tng-hhh "$status" "$OURS/issues/60" >"$EXPORT"
  live 60 OPEN
  assert "<$(check)>" 0 0 "<>" "a $status bead against an open issue is not a disagreement"
done

bead inventory-tng-iii deferred "$OURS/issues/60" >"$EXPORT"
live 60 CLOSED
assert "$(check)" 0 0 "open here, closed on GitHub" "but a deferred bead against a CLOSED issue is"

echo
echo "refusing rather than guessing"

bead inventory-tng-jjj closed "$OURS/issues/60" >"$EXPORT"
outcome=$(printf '60\tMERGED\n' | python3 "$HERE/drifted.py" "$EXPORT" "$REPOSITORY" 2>&1)
assert "$outcome" "$?" 1 "OPEN|CLOSED" "a state it does not recognise is refused, not read as not-closed"

outcome=$(printf 'sixty\tOPEN\n' | python3 "$HERE/drifted.py" "$EXPORT" "$REPOSITORY" 2>&1)
assert "$outcome" "$?" 1 "number<TAB>" "a number that is not a number is refused"

# Reached through to unsynced.ref_of, whose refusal is the thing that stops a
# ref nobody recognises being read as no ref at all.
bead inventory-tng-kkk closed 'lotia/nycmesh-inventory-tng#60' >"$EXPORT"
live 60 OPEN
outcome=$(check 2>&1)
assert "$outcome" "$?" 1 "does not recognise" "an unrecognised ref stops it rather than being skipped"

outcome=$(python3 "$HERE/drifted.py" "$WORK/absent.jsonl" "$REPOSITORY" <"$WORK/live" 2>&1)
assert "$outcome" "$?" 1 "does not exist" "a missing export is refused rather than read as empty"

# A row nothing can name is refused rather than reported under a made-up one --
# the same policy unexported.py applies to the same row.
printf '%s\n' '{"_type":"issue","status":"closed","external_ref":"'"$OURS"'/issues/60"}' >"$EXPORT"
live 60 OPEN
outcome=$(check 2>&1)
assert "$outcome" "$?" 1 "no id" "a bead with no id stops it rather than being named after its line"

: >"$EXPORT"
assert "<$(check)>" 0 0 "<>" "an empty export reports nothing, and does not fail"

verdict
