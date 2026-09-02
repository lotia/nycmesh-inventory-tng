#!/usr/bin/env bash
# What unsaid.py puts on an issue, and what it refuses to.
#
# The comment it composes is written to a PUBLIC repository, once per bead with
# something to say, so the cases here are mostly about restraint: a bead whose
# issue already says everything gets nothing at all, and a number is only ever
# written as `#n` when it is this repository's number.
#
# Usage: scripts/unsaid.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/testlib.sh"

workspace

REPOSITORY="lotia/nycmesh-inventory-tng"
OURS="https://github.com/$REPOSITORY"
EXPORT="$WORK/issues.jsonl"

# say [<bead>] -- the reader, against whatever $EXPORT currently holds.
say() { python3 "$HERE/unsaid.py" "$EXPORT" "$REPOSITORY" "$@"; }

echo "what a bead adds to its issue"

rich inventory-tng-aaa '{"acceptance_criteria":"The suite is green on main."}' "$OURS/issues/60" >"$EXPORT"
out=$(say inventory-tng-aaa)
assert "$out" 0 0 "Done when" "acceptance criteria are titled for the question they answer"
assert "$out" 0 0 "The suite is green on main." "and the text itself is carried over"
assert "$out" 0 0 "<!-- bead-fields " "with the marker that lets a second run rewrite this one"

# THE DIGEST IS WHAT MAKES THE MARKER CHEAP TO COMPARE, so it has to be stable
# for the same body and different for a different one -- see `stamped`. A
# constant would make every comment look current for ever, which is the failure
# nothing else here would notice.
first=$(say inventory-tng-aaa | head -1)
assert "$(say inventory-tng-aaa | head -1)" 0 0 "$first" "and the same bead stamps the same digest twice running"
rich inventory-tng-aaa '{"acceptance_criteria":"Something else entirely."}' "$OURS/issues/60" >"$EXPORT"
refute "$(say inventory-tng-aaa | head -1)" 0 0 "$first" "and a bead that changed stamps a different one"
rich inventory-tng-aaa '{"acceptance_criteria":"The suite is green on main."}' "$OURS/issues/60" >"$EXPORT"
assert "$out" 0 0 "inventory-tng-aaa" "and the bead it was written from, so a reader can go and look"

rich inventory-tng-bbb '{"design":"A predicate, not a wrapper.","notes":"Measured on 2026-09-02."}' \
  "$OURS/issues/61" >"$EXPORT"
out=$(say inventory-tng-bbb)
assert "$out" 0 0 "A predicate, not a wrapper." "a design section is carried"
assert "$out" 0 0 "Measured on 2026-09-02." "and so are notes"

# THE ORDER IS THE ONE A READER WANTS rather than the one the fields are stored
# in: what the shape of it is, then what would finish it, then the working
# material. Pinned as the sequence of headings, because asserting that each
# appears says nothing about the order they appear in.
rich inventory-tng-bbb \
  '{"design":"d","acceptance_criteria":"a","notes":"n","dependencies":[{"type":"parent-child","issue_id":"inventory-tng-bbb","depends_on_id":"inventory-tng-aaa"}]}' \
  "$OURS/issues/61" >"$EXPORT"
headings=$(say inventory-tng-bbb | grep -oE '\*\*(Design|Done when|Notes|Part of)\*\*' | tr '\n' ' ')
equals "$headings" "**Design** **Done when** **Notes** **Part of** " \
  "the sections come in the order a reader wants, with the links last"

echo
echo "a bead with nothing to add"
# ROUGHLY A THIRD OF THE TRACKER. A heading with nothing under it, on every one
# of those issues, is worse than the silence it replaces.

rich inventory-tng-ccc '{}' "$OURS/issues/62" >"$EXPORT"
out=$(say inventory-tng-ccc)
assert "<$out>" "$?" 0 "<>" "a bead whose issue already says everything gets no comment at all"

assert "$(say)" 0 0 "$(printf 'inventory-tng-ccc\t62\t')" \
  "and its row carries no marker, which is how the poster knows to take a stale comment down"

echo
echo "what the tracker holds and an issue body cannot"

rich inventory-tng-ddd '{"dependencies":[{"type":"parent-child","issue_id":"inventory-tng-ddd","depends_on_id":"inventory-tng-eee"}]}' "$OURS/issues/63" >"$EXPORT"
rich inventory-tng-eee '{}' "$OURS/issues/64" >>"$EXPORT"
out=$(say inventory-tng-ddd)
assert "$out" 0 0 "Part of** #64" "a child says what it is part of, as this repository's issue number"

# THE OTHER END OF THE SAME ROW, and the reason the reader indexes the whole
# export first: parent-child is recorded on the CHILD, so an epic's own row says
# nothing about what it holds and reads on GitHub as a description with nothing
# under it. inventory-tng-cwpa.15.
out=$(say inventory-tng-eee)
assert "$out" 0 0 "Holds** #63" "and the parent lists what it holds, which its own row does not say"

# THE DIRECTION IS THE OPPOSITE OF THE TYPE'S NAME, and rendering it the other
# way put the reverse of the truth on a public issue. `bd dep add <a> <b>`
# stores a `blocks` row on `a` meaning "a is blocked by b" -- its help calls
# --blocked-by and --depends-on aliases -- so the bead carrying the row is the
# one that is stuck, not the one doing the stopping.
rich inventory-tng-fff '{"dependencies":[{"type":"blocks","issue_id":"inventory-tng-fff","depends_on_id":"inventory-tng-eee"}]}' "$OURS/issues/65" >"$EXPORT"
rich inventory-tng-eee '{}' "$OURS/issues/64" >>"$EXPORT"
out=$(say inventory-tng-fff)
assert "$out" 0 0 "Blocked by** #64" "a bead waiting on another says which one it waits for"
refute "$out" 0 0 "Blocks*" "and never claims to be the one doing the blocking"

echo
echo "a number is only ever ours"
# inventory-tng-cwpa.12's rule, at a caller that would have made the same
# mistake: `#64` on GitHub means issue 64 OF THIS REPOSITORY, so writing one for
# a bead linked elsewhere would point a reader at unrelated work.

rich inventory-tng-ggg '{"dependencies":[{"type":"parent-child","issue_id":"inventory-tng-ggg","depends_on_id":"inventory-tng-hhh"}]}' "$OURS/issues/66" >"$EXPORT"
rich inventory-tng-hhh '{}' 'https://github.com/someone/else/issues/64' >>"$EXPORT"
out=$(say inventory-tng-ggg)
assert "$out" 0 0 'Part of** `inventory-tng-hhh`' "a bead linked to another repository is named by its id"
refute "$out" 0 0 "#64" "and never by a number that means something else here"

rich inventory-tng-iii '{"dependencies":[{"type":"parent-child","issue_id":"inventory-tng-iii","depends_on_id":"inventory-tng-jjj"}]}' "$OURS/issues/67" >"$EXPORT"
rich inventory-tng-jjj '{}' >>"$EXPORT"
assert "$(say inventory-tng-iii)" 0 0 'Part of** `inventory-tng-jjj`' \
  "and so is one GitHub has never heard of"

echo
echo "which beads have something to say"
# What the poster iterates. An issue is needed as well as something to say:
# there is nowhere to put a comment for a bead nothing has filed.

{
  rich inventory-tng-kkk '{"notes":"something"}' "$OURS/issues/68"
  rich inventory-tng-lll '{"notes":"something"}'
  rich inventory-tng-mmm '{}' "$OURS/issues/69"
} >"$EXPORT"
out=$(say)
assert "$out" 0 0 "$(printf 'inventory-tng-kkk\t68\t<!-- bead-fields ')" \
  "a bead with something to say is offered with its number and its marker"
refute "$out" 0 0 "inventory-tng-lll" "a bead with no issue is not -- there is nowhere to put a comment"
assert "$out" 0 0 "$(printf 'inventory-tng-mmm\t69\t\n')" \
  "and one with nothing to say is listed with an empty marker rather than left out"

# THE MARKER IN THE ROW IS THE ONE THE COMMENT OPENS WITH, which is the whole
# reason the caller can decide without asking again: composing the comment is
# how this knows there is anything to say, so throwing the body away made the
# caller re-render every bead in a fresh interpreter.
assert "$(say)" 0 0 "$(say inventory-tng-kkk | head -1)" \
  "and it is the same line the rendering itself opens with"

# WHY SILENT IS LISTED AT ALL, which is the case nothing else would catch: the
# fields a bead carries can be taken away, and the comment on its issue then
# says something the tracker no longer does. A caller that only ever heard about
# the talkative ones could not learn such an issue existed.
rich inventory-tng-kkk '{}' "$OURS/issues/68" >"$EXPORT"
assert "$(say)" 0 0 "$(printf 'inventory-tng-kkk\t68\t\n')" \
  "a bead that loses what it had is offered again, with nothing in the marker column"

echo
echo "refusing rather than guessing"

printf '%s\n' '{"_type":"issue","notes":"orphaned"}' >"$EXPORT"
outcome=$(say 2>&1)
assert "$outcome" "$?" 1 "no id" "a bead nothing can name stops it rather than being skipped"

# Reached through to unsynced.ref_of. A ref shaped like nothing bd writes means
# either corruption or bd having changed how it records the link, and reading it
# as absent would quietly render fewer links on every issue.
rich inventory-tng-nnn '{"notes":"x"}' 'lotia/nycmesh-inventory-tng#60' >"$EXPORT"
outcome=$(say 2>&1)
assert "$outcome" "$?" 1 "does not recognise" "an unrecognised ref stops it rather than being passed over"

rich inventory-tng-ooo '{"notes":"x"}' "$OURS/issues/70" >"$EXPORT"
outcome=$(say inventory-tng-never 2>&1)
assert "$outcome" "$?" 1 "no bead in the export is called" "a bead that is not there is named, not rendered empty"

outcome=$(python3 "$HERE/unsaid.py" "$WORK/absent.jsonl" "$REPOSITORY" 2>&1)
assert "$outcome" "$?" 1 "does not exist" "a missing export is refused rather than read as empty"

: >"$EXPORT"
out=$(say)
assert "<$out>" "$?" 0 "<>" "an empty export offers nothing, and does not fail"

verdict
