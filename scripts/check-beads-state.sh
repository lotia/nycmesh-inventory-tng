#!/usr/bin/env bash
# Can anything under .beads/ reach a public repository by accident?
#
# TWO QUESTIONS, and the first one is the reason this exists: whether the
# directories bd has decided to keep its state in are ones git will publish.
# The second is narrower and local -- whether the mode the metadata names has
# storage to go with it -- and it is here because it is the same file, the
# same one-line read, and the failure it catches is silent in the same way.
#
# What this deliberately does NOT ask is whether beads is configured well. bd
# answers that for itself and changes its mind between versions.
#
# WHY THAT IS WORTH A SCRIPT. The tracker's storage directory is chosen by the
# backend mode, and the mode changes -- `.beads/embeddeddolt` under embedded,
# `.beads/dolt` under proxied-server. Each mode names its own directory, and
# `.beads/.gitignore` has to have heard of it. It nearly did not: the flag that
# turns proxied-server mode on documents `.beads/proxieddb` and the mode
# actually uses `.beads/dolt`, so a `.gitignore` written from the documentation
# would have covered a directory that is never created and missed the one that
# holds the database.
#
# A Dolt database is not a file somebody skims before committing. It is tens of
# thousands of files and about a hundred megabytes, and this repository is
# public by decision 0029 -- so the failure being prevented is a working set
# published in full, including whatever was in it, without anybody seeing a
# diff they could have read.
#
# SO THE RULE IS THE INVARIANT RATHER THAN THE LIST: every directory under
# .beads/ is either tracked on purpose or ignored on purpose, and a new one
# that is neither is what this refuses. That way the next mode -- or the next
# version of bd -- is caught by a rule that did not have to know about it.
#
# Usage: check-beads-state.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")

REPO_ROOT=$(git rev-parse --show-toplevel) || exit 1
cd "$REPO_ROOT" || exit 1

. "$HERE/report.sh"

if [[ ! -d .beads ]]; then
  note "no .beads directory here, so there is no tracker state to publish"
  verdict "Nothing to check." "committing"
fi

# Every directory beads keeps, whatever it is called this week.
while IFS= read -r dir; do
  [[ -n "$dir" ]] || continue

  # Tracked on purpose is a fine answer: .beads/hooks is a directory of
  # symlinks that has to arrive with the clone, and check-setup.sh is the
  # script that cares whether it did.
  if [[ -n "$(git ls-files "$dir")" ]]; then
    continue
  fi

  # The trailing slash matters and is the reason this is not a one-liner
  # somebody types by hand: `.gitignore` patterns ending in `/` match
  # directories only, and `git check-ignore .beads/proxieddb` answers "not
  # ignored" for a directory that is ignored, purely because the path it was
  # handed had no slash on the end.
  if git check-ignore -q "$dir/"; then
    continue
  fi

  fail "$dir is neither tracked nor ignored, so a commit here would publish it"
  note "  add it to .beads/.gitignore, or track it if it is meant to travel"
done < <(find .beads -mindepth 1 -maxdepth 1 -type d | sort)

# And the mode's own directory has to be there at all. A workspace whose
# metadata names a mode whose storage is missing is one where bd will either
# refuse to open or quietly start an empty database, and the second is the
# expensive one -- 454 issues reading as none, with a warning rather than an
# error.
#
# THAT IS A QUESTION ABOUT A LOCAL WORKSPACE, AND ONLY A LOCAL ONE.
# `.beads/metadata.json` is gitignored -- it is bd's pointer at a database that
# is itself gitignored -- so a fresh clone, a CI checkout included, has neither
# the file nor the database, and has still done nothing wrong. Failing there
# would fail every run of this on a runner, which is exactly what it did the
# first time it was wired into ci.yml.
if [[ ! -e .beads/metadata.json ]]; then
  note "no .beads/metadata.json here, so bd has not been initialised in this checkout"
  note "  run \`bd init\` before using the tracker; nothing about storage is checked"
elif ! MODE=$(sed -n 's/.*"dolt_mode"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' .beads/metadata.json) ||
  [[ -z "$MODE" ]]; then
  fail ".beads/metadata.json names no dolt_mode, so which storage is authoritative is a guess"
  note "  bd writes this on init; without it bd falls back to a database named \"beads\""
else
  case "$MODE" in
    embedded) WANT=.beads/embeddeddolt ;;
    proxied-server | server | shared-server) WANT=.beads/dolt ;;
    *) WANT= ;;
  esac
  if [[ -z "$WANT" ]]; then
    note "dolt_mode is \"$MODE\", which this script has not been taught; storage not checked"
  elif [[ ! -d "$WANT" ]]; then
    fail "dolt_mode is \"$MODE\" but $WANT does not exist"
    note "  bd would open an empty database and say so only as a warning"
  fi
fi

# The second argument is not decoration: `verdict` prints "N things to fix
# before <this>" on the failure path, and under `set -u` a caller that omits it
# dies with "$2: unbound variable" instead of ever saying what is wrong.
verdict "The tracker keeps its state where git cannot publish it." "committing"
