#!/usr/bin/env bash
# Which repository the GitHub scripts are talking to.
#
# Sourced, never run. Two tools have to agree on the answer and they disagree
# about how to find it: `gh` reads the checkout and works it out for itself,
# while `bd` reads `github.owner`/`github.repo` or `GITHUB_REPOSITORY` and
# refuses per issue when neither is set. Resolving separately is how one
# script came to list one repository's issues and pull them from another,
# linking beads to whatever wore that number there.
#
# So the answer is settled once and exported, and a caller needs only an
# authenticated `gh`.
#
# Sourced the way report.sh is, from the directory the caller is in:
#
#   . "$HERE/repository.sh"
#   resolve_repository "$argument"     # "" when nobody named one
#   # $REPOSITORY and $GITHUB_REPOSITORY are now the same string

# need_tools <tool>...
#
# Refuses, once and by name, before anything is asked of GitHub. Here rather
# than in each caller because the division it encodes is itself a repair: a
# reader-only invocation answers from the committed export and needs no `bd`,
# and demanding one refused the question in the single place it is wanted
# WITHOUT a tracker to write to, which is CI. Stated in three scripts, that
# division drifts.
#
# Exit 2 rather than 1, matching every other "it could not look" in this family
# and distinguishing it from "it looked and objected".
need_tools() {
  local tool
  for tool in "$@"; do
    command -v "$tool" >/dev/null 2>&1 || {
      echo "${BASH_SOURCE[1]##*/}: $tool is needed and is not on the path." >&2
      exit 2
    }
  done
}

# resolve_repository [<owner/name>]
#
# Takes what the caller was told, falls back to GITHUB_REPOSITORY, then to what
# `gh` makes of the checkout. Exits 2 if none of the three answers, because
# every caller of this would otherwise go on to ask GitHub a question with no
# subject.
#
# The caller is not asked its own name: BASH_SOURCE[1] inside a function
# defined in a sourced file is the script that called it, the same way
# report.sh's `relay` gets it.
resolve_repository() {
  local named=${1:-} resolved
  resolved=${named:-${GITHUB_REPOSITORY:-}}
  if [[ -z "$resolved" ]]; then
    resolved=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null)
  fi
  if [[ -z "$resolved" ]]; then
    echo "${BASH_SOURCE[1]##*/}: could not work out which repository to use." >&2
    exit 2
  fi
  REPOSITORY="$resolved"
  export REPOSITORY GITHUB_REPOSITORY="$resolved"
}
