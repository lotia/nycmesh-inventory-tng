#!/usr/bin/env bash
# Ask one job of a pull request's CI run to run again.
#
# For a check whose answer depends on something other than the code, so that
# a change to that something is looked at without a push. Which check, and
# why it has to be re-run by NAME rather than with `gh run rerun --failed`, is
# the header of .github/workflows/look-again.yml, its one caller.
#
# Usage: scripts/look-again.sh <pull request number> <job name>
#
# gh reads the repository from GH_REPO and its credential from GH_TOKEN, the
# way it does in a workflow; nothing here names either.
#
# WHAT EXITS HOW, because a run of this shows up among the head's checks (the
# reasons are in review-cycle.yml's header) and so a non-zero exit is read as a
# red check. Nothing to do is 0 and says why. 1 is reserved for the re-run
# itself being refused -- the token without `actions: write`, the run past its
# re-run window -- which is this nudge being dead rather than idle, and the one
# thing worth going red over.
set -euo pipefail

usage="usage: look-again.sh <pull request number> <job name>"
pr=${1:?$usage}
job=${2:?$usage}

head_of() { gh pr view "$pr" --json headRefOid --jq .headRefOid; }

head=$(head_of)

# The most recent ci.yml run for this head, which is what ties it to the pull
# request rather than to whatever ran last.
read -r id status < <(gh run list --workflow ci.yml --commit "$head" --limit 1 \
                        --json databaseId,status \
                        | jq -r '.[0] | select(.) | "\(.databaseId) \(.status)"') || true
# `|| true` because `read` answers 1 for no line at all, and no line is the
# answer this next test is for.
if [[ -z "${id:-}" ]]; then
  echo "No CI run for $head yet, so there is nothing to ask again."
  exit 0
fi

# THE RUN HAS TO BE OVER before one job of it can be asked again, and there is
# a reason to wait rather than leave: the job may already have finished, having
# read the body as it stood, while the browser suite beside it is still going.
# Leaving would let that verdict stand, which is the gap this exists to close.
#
# The interval is generous because the wait is minutes long and every tick is
# a line in the log; the output is dropped for the same reason.
if [[ "$status" != "completed" ]]; then
  echo "Run $id is still $status; waiting for it to finish before asking again."
  gh run watch "$id" --interval 30 >/dev/null
fi

# THE HEAD IS READ AGAIN, right before asking, whether or not there was a
# wait. A push during the wait is what cancelled the run being watched; a push
# in the seconds since the head was first read is the same case with a shorter
# window. Either way a re-run of a job on the old head would re-enter ci.yml's
# concurrency group -- which has cancel-in-progress set, so it would cancel
# the new head's run under it, and nothing would start that run again. The new
# head's own run reads the body when it gets there.
now=$(head_of)
if [[ "$now" != "$head" ]]; then
  echo "The head moved to $now since it was read, and its own run reads the body."
  exit 0
fi

# jq with --arg rather than gh's own --jq, because that would mean writing the
# job name into a jq program by string interpolation, and a name is free text.
jobid=$(gh run view "$id" --json jobs \
          | jq -r --arg job "$job" '[.jobs[] | select(.name == $job)][0].databaseId')
if [[ -z "$jobid" || "$jobid" == "null" ]]; then
  echo "Run $id carries no job named '$job', so there is nothing to ask again."
  exit 0
fi

if ! out=$(gh run rerun --job "$jobid" 2>&1); then
  echo "::error::could not ask CI again about '$job' (job $jobid of run $id): $out"
  exit 1
fi
echo "Asked '$job' (job $jobid of run $id) to look again."
