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

# refuse <first line> [<more lines>...]
#
# Stops at exit 2, the code this family uses for "it could not look" as against
# "it looked and objected". Named because the guards below spell the same four
# lines of `echo ... >&2` each, and report.sh's `fail`/`stop` are the wrong
# vocabulary here: those exit 1.
refuse() {
  # BASH_SOURCE[-1] is the outermost script -- the one somebody typed -- rather
  # than [1], which inside a helper called by another helper in this same file
  # is this file. A refusal has to name the command that was run.
  printf '%s: %s\n' "${BASH_SOURCE[-1]##*/}" "$1" >&2
  shift
  [[ $# -gt 0 ]] && printf '%s\n' "$@" >&2
  exit 2
}

# require_current_checkout <path inside the checkout>
#
# Refuses unless the checkout has fetched and is level with its upstream.
#
# HERE RATHER THAN IN THE ONE SCRIPT THAT CALLS IT TODAY, because the hazard is
# the family's. 0031 records it as running in both directions: a checkout that
# has not fetched somebody else's `external_ref` will file a second ISSUE if it
# exports, and pull-new-issues.sh will make a second BEAD if it pulls, because
# unsynced.py offers an issue it cannot see a link for. Only the export half is
# guarded so far -- an ordinary token cannot delete a GitHub issue, while a
# duplicate bead can be deleted, so the two are not equally urgent -- but the
# guard has to sit where the other half can call it rather than copy it.
# inventory-tng-cwpa.10.
require_current_checkout() {
  local root=$1 fetched upstream behind

  if ! fetched=$(git -C "$root" fetch --quiet 2>&1); then
    refuse "could not fetch, so it cannot be said whether this checkout is current." \
      "Nothing was done." "$fetched"
  fi

  if ! upstream=$(git -C "$root" rev-parse --abbrev-ref '@{upstream}' 2>/dev/null); then
    refuse "this branch tracks nothing, so nothing here can say whether the" \
      "tracker is current. Nothing was done." \
      "  git branch --set-upstream-to=origin/<branch>"
  fi

  # COUNTED, OR REFUSED. Defaulting a rev-list that could not answer -- an
  # unborn HEAD, a remote-tracking ref that went away between the two commands
  # -- reads "it could not look" as "nothing to look at", which is the one
  # direction this must never fail in.
  if ! behind=$(git -C "$root" rev-list --count "HEAD..@{upstream}" 2>&1); then
    refuse "could not count what $upstream is ahead by, so it cannot be said" \
      "whether this checkout is current. Nothing was done." "$behind"
  fi

  if [[ "$behind" -ne 0 ]]; then
    refuse "$behind commit(s) behind $upstream, so .beads/issues.jsonl may not" \
      "know about issues somebody else has already filed. Acting from here would" \
      "duplicate what they did. Nothing was done." \
      "  git pull --ff-only"
  fi
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
