#!/usr/bin/env bash
# What has to be true before a script here can talk to GitHub, and what to ask
# it for.
#
# Three preconditions: the tools are on the path, the checkout is current, and
# everything agrees which repository is meant. Each refuses rather than
# returning, because a caller that got past one of these without it holding
# would be asking GitHub a question it has no business asking.
#
# AND ONE THING THAT IS NOT A PRECONDITION, which is why it is called out here
# rather than left to be found: how big a page to ask for, and how to tell a
# page that fits from one `gh` stopped part way through. That pair ANSWERS
# rather than refusing, and deliberately -- the three callers do not agree on
# what a page cut short costs them or on which exit status says so, and
# `listing_cut_short` says why the wording is theirs.
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
# Sourced AFTER report.sh, from the directory the caller is in:
#
#   . "$HERE/report.sh"
#   . "$HERE/repository.sh"
#   resolve_repository "$argument"     # "" when nobody named one
#   # $REPOSITORY and $GITHUB_REPOSITORY are now the same string

# THE ORDER IS CHECKED RATHER THAN ASKED FOR, because getting it wrong fails
# OPEN. `require_current_checkout` says no by calling report.sh's `refuse`, and
# an undefined `refuse` does not stop a function -- it prints "command not
# found" and the next line runs, so every one of that guard's four refusals
# becomes a no-op and a stale checkout is waved through. That is the one
# direction these guards must never fail in, so the dependency is a guard of
# its own.
#
# `count_lines` IS ASKED FOR THE SAME WAY, and it fails open more quietly still:
# `listing_cut_short` compares its output against a number, and an undefined one
# substitutes the empty string, which `[[ -ge ]]` reads as nought. The answer is
# then "the page was not full" about a page nothing counted.
for needed in refuse count_lines; do
  declare -F "$needed" >/dev/null || {
    echo "repository.sh: source report.sh before this file; its guards need $needed." >&2
    exit 2
  }
done
unset needed

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

#: How many issues `gh issue list` is asked for at once, and the number a full
#: page is recognised by. Three scripts fetch a listing and all three want both,
#: which is what brought it here: two of them named a constant of their own with
#: a comment insisting the number must not be written twice, and between them
#: there were two of it plus a bare literal in the third.
#:
#: ISSUE_LIMIT rather than LIMIT, because this file is sourced into whatever the
#: caller already has: a name this general would be one a script could not use
#: for anything of its own without silently changing what its listing asks for.
ISSUE_LIMIT=1000

#: What to tell somebody who has hit it. Beside the constant because it names
#: where the constant IS, which is the one part of those three refusals that is
#: not about the caller -- and the one that goes stale if this moves again. What
#: a page cut short costs, and whether it is a refusal or a counted failure,
#: stays with each caller.
# shellcheck disable=SC2034  # read by the three callers that source this file
ISSUE_LIMIT_ADVICE="Raise ISSUE_LIMIT in scripts/repository.sh and re-run."

# listing_cut_short <what GitHub returned>
#
# True when the page came back exactly full, and so may stop short of the end.
#
# ONE LINE PER ISSUE IS THE CONTRACT, and it is stated because nothing enforces
# it: this counts lines, and every caller's `--jq` happens to emit one per
# issue. A projection emitting two would halve the count and answer "the page
# was not full" -- fail-open, which is the one direction this file's guards must
# never fail in. A caller that cannot promise one line each should count its own
# records and compare against ISSUE_LIMIT itself.
#
# === A FULL PAGE IS NOT AN ANSWER ===
#
# `gh` stops at `--limit` and says nothing about having done so, so a listing
# exactly that long is indistinguishable from one that fits. What follows is
# never a visible failure: export-issues.sh leaves a closed bead's issue open
# because the issue fell off the end, drifted.py passes over a bead whose issue
# is not in the listing and reports the missing part as agreeing, and
# pull-new-issues.sh says every issue is already linked about the part it never
# saw. Each of those is a green run over an unasked question.
#
# ASKED WHERE THE LISTING IS FETCHED, which is the rule this encodes and the
# reason it is a predicate rather than a refusal. One listing now feeds several
# questions -- sync-issues.sh cuts two column pairs out of one answer -- so
# leaving the guard at whichever question happened to notice had the others
# answer from a page the run had already worked out it could not see the end of.
# The wording, and whether it is a refusal or a counted failure, belongs to each
# caller: they differ in what has already been done and what is worth advising.
# inventory-tng-cwpa.14.
listing_cut_short() {
  [[ "$(count_lines "$1")" -ge "$ISSUE_LIMIT" ]]
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
