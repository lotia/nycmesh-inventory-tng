#!/usr/bin/env bash
# What look-again.sh asks gh for, and when it asks nothing.
#
# The interesting half is the second: the script runs among a head's checks,
# so every exit it takes is a verdict somebody reads, and most of them have to
# be a quiet 0. A head with no run, a run without the job, a head that moved
# while the run was being waited for -- none of those is the nudge failing,
# and a suite that only proved the re-run happens would leave the exits that
# matter to the first pull request they go wrong on.
#
# Usage: scripts/look-again.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
SCRIPT="$HERE/look-again.sh"
. "$HERE/testlib.sh"
workspace

JOB='Not marked do-not-merge'

# A gh that answers from the scene: one file per question, in the shape the
# real one prints for --json. Every call is logged, which is how a case says
# "and nothing was re-run" rather than only "and it exited 0".
#
# `head-next`, if the scene writes it, is what every reading of the head after
# the first answers: a push landing while the script ran, whether it was
# waiting on the run at the time or not.
BIN="$WORK/bin"
mkdir -p "$BIN"
cat >"$BIN/gh" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$SCENE/calls"
case "$1 $2" in
  "pr view")
    cat "$SCENE/head"
    if [[ -e "$SCENE/head-next" ]]; then
      cp -f "$SCENE/head-next" "$SCENE/head"
    fi
    ;;
  "run list")  cat "$SCENE/runs" ;;
  "run view")  cat "$SCENE/jobs" ;;
  "run watch") ;;
  "run rerun")
    if [[ -e "$SCENE/rerun-refused" ]]; then
      cat "$SCENE/rerun-refused" >&2
      exit 1
    fi
    ;;
  *) echo "the stub was asked something it does not answer: $*" >&2; exit 99 ;;
esac
STUB
chmod +x "$BIN/gh"
# The real bash, jq and coreutils, and nothing else: the script's own header says it
# reaches for gh and jq, and this is what holds it to that.
borrow "$BIN" bash jq cat cp
export SCENE="$WORK/scene"

# scene <head> <runs json> <jobs json> -- a fresh scene, calls log emptied.
scene() {
  rm -rf "$SCENE"
  mkdir -p "$SCENE"
  printf '%s\n' "$1" >"$SCENE/head"
  printf '%s\n' "$2" >"$SCENE/runs"
  printf '%s\n' "$3" >"$SCENE/jobs"
  : >"$SCENE/calls"
}

run() {
  PATH="$BIN" "$BIN/bash" "$SCRIPT" "$@" 2>&1
}

# asked <substring> <name> -- a gh call matching this was made; unasked -- none
# was. Both through testlib, which refuses an empty substring.
asked() { assert "$(cat "$SCENE/calls")" 0 0 "$1" "$2"; }
unasked() { refute "$(cat "$SCENE/calls")" 0 0 "$1" "$2"; }

DONE='[{"databaseId": 41, "status": "completed"}]'
GOING='[{"databaseId": 41, "status": "in_progress"}]'
JOBS='{"jobs": [{"name": "Backend", "databaseId": 900}, {"name": "Not marked do-not-merge", "databaseId": 901}]}'

echo "the ordinary case"

scene abc123 "$DONE" "$JOBS"
out=$(run 7 "$JOB"); status=$?
assert "$out" "$status" 0 "job 901 of run 41" "a finished run has its job asked again"
asked "run rerun --job 901" "by job id, which is what re-runs a job that passed"
asked "run list --workflow ci.yml --commit abc123" "on the run for this head, not the latest run anywhere"
unasked "run watch" "without waiting for a run that is already over"

echo
echo "nothing to ask again, and each says so at exit 0"
# These are what a run of the nudge mostly finds, and a non-zero exit from any
# of them is a red check on a pull request that did nothing wrong.

scene abc123 '[]' "$JOBS"
out=$(run 7 "$JOB"); status=$?
assert "$out" "$status" 0 "No CI run for abc123 yet" "a head CI has not run on"
unasked "run rerun" "re-runs nothing"

scene abc123 "$DONE" '{"jobs": [{"name": "Backend", "databaseId": 900}]}'
out=$(run 7 "$JOB"); status=$?
assert "$out" "$status" 0 "carries no job named 'Not marked do-not-merge'" "a run without the job"
unasked "run rerun" "re-runs nothing either"

echo
echo "a run still going is waited for"
# The job may have finished -- with the body as it was -- while the browser
# suite beside it has not, so leaving would let that verdict stand.

scene abc123 "$GOING" "$JOBS"
out=$(run 7 "$JOB"); status=$?
assert "$out" "$status" 0 "waiting for it to finish" "the wait is announced"
asked "run watch 41" "and is a watch on that run"
asked "run rerun --job 901" "followed by the re-run once it is over"

# A push during the wait is what cancelled the run; re-running a job of it
# would re-enter ci.yml's concurrency group under the new head's run.
scene abc123 "$GOING" "$JOBS"
printf 'def456\n' >"$SCENE/head-next"
out=$(run 7 "$JOB"); status=$?
assert "$out" "$status" 0 "The head moved to def456" "a head that moved during the wait is left to its own run"
unasked "run rerun" "and nothing on the old head is re-run"

# The same push, with no wait to hide in: the head moved in the seconds between
# being read and the re-run being asked for. The re-run would still re-enter
# ci.yml's concurrency group under the new head's run, so it is still not made.
scene abc123 "$DONE" "$JOBS"
printf 'def456\n' >"$SCENE/head-next"
out=$(run 7 "$JOB"); status=$?
assert "$out" "$status" 0 "The head moved to def456" "a head that moved without a wait is left to its own run too"
unasked "run rerun" "and nothing on the old head is re-run either"

echo
echo "the one exit that is red"

scene abc123 "$DONE" "$JOBS"
printf 'HTTP 403: Resource not accessible by integration\n' >"$SCENE/rerun-refused"
out=$(run 7 "$JOB"); status=$?
assert "$out" "$status" 1 "::error::could not ask CI again" "a refused re-run is the nudge being dead, and fails"
assert "$out" "$status" 1 "Resource not accessible" "saying what gh said"

echo
echo "what it is given"

out=$(run 7); status=$?
assert "$out" "$status" 1 "usage: look-again.sh" "a missing job name is refused"
out=$(run); status=$?
assert "$out" "$status" 1 "usage: look-again.sh" "and so is a missing pull request"

echo
echo "the job it is pointed at exists"
# The workflow does not spell the name: it asks the reader, which is the one
# place the name lives. do-not-merge.test.sh pins that name to ci.yml; this
# pins the workflow to the reader.
grep -q 'do_not_merge.py --check-name' "$HERE/../.github/workflows/look-again.yml"; status=$?
exits "$status" 0 "look-again.yml takes the job name from the reader rather than spelling it"

verdict
