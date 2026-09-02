#!/usr/bin/env bash
# Issues somebody filed on GitHub, brought into the tracker.
#
# `bd` syncs what it already knows about and enumerates nothing, so an issue
# opened by somebody who has never run `bd` is invisible to it for ever.
# unsynced.py finds those; this hands them over one at a time and says what
# happened. `inventory-tng-cwpa.1`, and the argument for the whole arrangement
# is on `inventory-tng-cwpa`.
#
# Usage: pull-new-issues.sh [--dry-run | --check] [--listing <file>] [<repository>]
#
# --listing takes the `number<TAB>url` records this would otherwise fetch, so a
# caller that has already asked GitHub does not make it ask again. It changes
# where the bytes come from and nothing else: the comparison and the wording of
# what is waiting stay here, which is what made delegating to this script worth
# doing. inventory-tng-cwpa.13. What it does add is a way to be handed another
# repository's issues, and unsynced.py refuses those -- see the flag's own
# branch below.
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
listing=""
repository="${GITHUB_REPOSITORY:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --listing)
      [[ $# -ge 2 ]] || refuse "--listing needs a file"
      listing=$2
      shift
      ;;
    --dry-run) dry_run=true ;;
    # `--check` is a dry run that refuses. Nothing is pulled either way, so it
    # needs no more than a dry run does.
    --check) dry_run=true; check=true ;;
    -*) refuse "unknown flag $1" ;;
    *) repository="$1" ;;
  esac
  shift
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

# === AND THE CHECKOUT HAS TO BE CURRENT BEFORE ANYTHING IS PULLED ===
#
# The other half of inventory-tng-cwpa.10: a checkout that has not fetched
# somebody else's `external_ref` cannot see that the issue is already linked, so
# unsynced.py offers it and this makes a SECOND bead for work that has one. The
# guard is repository.sh's, and 0031 has the reasoning, including why the export
# side was done first.
#
# WORSE HERE THAN THERE IN ONE RESPECT, which is why nothing else caught it: the
# export at least refuses when GitHub holds an issue no bead points at, and on a
# stale checkout that precondition passes precisely because the ref it is
# missing is the one that would have failed it.
#
# Only a run that writes is held to it, for the reason 0031 gives.
[[ "$dry_run" == true ]] || require_current_checkout "$REPO_ROOT"


# `gh issue list` and not the REST collection, because /issues returns pull
# requests too and they share the number space. Pulling a pull request as an
# issue would make a bead for a branch.
#
# `--state all`, so an issue somebody filed and closed before anybody synced
# still becomes a bead. It is a record either way, and a closed one that never
# arrived is the shape nobody goes looking for.
#
# STDERR KEPT OUT OF THE VALUE. `gh` writes to stderr on calls that SUCCEED --
# a new-release notice, an authentication advisory -- and folding those in made
# them lines of what unsynced.py reads as `number<TAB>url` records. It refuses a
# line it cannot parse, so a perfectly good listing stopped the run, on a
# scheduled job, where a red check sends somebody looking for a sync problem
# that does not exist. export-issues.sh has carried the same arrangement since
# it was reviewed for it. inventory-tng-p8q4.1.
gaps=$(mktemp) || exit 2
trap 'rm -f "$gaps"' EXIT

if [[ -n "$listing" ]]; then
  # A FILE, AND READ BY THE SHELL ITSELF. `-r` alone is true of a DIRECTORY, and
  # the read that followed was not checked: `cat` failed, `offered` came back
  # empty, and the guard below reported "GitHub has no issues" and exited 0 --
  # a listing nothing ever read, answering the question green, which is the one
  # failure that guard exists to stop. `-f` settles it before the read.
  #
  # `$(<file)` rather than `cat`, because the read is then bash's and the script
  # reaches for no program it did not already need. That list is an assertion
  # pull-new-issues.test.sh makes with `borrow`, and `cat` was not in it: under
  # that PATH the read failed and the run went green for the same reason.
  #
  # WHOSE ISSUES THEY ARE IS NOT ASKED HERE, and that is deliberate rather than
  # missing. This flag is the one way records reach the reader without `--repo`
  # having settled that question first, so somebody has to ask it -- but what a
  # GitHub URL looks like is unsynced.py's to know, and a second opinion about
  # it written in shell is how the two come to disagree. `refuse_foreign` there
  # holds the whole rule, and says what reading somebody else's would cost.
  # inventory-tng-cwpa.12.
  [[ -f "$listing" && -r "$listing" ]] || refuse "cannot read the listing it was handed: $listing"
  offered=$(<"$listing")
elif ! offered=$(gh issue list ${repository:+--repo "$repository"} --state all --limit 1000 \
  --json number,url --jq '.[] | "\(.number)\t\(.url)"' 2>"$gaps"); then
  refuse "could not list issues:" "$(tail -2 "$gaps")"
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

# THE MATCHING NEEDS NO REPOSITORY -- the URL GitHub returns is compared whole
# against the URL bd stored, so it is carried in the string being matched. The
# repository is handed over for the other thing unsynced.py does with it: it
# refuses a record that is not an issue of this repository, which is what a
# listing handed in through --listing has nothing else to say.
#
# AND ITS REFUSAL IS FRAMED HERE, because unsynced.py prints a bare sentence and
# a bare sentence names no command. Every other refusal in this file goes through
# `refuse`, which prefixes what somebody typed -- report.sh says why that is not
# decoration -- and the guard this replaced did too, until the rule moved into
# python. One wording covers all of them: a missing export, a line it could not
# parse and a record from somewhere else are each "it could not be read".
new=$(printf '%s\n' "$offered" | python3 "$HERE/unsynced.py" "$EXPORT" "$repository") ||
  refuse "the listing could not be read against the tracker (above). Nothing was pulled."

if [[ -z "$new" ]]; then
  note "Every issue on GitHub is already linked to a bead."
  [[ -n "${GITHUB_OUTPUT:-}" ]] && echo "pulled=0" >> "$GITHUB_OUTPUT"
  verdict "Nothing to pull." pulling
fi

count=$(count_lines "$new")
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
