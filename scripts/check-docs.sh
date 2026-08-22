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
  mapfile -t paths < <(
    git ls-files '*.md' '*.sh' '*.py' '*.yml' '*.yaml' '*.ts' '*.tsx'
  )
fi

ALLOW="$REPO_ROOT/scripts/check-docs.allow"

findings=$(WORDS="$WORDS" ALLOW="$ALLOW" python3 "$HERE/check-docs.py" "${paths[@]}") || {
  # Tested, because the assignment's status is not: without this a traceback
  # inside the heredoc leaves findings empty, the loop below emits nothing,
  # and the all-clear prints over a checker that read nothing at all. The
  # corpus is every tracked source file now, so the crash surface is large
  # and the rule this guards is the one CLAUDE.md calls non-negotiable.
  echo "check-docs.sh: the reader failed, so nothing was checked." >&2
  exit 2
}

while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  case "$line" in
    fail\ *) fail "${line#fail }" ;;
    note\ *) note "${line#note }" ;;
  esac
done <<<"$findings"

verdict "No prose repeated across files in runs of $WORDS words or more." \
  "one of each pair is a link"
