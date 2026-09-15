#!/usr/bin/env bash
# The table of git hooks in docs/git-hooks.md, written from the hooks
# themselves.
#
# The page is what a developer reads when a commit is refused, and the one
# promise it has to keep is that it describes the hooks that actually run --
# not the ones somebody remembered when they wrote it. So the table is not
# maintained; it is rendered from .beads/hooks and each checker's own account
# of itself, and CI refuses a page that differs from that render. A hook added,
# removed or changed makes the Documentation job red until this is run again.
# inventory-tng-f6n4.
#
# Usage: scripts/hooks-doc.sh           # rewrite the table in place
#        scripts/hooks-doc.sh --check   # exit 1 if the page would change
#
# WHAT IS RENDERED, AND WHAT IS NOT. Everything between the two markers below
# is; the prose around them is a person's and is left alone. So the page keeps
# a voice and the table keeps the facts, and the two cannot drift apart because
# only one of them is typed.
#
# EVERY HOOK IS CLASSIFIED OR REFUSED. A file in the hooks directory that is
# neither a link to one of this repository's checkers nor a beads shim is not
# quietly listed as "unknown": the render fails and names it, because a hook
# this cannot describe is a hook the page would be silent about, and silence
# is the failure the page exists to prevent. The same shape as
# scripts/check-beads-state.sh, one directory over.

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
REPO_ROOT=$(git rev-parse --show-toplevel) || exit 1
cd "$REPO_ROOT" || exit 1

. "$HERE/hooks-path.sh"

PAGE=docs/git-hooks.md
BEGIN='<!-- hooks-doc: begin -->'
END='<!-- hooks-doc: end -->'

CHECK=0
case "${1:-}" in
  "") ;;
  --check) CHECK=1 ;;
  *)
    echo "usage: hooks-doc.sh [--check]" >&2
    exit 2
    ;;
esac

# The order git fires them around one commit and one push, so the table reads
# as a timeline rather than alphabetically. A hook not in this list is still
# rendered, after these.
ORDER=(pre-commit prepare-commit-msg commit-msg post-commit pre-push post-checkout post-merge)

# What each beads shim does, in beads' own words (`bd hooks --help`). Kept here
# rather than asked of bd, because the render has to be the same on a runner
# that has no bd as on a laptop that does. A shim beads has not yet taught this
# file about is refused below, which is the day to add its row.
beads_purpose() {
  case "$1" in
    pre-commit)         echo "hands off to beads, which runs any hooks it was told to chain before a commit" ;;
    prepare-commit-msg) echo "hands off to beads, which adds an agent-identity trailer to the message for forensics" ;;
    pre-push)           echo "hands off to beads, which runs any hooks it was told to chain before a push" ;;
    post-checkout)      echo "hands off to beads, which runs any hooks it was told to chain after a checkout" ;;
    post-merge)         echo "hands off to beads, which runs any hooks it was told to chain after a pull or merge" ;;
    *)                  return 1 ;;
  esac
}

# row <hook> -- one table row, or a refusal on stderr and 1.
row() {
  local hook=$1 path="$HOOKS/$1" target version purpose
  if [[ -L "$path" ]]; then
    target=$(readlink "$path")
    target=${target#../../}
    [[ -x "$target" ]] || {
      echo "hooks-doc.sh: $path links to $target, which is not an executable here." >&2
      return 1
    }
    # A checker describes what it refuses in its own words, with its own
    # numbers. The page is not allowed to paraphrase them.
    # Non-empty, not merely exit 0: a checker that takes the flag and says
    # nothing would be documented as doing nothing.
    purpose=$("./$target" --describe 2>/dev/null) && [[ -n "$purpose" ]] || {
      echo "hooks-doc.sh: $target does not answer --describe, so it cannot be documented." >&2
      return 1
    }
    printf '| `%s` | [`%s`](../%s) | %s |\n' "$hook" "$target" "$target" "$purpose"
  elif version=$(grep -o 'BEGIN BEADS INTEGRATION v[0-9.]*' "$path" 2>/dev/null); then
    version=${version##* }
    purpose=$(beads_purpose "$hook") || {
      echo "hooks-doc.sh: $path is a beads shim this script has no description for. Add one to beads_purpose." >&2
      return 1
    }
    printf '| `%s` | beads shim %s: `bd hooks run %s` | Never refuses on its own: %s, and passes the exit status of `bd` through. |\n' \
      "$hook" "$version" "$hook" "$purpose"
  else
    echo "hooks-doc.sh: $path is neither a link to a checker nor a beads shim, so it cannot be documented." >&2
    return 1
  fi
}

# Every hook in the directory, in ORDER first and then whatever is left.
hooks() {
  local name listed=()
  for name in "${ORDER[@]}"; do
    [[ -e "$HOOKS/$name" ]] && { echo "$name"; listed+=("$name"); }
  done
  for path in "$HOOKS"/*; do
    name=$(basename "$path")
    [[ -f "$path" || -L "$path" ]] || continue
    case " ${listed[*]-} " in *" $name "*) continue ;; esac
    echo "$name"
  done
}

render() {
  echo "$BEGIN"
  echo "<!-- Rendered by scripts/hooks-doc.sh from $HOOKS. Do not edit between the markers; run the script. -->"
  echo "| Hook | What runs | What it does, and what it refuses |"
  echo "| --- | --- | --- |"
  local name ok=0
  while IFS= read -r name; do
    row "$name" || ok=1
  done < <(hooks)
  echo "$END"
  return "$ok"
}

[[ -f "$PAGE" ]] || {
  echo "hooks-doc.sh: $PAGE does not exist; the prose around the table is a person's to write first." >&2
  exit 1
}
if ! grep -qxF "$BEGIN" "$PAGE" || ! grep -qxF "$END" "$PAGE"; then
  echo "hooks-doc.sh: $PAGE has no '$BEGIN' ... '$END' block to write into." >&2
  exit 1
fi

table=$(render) || exit 1

# The page with the block replaced: everything before BEGIN, the render,
# everything after END.
wanted=$(awk -v begin="$BEGIN" -v end="$END" -v table="$table" '
  $0 == begin { print table; skipping = 1; next }
  $0 == end   { skipping = 0; next }
  !skipping   { print }
' "$PAGE")

if [[ "$CHECK" -eq 1 ]]; then
  if diff -u "$PAGE" <(printf '%s\n' "$wanted") >/dev/null; then
    echo "$PAGE describes the hooks that run."
    exit 0
  fi
  echo "$PAGE does not describe the hooks that run. Run scripts/hooks-doc.sh and commit the result:"
  diff -u "$PAGE" <(printf '%s\n' "$wanted") | tail -n +3
  exit 1
fi

printf '%s\n' "$wanted" >"$PAGE"
echo "Wrote the hooks table into $PAGE."
