#!/usr/bin/env bash
# What repo-settings.sh must say about settings it is shown.
#
# `gh` is stubbed, so nothing here talks to GitHub and nothing here can change
# a repository. Only --check is exercised: the applying half is two API calls
# with no logic in them, and a test that stubbed those would be testing the
# stub.
#
# Usage: scripts/repo-settings.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
CHECK="$HERE/repo-settings.sh"
. "$HERE/testlib.sh"
workspace
mkdir -p "$WORK/bin"

# The stub answers out of two files the cases below write, and reports the
# protection call as failing when there is no file for it -- which is what gh
# does for an unprotected branch, 404 body on stdout and a non-zero status.
cat > "$WORK/bin/gh" <<'STUB'
#!/usr/bin/env bash
case "$*" in
  *"branches/main/protection"*)
    if [[ -s "$STUBS/protection.json" ]]; then
      cat "$STUBS/protection.json"
    else
      echo '{"message":"Branch not protected","status":"404"}'
      exit 1
    fi
    ;;
  *"repo view"*) echo "someone/somewhere" ;;
  *".permissions.admin"*) cat "$STUBS/admin" ;;
  *"actions/permissions/workflow"*) cat "$STUBS/approve" ;;
  *)
    # repos/<x> with a --jq that selects the four merge flags as TSV
    cat "$STUBS/merge.tsv"
    ;;
esac
STUB
chmod +x "$WORK/bin/gh"
export PATH="$WORK/bin:$PATH"
export STUBS="$WORK"

merge() { printf '%s\n' "$1" > "$WORK/merge.tsv"; }
admin() { printf '%s\n' "$1" > "$WORK/admin"; }
# Whether actions may open a pull request. Empty stands for a token that could
# not read it, which must report as unchecked rather than as wrong.
approve() { printf '%s\n' "$1" > "$WORK/approve"; }
RIGHT=$'false\tfalse\ttrue\ttrue'
protection() { cat > "$WORK/protection.json"; }
unprotected() { : > "$WORK/protection.json"; }

# From the workflow, not typed out again: a fixture that agreed with a copy
# would pass while the copy and the workflow disagreed, which is the drift this
# is here to catch.
CONTEXTS=$(python3 "$HERE/ci-check-names.py" "$HERE/../.github/workflows/ci.yml" | jq -Rnc '[inputs]')

GOOD='{
  "required_status_checks": {"strict": true, "contexts": '"$CONTEXTS"'},
  "enforce_admins": {"enabled": true},
  "required_pull_request_reviews": {"required_approving_review_count": 0},
  "required_conversation_resolution": {"enabled": true},
  "required_linear_history": {"enabled": true},
  "allow_force_pushes": {"enabled": false}
}'

check "$CHECK" --check someone/somewhere

echo "repo-settings.sh"

admin true
merge "$RIGHT"
approve true
protection <<<"$GOOD"
expect 0 "Settings are as this repository expects" "settings that match are accepted"

# See repo-settings.sh on a field the API omits. This is what the scheduled
# job did on its first real run: reported all four as wrong.
admin false
merge "$(printf '\t\t\t')"
protection <<<"$GOOD"
expect 0 "cannot read the merge methods" "merge flags that could not be read are said, not called wrong"

# gh writes the 404 body to stdout, so this is only reachable by reading its
# exit status. Getting it wrong produced six "-- is null" lines and a jq error.
admin true
merge "$RIGHT"
unprotected
expect 1 "main is not protected" "an unprotected main is said plainly, once"

# See repo-settings.sh: the same 404 reaches a token that simply may not look.
admin false
merge "$RIGHT"
unprotected
expect 0 "cannot read branch protection" "a token that cannot look is told so, not alarmed"

merge $'true\tfalse\ttrue\ttrue'
protection <<<"$GOOD"
expect 1 "squash merge is ON" "squash merge being back is noticed"

merge $'false\ttrue\ttrue\ttrue'
protection <<<"$GOOD"
expect 1 "merge commits are ON" "merge commits being back are noticed"

# The setting the issue-sync workflow depends on. It was off, undeclared, and
# so the only thing that noticed was the workflow failing -- inventory-tng-pmw2.
merge "$RIGHT"
approve false
expect 1 "may NOT open pull requests" "actions being unable to open a pull request is noticed"

approve ""
expect 0 "cannot read the workflow permissions" "a token that cannot read them is told so, not alarmed"

approve true

merge "$RIGHT"
protection <<<"${GOOD/\"enforce_admins\": \{\"enabled\": true\}/\"enforce_admins\": \{\"enabled\": false\}}"
expect 1 "administrators are bound too -- is false" "an administrator bypass is noticed"

# A job added to CI and forgotten here is a check that gates nothing.
merge "$RIGHT"
protection <<<"${GOOD/\"One issue per commit\"/\"Something else\"}"
expect 1 "the required checks are not the jobs in ci.yml" "a required check that drifted is noticed"

# The claim in that message is only true because the names are read out of the
# workflow. A job added to CI is required from the next run, with nothing to
# remember.
names=$(python3 "$HERE/ci-check-names.py" "$HERE/../.github/workflows/ci.yml")
if [[ "$names" == *"One issue per commit"* && "$names" == *"Container images (backend)"* ]]; then
  pass "the checks are read from ci.yml, matrix jobs expanded"
else
  fail_case "the checks are read from ci.yml, matrix jobs expanded" "$names"
fi

verdict
