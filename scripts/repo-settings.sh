#!/usr/bin/env bash
# The repository settings this project's workflow depends on, as code.
#
# What they are and why is DEVELOPERS.md "Pull requests"; the reasoning is ADR
# 0017. This applies them, and with --check reports what has drifted. It is
# idempotent.
#
# Usage: repo-settings.sh [--check] [<owner/repo>]
#
#   --check   report what differs and change nothing (exit 1 if anything does)
#
# Needs a token with Administration: write. See DEVELOPERS.md#pull-requests.

set -uo pipefail

CHECK=0
REPO=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)
      CHECK=1
      shift
      ;;
    *)
      REPO=$1
      shift
      ;;
  esac
done

[[ -n "$REPO" ]] || REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner) || {
  echo "Could not work out which repository this is." >&2
  exit 1
}

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
WORKFLOW="$HERE/../.github/workflows/ci.yml"

# Read out of .github/workflows/ci.yml rather than listed here, because the
# only interesting question is whether the required checks are the jobs in that
# file -- and a copy cannot answer it. A job added there is required from the
# next run of this script, and one renamed stops being required by its old name
# instead of silently gating nothing under it.
#
# The name GitHub reports a check under is the job's `name:`, with the matrix
# value appended for a matrix job.
mapfile -t CHECKS < <(python3 "$HERE/ci-check-names.py" "$WORKFLOW") || exit 1
if [[ ${#CHECKS[@]} -eq 0 ]]; then
  echo "No job names found in $WORKFLOW." >&2
  exit 1
fi

differs=0
report() {
  printf '  %s %s\n' "$1" "$2"
  if [[ "$1" == "✗" ]]; then differs=1; fi
}

# --- how a pull request may be merged --------------------------------------
#
# Rebase only. See docs/decisions/0017-review-through-pull-requests.md.

echo "Merge methods:"
current=$(gh api "repos/$REPO" --jq '[.allow_squash_merge, .allow_merge_commit, .allow_rebase_merge, .delete_branch_on_merge] | @tsv')
IFS=$'\t' read -r squash merge rebase delete <<<"$current"

[[ "$squash" == "false" ]] && report "·" "squash merge is off" || report "✗" "squash merge is ON"
[[ "$merge" == "false" ]] && report "·" "merge commits are off" || report "✗" "merge commits are ON"
[[ "$rebase" == "true" ]] && report "·" "rebase merge is on" || report "✗" "rebase merge is OFF"
[[ "$delete" == "true" ]] && report "·" "branches are deleted on merge" || report "✗" "branches are kept on merge"

if [[ "$CHECK" -eq 0 ]]; then
  gh api -X PATCH "repos/$REPO" \
    -F allow_squash_merge=false \
    -F allow_merge_commit=false \
    -F allow_rebase_merge=true \
    -F delete_branch_on_merge=true >/dev/null || exit 1
fi

# --- what main accepts -----------------------------------------------------
#
# No approvals are required because a solo maintainer cannot approve their own
# pull request and would deadlock. A pull request is still required, which is
# the part that matters; raise the count the day there is a second maintainer.

echo "Protection on main:"
# Its exit status, not the emptiness of its output: gh writes the 404 body to
# stdout, so an unprotected branch arrives here as a perfectly non-empty JSON
# object that every probe below then reads as null.
if ! protection=$(gh api "repos/$REPO/branches/main/protection" 2>/dev/null); then
  report "✗" "main is not protected"
else
  IFS=$'\t' read -r strict admins conversations linear forced reviews < <(
    printf '%s' "$protection" | jq -r '[
      .required_status_checks.strict,
      .enforce_admins.enabled,
      .required_conversation_resolution.enabled,
      .required_linear_history.enabled,
      .allow_force_pushes.enabled,
      (.required_pull_request_reviews != null)
    ] | @tsv'
  )

  probe() {
    local got=$1 want=$2 text=$3
    if [[ "$got" == "$want" ]]; then
      report "·" "$text"
    else
      report "✗" "$text -- is $got"
    fi
  }
  probe "$strict" true "a branch must be current"
  probe "$admins" true "administrators are bound too"
  probe "$conversations" true "conversations must be resolved"
  probe "$linear" true "history must stay linear"
  probe "$forced" false "force pushes are refused"
  probe "$reviews" true "a pull request is required"

  have=$(printf '%s' "$protection" | jq -r '.required_status_checks.contexts | sort | join("\n")')
  want=$(printf '%s\n' "${CHECKS[@]}" | sort)
  if [[ "$have" == "$want" ]]; then
    report "·" "${#CHECKS[@]} checks are required"
  else
    report "✗" "the required checks are not the jobs in ci.yml"
    diff <(echo "$have") <(echo "$want") | sed 's/^/      /'
  fi
fi

if [[ "$CHECK" -eq 0 ]]; then
  gh api -X PUT "repos/$REPO/branches/main/protection" --input - >/dev/null <<JSON || exit 1
{
  "required_status_checks": {
    "strict": true,
    "contexts": $(printf '%s\n' "${CHECKS[@]}" | jq -Rnc '[inputs]')
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 0,
    "dismiss_stale_reviews": true,
    "require_last_push_approval": false
  },
  "restrictions": null,
  "required_conversation_resolution": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_linear_history": true
}
JSON
  echo
  echo "Applied."
  exit 0
fi

echo
if [[ "$differs" -eq 0 ]]; then
  echo "Settings are as this repository expects."
  exit 0
fi
echo "Something differs. Rerun without --check to put it back."
exit 1
