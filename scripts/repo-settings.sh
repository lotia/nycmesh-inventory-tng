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

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
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

. "$HERE/report.sh"

# compare <got> <want> <what to say when it matches> [<what to say when it does not>]
compare() {
  if [[ "$1" == "$2" ]]; then note "$3"; else fail "${4:-$3 -- is $1}"; fi
}

# --- how a pull request may be merged --------------------------------------
#
# Rebase only. See docs/decisions/0017-review-through-pull-requests.md.

echo "Merge methods:"
current=$(gh api "repos/$REPO" --jq '[.allow_squash_merge, .allow_merge_commit, .allow_rebase_merge, .delete_branch_on_merge] | @tsv')
IFS=$'\t' read -r squash merge rebase delete <<<"$current"

# The API omits these for a token without push access, and a missing field
# arrives here as an empty string -- which compares unequal to "false" and
# reports all four as wrong. "Nobody could look" is not "everything is broken";
# the scheduled job runs with exactly such a token unless a secret is set.
readable=1
[[ -n "$squash" && -n "$merge" && -n "$rebase" && -n "$delete" ]] || readable=0

if [[ "$readable" -eq 0 ]]; then
  note "this token cannot read the merge methods, so they were not checked"
  note "  a token with push access can"
else

  compare "$squash" false "squash merge is off" "squash merge is ON"
  compare "$merge" false "merge commits are off" "merge commits are ON"
  compare "$rebase" true "rebase merge is on" "rebase merge is OFF"
  compare "$delete" true "branches are deleted on merge" "branches are kept on merge"
fi

if [[ "$CHECK" -eq 0 ]]; then
  gh api -X PATCH "repos/$REPO" \
    -F allow_squash_merge=false \
    -F allow_merge_commit=false \
    -F allow_rebase_merge=true \
    -F delete_branch_on_merge=true >/dev/null || exit 1
fi

# --- what a workflow may do ------------------------------------------------
#
# The issue-sync workflow opens a pull request, and GitHub refuses that unless
# the repository allows it. It is off by default, so this is a setting the
# automation depends on and nothing declared -- meaning the workflow failed at
# its last step every run and only the workflow knew. inventory-tng-pmw2.
#
# default_workflow_permissions is deliberately not declared. It reads "read",
# and issue-sync.yml asks for contents: write in its own permissions block,
# which demonstrably works -- every refused run still pushed its branch.
# Declaring the repository default would assert something nothing depends on.

echo
echo "What a workflow may do:"
approve=$(gh api "repos/$REPO/actions/permissions/workflow" --jq '.can_approve_pull_request_reviews' 2>/dev/null)

if [[ -z "$approve" ]]; then
  note "this token cannot read the workflow permissions, so they were not checked"
  note "  a token with Administration access can"
else
  compare "$approve" true "actions may open pull requests" \
    "actions may NOT open pull requests, so the issue-sync workflow cannot propose one"
fi

if [[ "$CHECK" -eq 0 ]]; then
  gh api -X PUT "repos/$REPO/actions/permissions/workflow" \
    -F can_approve_pull_request_reviews=true >/dev/null || exit 1
fi

# --- what main accepts -----------------------------------------------------
#
# No approvals are required because a solo maintainer cannot approve their own
# pull request and would deadlock. A pull request is still required, which is
# the part that matters; raise the count the day there is a second maintainer.

echo "Protection on main:"
# Its exit status, not the emptiness of its output: gh writes the 404 body to
# stdout, so an unprotected branch arrives here as a perfectly non-empty JSON
# object that every comparison below then reads as null.
if ! protection=$(gh api "repos/$REPO/branches/main/protection" 2>/dev/null); then
  # "Nobody could look" is not "nothing is there". Only a token with
  # Administration can read protection, and reporting an unprotected main to
  # somebody holding a read-only one is a false alarm they cannot act on.
  if [[ "$(gh api "repos/$REPO" --jq '.permissions.admin // false' 2>/dev/null)" == "true" ]]; then
    fail "main is not protected"
  else
    note "this token cannot read branch protection, so it was not checked"
    note "  a token with Administration: read can"
  fi
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

  compare "$strict" true "a branch must be current"
  compare "$admins" true "administrators are bound too"
  compare "$conversations" true "conversations must be resolved"
  compare "$linear" true "history must stay linear"
  compare "$forced" false "force pushes are refused"
  compare "$reviews" true "a pull request is required"

  have=$(printf '%s' "$protection" | jq -r '.required_status_checks.contexts | sort | join("\n")')
  want=$(printf '%s\n' "${CHECKS[@]}" | sort)
  if [[ "$have" == "$want" ]]; then
    note "${#CHECKS[@]} checks are required"
  else
    fail "the required checks are not the jobs in ci.yml"
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

# The remedy for this one is rerunning the tool, not editing the tree, and
# verdict has no slot for a next step.
[[ "$problems" -gt 0 ]] && echo && echo "Rerun without --check to put it back."
verdict "Settings are as this repository expects." "these match again"
