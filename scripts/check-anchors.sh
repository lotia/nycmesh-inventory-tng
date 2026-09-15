#!/usr/bin/env bash
# A heading a comment points at has to exist.
#
# lychee holds every Markdown link to its target, fragment included, and reads
# nothing else. The references that live outside Markdown -- `docs/code-style.md#typing`
# in a docstring, `docs/commits.md "Checking it"` in a refusal message, either in a
# workflow comment or an editor setting -- are the same promise with nothing
# holding it, and they outnumber the ones in pages. check-anchors.py is the
# reader and says how a heading becomes an anchor.
#
# Usage: check-anchors.sh [<path>...]

set -uo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel) || exit 1
cd "$REPO_ROOT" || exit 1

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/report.sh"

paths=("$@")
if [[ ${#paths[@]} -eq 0 ]]; then
  # Everything that is not a page, less what check-docs.sh leaves out of its
  # own corpus and for its reasons: what will not decode, the two lock files,
  # and the tracker's directory, whose exports quote references as data. The
  # enumeration flags are argued there too.
  mapfile -t paths < <(
    git ls-files --cached --others --exclude-standard | grep -Evi \
      '\.md$|\.(png|jpe?g|gif|ico|svg|woff2?|ttf|eot|wasm|xlsx|pdf|zip)$|(^|/)(uv\.lock|package-lock\.json)$|^\.beads/'
  )
fi

relay python3 "$HERE/check-anchors.py" "${paths[@]}"

verdict "Every heading a comment points at exists." "somebody follows one of them"
