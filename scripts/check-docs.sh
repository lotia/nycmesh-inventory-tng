#!/usr/bin/env bash
# One topic, one place -- the part of it a machine can see.
#
# What this reads and why is DEVELOPERS.md#1-one-topic-one-place. How it does
# it: cut each file's prose into overlapping runs of words and report any run
# that turns up in two of them.
#
# Usage: check-docs.sh [--words N] [<path>...]
#
# scripts/check-docs.allow holds the repetitions that are meant to be there.
# Its own header says how an entry is written.

set -uo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel) || exit 1
cd "$REPO_ROOT" || exit 1

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/report.sh"

WORDS=12
paths=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --words)
      WORDS=${2:?--words needs a number}
      shift 2
      ;;
    *)
      paths+=("$1")
      shift
      ;;
  esac
done

if [[ ${#paths[@]} -eq 0 ]]; then
  # Stated as what is *not* read, so a file added or moved is read by default.
  # Listing the places to look meant a helper leaving scripts/ stopped being
  # checked with no signal, and ci.yml -- whose comments repo-settings.sh's
  # whole design rests on -- was never read at all.
  #
  # Application code is read too, for the same reason: a docstring is where a
  # decision record gets paraphrased. What that costs is that a docstring
  # beside the invariant it enforces looks the same to a machine, so the
  # judgement moves into check-docs.allow. See
  # DEVELOPERS.md#1-one-topic-one-place.
  #
  # It said that and then listed seven extensions, which is the same mistake
  # one layer down: `.env.sample` is where every configuration variable in this
  # repository is explained and matched none of them, and neither did the
  # extensionless programs under scripts/ -- so the two files whose whole job
  # is explaining things were the two nothing read. Three commits pasted prose
  # into `.env.sample` before a review noticed.
  #
  # So the corpus is stated as a subtraction, and the pattern below is the whole
  # of it. What keeps a file out is that its prose is nobody's here, which comes
  # out as three clauses rather than one tidy rule -- extensions, for what is
  # not text; two names, for the lock files a resolver writes; and one
  # directory. The directory is the tracker's: the exports are data, the five
  # generated hooks carry beads' own banner and reading them reports four
  # repetitions nothing here can fix, and the README came with the tool. It is
  # the only path excluded for where it is, and it is worth being uneasy about
  # -- a `.beads/` holding something of ours would go unread and say nothing.
  # DEVELOPERS.md#1-one-topic-one-place puts that in words.
  #
  # `--others --exclude-standard` alongside `--cached` is what makes the answer
  # here the answer CI gives. A bare `git ls-files` enumerates the index, so a
  # page written and not yet committed is invisible to a run of this and plainly
  # visible to the job that runs after it is -- which is a checker passing on a
  # corpus that is not the one under test. `--others` adds what git can see and
  # is not tracking; `--exclude-standard` keeps .gitignore deciding, so a build
  # artefact and a node_modules stay out. Asking an author to `git add -N` first
  # was the alternative, and a guard that only works when you remember a flag is
  # not one. inventory-tng-hoc6, after it cost two runs in one sitting.
  mapfile -t paths < <(
    git ls-files --cached --others --exclude-standard | grep -Evi \
      '\.(png|jpe?g|gif|ico|svg|woff2?|ttf|eot|wasm|xlsx|pdf|zip)$|(^|/)(uv\.lock|package-lock\.json)$|^\.beads/'
  )
fi

ALLOW="$REPO_ROOT/scripts/check-docs.allow"

# The corpus is every source file in the checkout, so the crash surface is
# large and the rule this guards is the one AGENTS.md calls non-negotiable --
# which is why `relay` exists rather than a bare assignment whose exit status
# nothing reads.
relay env WORDS="$WORDS" ALLOW="$ALLOW" python3 "$HERE/check-docs.py" "${paths[@]}"

verdict "No prose repeated across files in runs of $WORDS words or more." \
  "one of each pair is a link"
