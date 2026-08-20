#!/usr/bin/env bash
# Post what a pull request's batch holds, read off the commits.
#
# Why it is said rather than typed is DEVELOPERS.md#pull-requests. This
# rewrites its own comment rather than adding one per push.
#
# Usage: say-batch.sh          with RANGE, PR and GH_TOKEN in the environment

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")

# The marker is how the comment is found again. It is invisible in the rendered
# page, so it costs a reader nothing.
MARKER="<!-- batch-contents -->"

# body_from <listing>: the comment, from check-batch.sh --list rows. Separated
# from the posting so it can be tested without a network or a pull request.
body_from() {
  local sha issue subject out
  out="$MARKER
### What this batch holds

Read off the commits by \`check-batch.sh\`, not typed.

| | Issue | |
| --- | --- | --- |
"
  while IFS=$'\t' read -r sha issue subject; do
    [[ -z "$sha" ]] && continue
    # The summary already opens with the issue's short form, and repeating both
    # in one row reads as a stutter.
    out+="| \`$sha\` | \`$issue\` | ${subject#*: } |
"
  done <<<"$1"
  printf '%s' "$out"
}

# Sourced by its test, which wants body_from and nothing else.
[[ "${BASH_SOURCE[0]}" != "${0}" ]] && return 0

: "${RANGE:?RANGE is required}"
: "${PR:?PR is required}"

REPO=${GITHUB_REPOSITORY:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}

listing=$("$HERE/check-batch.sh" --list "$RANGE") || exit 1
[[ -n "$listing" ]] || exit 0

body=$(body_from "$listing")

existing=$(gh api "repos/$REPO/issues/$PR/comments" --paginate --slurp 2>/dev/null |
  jq -r "[.[][] | select(.body | startswith(\"$MARKER\"))] | first | .id // empty")

if [[ -n "$existing" ]]; then
  gh api -X PATCH "repos/$REPO/issues/comments/$existing" -f body="$body" >/dev/null
else
  gh api -X POST "repos/$REPO/issues/$PR/comments" -f body="$body" >/dev/null
fi
