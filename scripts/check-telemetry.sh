#!/usr/bin/env bash
# Code that changes something and says nothing about it.
#
# The rule is DEVELOPERS.md#definition-of-done: work after the telemetry sweep
# is expected to be logged and instrumented, and a line in a checklist will not
# hold that any more than it held the documentation rule. check-telemetry.py is
# the reader and says what it reads and what it deliberately does not.
#
# Usage: check-telemetry.sh [<path>...]
#
# scripts/check-telemetry.allow holds the modules that are right to leave
# quiet. Its own header says how an entry is written.

set -uo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel) || exit 1
cd "$REPO_ROOT" || exit 1

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/report.sh"

paths=("$@")
if [[ ${#paths[@]} -eq 0 ]]; then
  # The application's own source, and not its tests: a test that changes
  # something changes it in a database nobody is watching, which is the whole
  # of what this rule is about.
  mapfile -t paths < <(git ls-files 'backend/src/inventory/*.py' 'backend/src/inventory/**/*.py' | grep -v '/tests/')
fi

relay \
  env ALLOW="$REPO_ROOT/scripts/check-telemetry.allow" python3 "$HERE/check-telemetry.py" "${paths[@]}"

verdict "Everything that changes something says so." "each one says what it did"
