#!/usr/bin/env bash
# Issues somebody filed on GitHub, brought into the tracker.
#
# `bd` syncs what it already knows about and enumerates nothing, so an issue
# opened by somebody who has never run `bd` is invisible to it for ever.
# unsynced.py finds those; this hands them over one at a time and says what
# happened. `inventory-tng-cwpa.1`, and the argument for the whole arrangement
# is on `inventory-tng-cwpa`.
#
# Usage: pull-new-issues.sh [--dry-run | --check] [<repository>]
#
# --dry-run says what would be brought in and brings nothing.
# --check does the same and REFUSES when anything is waiting, which is what a
# scheduled job wants: red for as long as the answer is yes, green the moment
# the rows are committed. Both answer from the committed export alone.
#
# Writes `pulled=<count>` to GITHUB_OUTPUT on every path that exits 0, the
# paths where the count is zero included: a step reading a missing one gets an
# empty string, and a caller that branched on it would read that as neither
# number. Nothing reads it today -- issue-sync.yml gates on `--check`'s exit
# status instead -- so this is the shape a caller would need, not one in use.
#
# The repository defaults to GITHUB_REPOSITORY, then to whatever `gh` resolves
# for the checkout. Needs `gh` authenticated, and `bd` on the path unless this
# is a dry run; both are refused loudly rather than worked around.

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/report.sh"
. "$HERE/repository.sh"

REPO_ROOT=$(git -C "$HERE" rev-parse --show-toplevel) || exit 1
EXPORT="$REPO_ROOT/.beads/issues.jsonl"

dry_run=false
check=false
repository="${GITHUB_REPOSITORY:-}"
for argument in "$@"; do
  case "$argument" in
    --dry-run) dry_run=true ;;
    # `--check` is a dry run that refuses. Nothing is pulled either way, so it
    # needs no more than a dry run does.
    --check) dry_run=true; check=true ;;
    -*) echo "pull-new-issues: unknown flag $argument" >&2; exit 2 ;;
    *) repository="$argument" ;;
  esac
done

# `bd` ONLY WHEN SOMETHING WILL ACTUALLY BE PULLED. A dry run answers from the
# committed export through unsynced.py and never invokes it, so demanding it
# refused a question this script can answer on its own -- and refused it in the
# one place the answer is wanted WITHOUT a tracker to write to, which is CI.
# The workflow asks exactly that question and got exit 2 for it.
# inventory-tng-qnxb.
tools=(gh python3)
[[ "$dry_run" == true ]] || tools+=(bd)
need_tools "${tools[@]}"

# One answer for both tools, settled in repository.sh, which says why they
# cannot each be left to work it out.
resolve_repository "$repository"
repository="$REPOSITORY"


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

# THE REFUSAL LIVES HERE RATHER THAN IN THE WORKFLOW THAT WANTS IT, which is
# the whole of why `--check` exists. A job whose only behaviour is fifteen lines
# of inline shell is a job whose behaviour nothing tests, and the wording somebody
# acts on could then only be checked by dispatching it. scripts/settings.yml is
# the precedent: the workflow is one `run:` line and the script's exit status is
# the signal. inventory-tng-qnxb.
if [[ "$check" == true ]]; then
  fail "$count issue(s) on GitHub have no bead, and nothing here brings them in."
  note "Landing them is a person's job -- see DEVELOPERS.md#issue-tracking:"
  note "  scripts/pull-new-issues.sh"
  note "  scripts/untriaged.py .beads/issues.jsonl"
  note "This goes green by itself once those rows are committed, with nothing to close."
  stop "the tracker has heard of every issue"
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
