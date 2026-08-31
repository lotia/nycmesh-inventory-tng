#!/usr/bin/env bash
# Issues somebody filed on GitHub, brought into the tracker.
#
# `bd` syncs what it already knows about and enumerates nothing, so an issue
# opened by somebody who has never run `bd` is invisible to it for ever.
# unsynced.py finds those; this hands them over one at a time and says what
# happened. `inventory-tng-cwpa.1`, and the argument for the whole arrangement
# is on `inventory-tng-cwpa`.
#
# Usage: pull-new-issues.sh [--dry-run] [<repository>]
#
# The repository defaults to GITHUB_REPOSITORY, then to whatever `gh` resolves
# for the checkout. Needs `gh` authenticated and `bd` on the path; both are
# refused loudly rather than worked around.

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/report.sh"

REPO_ROOT=$(git -C "$HERE" rev-parse --show-toplevel) || exit 1
EXPORT="$REPO_ROOT/.beads/issues.jsonl"

dry_run=false
repository="${GITHUB_REPOSITORY:-}"
for argument in "$@"; do
  case "$argument" in
    --dry-run) dry_run=true ;;
    -*) echo "pull-new-issues: unknown flag $argument" >&2; exit 2 ;;
    *) repository="$argument" ;;
  esac
done

for tool in gh bd python3; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "pull-new-issues: $tool is needed and is not on the path." >&2
    exit 2
  }
done

# BD IS CONFIGURED SEPARATELY FROM GH, and finding that out by running it is
# how this was discovered: `gh` reads the checkout to know which repository it
# is in, and `bd github pull` does not -- it wants github.owner and
# github.repo, or GITHUB_REPOSITORY, and refuses per issue without them.
# Resolved once here and exported, so a caller needs only an authenticated
# `gh` and a token.
#
# ONE ANSWER FOR BOTH, and that is the repair rather than the design. This
# resolved bd's repository only when GITHUB_REPOSITORY was unset, while
# `gh issue list` always honoured the argument -- so passing an argument while
# that variable named something else listed one repository's issues and pulled
# them from another, linking beads to whatever wore that number there.
if [[ -n "$repository" ]]; then
  resolved="$repository"
else
  resolved=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null)
fi
if [[ -z "$resolved" ]]; then
  echo "pull-new-issues: could not work out which repository to use." >&2
  exit 2
fi
repository="$resolved"
export GITHUB_REPOSITORY="$resolved"


# `gh issue list` and not the REST collection, because /issues returns pull
# requests too and they share the number space. Pulling a pull request as an
# issue would make a bead for a branch.
#
# `--state all`, so an issue somebody filed and closed before anybody synced
# still becomes a bead. It is a record either way, and a closed one that never
# arrived is the shape nobody goes looking for.
offered=$(gh issue list ${repository:+--repo "$repository"} --state all --limit 1000 \
  --json number,url --jq '.[] | "\(.number)\t\(.url)"' 2>&1)
if [[ $? -ne 0 ]]; then
  echo "pull-new-issues: could not list issues:" >&2
  echo "$offered" >&2
  exit 2
fi

# THE GUARD THAT MATTERS. An empty list is indistinguishable from a repository
# with no issues, and both are indistinguishable from `gh` printing nothing on
# a failure it exited 0 for. Saying so is cheap; a silent no-op that reports
# success is how this stops working without anybody noticing.
if [[ -z "$offered" ]]; then
  note "GitHub has no issues, so there is nothing here to bring in."
  [[ -n "${GITHUB_OUTPUT:-}" ]] && echo "pulled=0" >> "$GITHUB_OUTPUT"
  verdict "Nothing to pull." pulling
fi

# unsynced.py needs no repository: the URL GitHub returns is compared whole
# against the URL bd stored, so it is carried in the string being matched.
new=$(printf '%s\n' "$offered" | python3 "$HERE/unsynced.py" "$EXPORT") || exit 2

if [[ -z "$new" ]]; then
  note "Every issue on GitHub is already linked to a bead."
  [[ -n "${GITHUB_OUTPUT:-}" ]] && echo "pulled=0" >> "$GITHUB_OUTPUT"
  verdict "Nothing to pull." pulling
fi

count=$(printf '%s\n' "$new" | wc -l | tr -d ' ')
note "$count issue(s) on GitHub that no bead points at: $(printf '%s' "$new" | tr '\n' ' ')"

# SAID IN A FORM A CALLER CAN READ, because "did anything arrive" is not
# answerable from the tracker afterwards: importing the export in a fresh
# workspace loses fields -- `defer_until` at least, inventory-tng-oavo -- so
# the file differs whether or not an issue was pulled. A run that proposed on
# that difference proposed its own churn. inventory-tng-wjkd.
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  echo "pulled=$count" >> "$GITHUB_OUTPUT"
fi

if [[ "$dry_run" == true ]]; then
  verdict "Nothing pulled: this was a dry run." pulling
fi

# ONE AT A TIME, rather than one call with every number. `bd github pull`
# warns and carries on when a reference will not fetch, so a batch reports
# success having skipped some of it; asked separately, the one that failed is
# named and counted here.
while IFS= read -r number; do
  [[ -z "$number" ]] && continue
  if output=$(bd github pull "$number" 2>&1); then
    note "#$number -> $(printf '%s' "$output" | tail -1)"
  else
    fail "#$number could not be pulled: $(printf '%s' "$output" | tail -1)"
  fi
done <<<"$new"

verdict "Every new issue reached the tracker." pulling
