#!/usr/bin/env bash
# Configuration values nobody explained.
#
# check-config.py is the reader and its docstring says what "documented" means
# here and why the obvious rule is the wrong one. This is the wrapper the
# guardrail job runs, in the shape the other checkers beside it use.
#
# Usage: check-config.sh [<repository root>]
#
# scripts/check-config.allow holds the values that are right to leave bare. Its
# own header says how an entry is written.

set -uo pipefail

REPO_ROOT=${1:-$(git rev-parse --show-toplevel)} || exit 1

command -v python3 >/dev/null 2>&1 || {
  echo "check-config: python3 is needed to read the configuration files." >&2
  exit 1
}

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/report.sh"

# `relay` rather than a bare call, for the reason its own comment gives: a
# reader that died prints nothing, and nothing is indistinguishable from
# finding nothing wrong.
relay python3 "$HERE/check-config.py" "$REPO_ROOT"

verdict "Every configuration value says what it is for." "somebody meets a value nobody explained"
