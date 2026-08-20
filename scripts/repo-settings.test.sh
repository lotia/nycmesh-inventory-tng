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

CHECK=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/repo-settings.sh
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/bin"

passed=0
failed=0

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
RIGHT=$'false\tfalse\ttrue\ttrue'
protection() { cat > "$WORK/protection.json"; }
unprotected() { : > "$WORK/protection.json"; }

# From the workflow, not typed out again: a fixture that agreed with a copy
# would pass while the copy and the workflow disagreed, which is the drift this
# is here to catch.
CONTEXTS=$(python3 "$(dirname "$CHECK")/ci-check-names.py" \
  "$(dirname "$CHECK")/../.github/workflows/ci.yml" | jq -Rnc '[inputs]')

GOOD='{
  "required_status_checks": {"strict": true, "contexts": '"$CONTEXTS"'},
  "enforce_admins": {"enabled": true},
  "required_pull_request_reviews": {"required_approving_review_count": 0},
  "required_conversation_resolution": {"enabled": true},
  "required_linear_history": {"enabled": true},
  "allow_force_pushes": {"enabled": false}
}'

# expect <exit status> <substring> <what this case is called>
expect() {
  local want_status=$1 want_text=$2 name=$3 output status
  output=$("$CHECK" --check someone/somewhere 2>&1)
  status=$?
  if [[ "$status" -eq "$want_status" && "$output" == *"$want_text"* ]]; then
    printf '  ok   %s\n' "$name"
    passed=$((passed + 1))
  else
    printf '  FAIL %s\n' "$name"
    printf '       wanted exit %s and %q\n' "$want_status" "$want_text"
    printf '       got exit %s:\n%s\n' "$status" "$output"
    failed=$((failed + 1))
  fi
}

echo "repo-settings.sh"

merge "$RIGHT"
protection <<<"$GOOD"
expect 0 "Settings are as this repository expects" "settings that match are accepted"

# gh writes the 404 body to stdout, so this is only reachable by reading its
# exit status. Getting it wrong produced six "-- is null" lines and a jq error.
merge "$RIGHT"
unprotected
expect 1 "main is not protected" "an unprotected main is said plainly, once"

merge $'true\tfalse\ttrue\ttrue'
protection <<<"$GOOD"
expect 1 "squash merge is ON" "squash merge being back is noticed"

merge $'false\ttrue\ttrue\ttrue'
protection <<<"$GOOD"
expect 1 "merge commits are ON" "merge commits being back are noticed"

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
scene_names=$(python3 "$(dirname "$CHECK")/ci-check-names.py" \
  "$(dirname "$CHECK")/../.github/workflows/ci.yml")
if [[ "$scene_names" == *"One issue per commit"* && "$scene_names" == *"Container images (backend)"* ]]; then
  printf '  ok   %s\n' "the checks are read from ci.yml, matrix jobs expanded"
  passed=$((passed + 1))
else
  printf '  FAIL %s\n' "the checks are read from ci.yml, matrix jobs expanded"
  printf '       got:\n%s\n' "$scene_names"
  failed=$((failed + 1))
fi

echo
if [[ "$failed" -eq 0 ]]; then
  echo "$passed passed."
  exit 0
fi
echo "$failed failed, $passed passed."
exit 1
