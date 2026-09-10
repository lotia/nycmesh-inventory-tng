#!/usr/bin/env bash
# Can anything under .beads/ reach a public repository by accident?
#
# THREE QUESTIONS, and what they share is the failure rather than the subject:
# each is a way for the tracker's state to be wrong while everything looks
# fine.
#
# The first is the reason this exists -- whether the directories bd keeps its
# state in are ones git will publish. The second is whether the mode the
# metadata names has storage to go with it. The third is whether the committed
# export is still being kept current, because a file that quietly stopped
# updating is state that misleads a commit as surely as one that should never
# have been in it.
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
# THE COMMITTED EXPORT HAS TO STILL BE TRUE. `.beads/issues.jsonl` is the one
# piece of tracker state this repository does commit, and
# scripts/check-batch.sh reads it to decide whether every issue a batch claims
# to close is closed. `export.auto` is what keeps it current; with that off the
# file keeps whatever it last said, and the landing gate answers from a record
# that has stopped moving.
#
# It fails in both directions and neither mentions the export: a finished batch
# refused because the file has not caught up, or an unfinished one passed
# because the file still shows work that has since been reopened.
#
# This is here rather than left to a comment beside the setting because
# `bd init` deleted it once already, and it was noticed only because somebody
# happened to be watching that file. Decision 0032 point 3 is the rule; this is
# the assertion it asks for.
# WHICHEVER SHAPE IT IS WRITTEN IN, and this is not fussiness. bd accepts the
# flat `export.auto: true`, the nested block, and the flow mapping, and
# `bd config get export.auto` answers `true` for all three -- measured against
# the binary rather than assumed. The nested form is the one bd's own commented
# template in this file documents, so a check that matched only the flat
# spelling would refuse a configuration bd considers correct, and refuse it in
# CI, on the documented shape.
#
# That is the doc-versus-behaviour gap decision 0032 point 4 is about, which
# would have been an unhappy thing to build into the check point 3 cites.
export_auto_is_on() {
  local file=$1
  grep -qE '^[[:space:]]*export\.auto:[[:space:]]*true([[:space:]]|$)' "$file" && return 0
  grep -qE '^[[:space:]]*export:[[:space:]]*\{[^}]*auto:[[:space:]]*true' "$file" && return 0
  # The block form: an `export:` at the start of a line, then `auto: true`
  # indented under it, ending at the next line that is not indented.
  awk '
    /^export:[[:space:]]*$/ { inblock = 1; next }
    inblock && /^[^[:space:]#]/ { inblock = 0 }
    inblock && /^[[:space:]]+auto:[[:space:]]*true([[:space:]]|$)/ { found = 1 }
    END { exit found ? 0 : 1 }
  ' "$file"
}

if [[ -f .beads/config.yaml ]]; then
  if ! export_auto_is_on .beads/config.yaml; then
    fail ".beads/config.yaml does not set export.auto: true"
    note "  .beads/issues.jsonl then stops being refreshed, and check-batch.sh reads it"
  fi
  # BOTH SETTINGS THAT RUN DELETED, not just the one whose loss was noticed.
  # `bd init --proxied-server` took `export.auto` and `sync.remote` together,
  # and checking only the first would be the "fix each instance and move on"
  # habit decision 0032 exists to break -- reproduced inside the check that
  # record cites, which would be a poor joke to leave in the tree.
  #
  # This one fails less quietly: `bd sync` errors without a remote. Less
  # quietly is not loudly, because nothing runs `bd sync` on a schedule here,
  # so the gap between losing it and finding out is however long it is until
  # somebody tries to publish.
  if ! grep -qE '^[[:space:]]*(sync\.)?remote:' .beads/config.yaml; then
    fail ".beads/config.yaml names no sync remote"
    note "  the tracker then has nowhere to publish to, and says so only when asked"
  fi
elif [[ -f .beads/metadata.json ]]; then
  # A workspace with a database but no config is one bd has rewritten or one
  # somebody trimmed; either way the setting cannot be there to find.
  fail ".beads/config.yaml is missing from an initialised workspace"
  note "  export.auto lives there, and without it the committed export goes stale"
fi

verdict "The tracker keeps its state where git cannot publish it." "committing"
