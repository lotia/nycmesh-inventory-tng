#!/usr/bin/env bash
# The tables of hooks in docs/git-hooks.md, written from the hooks themselves.
#
# The page is what a developer reads when a commit is refused, and the one
# promise it has to keep is that it describes the hooks that actually run --
# not the ones somebody remembered when they wrote it. So the tables are not
# maintained; they are rendered from .beads/hooks, from .claude/settings.json,
# and from each script's own account of itself, and CI refuses a page that
# differs from that render. A hook added, removed or changed makes the
# Documentation job red until this is run again. inventory-tng-f6n4.
#
# Usage: scripts/hooks-doc.sh           # rewrite the blocks in place
#        scripts/hooks-doc.sh --check   # exit 1 if the page would change
#
# WHAT IS RENDERED, AND WHAT IS NOT. Everything between a block's two markers
# is; the prose around them is a person's and is left alone. So the page keeps
# a voice and the tables keep the facts, and the two cannot drift apart
# because only one of them is typed.
#
# EVERY HOOK IS CLASSIFIED OR REFUSED. A file in the hooks directory that is
# neither a link to one of this repository's checkers nor a beads shim is not
# quietly listed as "unknown", and a script the settings file registers that
# cannot say what it refuses is not guessed at: the render fails and names it,
# because a hook this cannot describe is a hook the page would be silent about,
# and silence is the failure the page exists to prevent. The same shape as
# scripts/check-beads-state.sh, one directory over.

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
REPO_ROOT=$(git rev-parse --show-toplevel) || exit 1
cd "$REPO_ROOT" || exit 1

. "$HERE/report.sh"
. "$HERE/hooks-path.sh"

PAGE=docs/git-hooks.md
SETTINGS=.claude/settings.json
# The blocks, by name; a block's markers are `<!-- <name>: begin -->` and the
# same with `end`, and `render_<name with _>` is what fills it.
BLOCKS=(hooks-doc claude-hooks)

CHECK=0
case "${1:-}" in
  "") ;;
  --check) CHECK=1 ;;
  *) refuse "usage: hooks-doc.sh [--check]" ;;
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

# object <what is wrong> -- a failure said on stderr.
#
# The renderers run inside a command substitution that captures the block they
# print, so an objection said on stdout there would be captured with it and
# thrown away when the render is refused. stderr is what reaches the person.
object() { fail "$1" >&2; }

# describe_of <script> -- what a script of this repository's says it refuses,
# or a failure and 1.
#
# A script describes itself in its own words, with its own numbers; the page is
# not allowed to paraphrase them. Non-empty, not merely exit 0: a script that
# takes the flag and says nothing would be documented as doing nothing.
describe_of() {
  local script=$1 said
  [[ -x "$script" ]] || { object "$script is registered or linked, and is not an executable here."; return 1; }
  said=$("./$script" --describe 2>/dev/null) && [[ -n "$said" ]] || {
    object "$script does not answer --describe, so what it refuses cannot be documented."
    return 1
  }
  printf '%s\n' "$said"
}

# stamp <what it was rendered from> -- the line that says a block is not typed.
stamp() {
  echo "<!-- Rendered by scripts/hooks-doc.sh from $1. Do not edit between the markers; run the script. -->"
}

# row <hook> -- one table row, or a failure and 1.
row() {
  local hook=$1 path="$HOOKS/$1" target purpose
  if [[ -L "$path" ]]; then
    # Resolved and then made relative to the root, rather than stripping a
    # literal `../../`: the depth of the hooks directory is hooks-path.sh's
    # fact, and bootstrap-dev.sh says why nothing else may spell it.
    target=$(readlink -f "$path")
    target=${target#"$REPO_ROOT/"}
    purpose=$(describe_of "$target") || return 1
    printf '| `%s` | [`%s`](../%s) | %s |\n' "$hook" "$target" "$target" "$purpose"
  elif [[ $(<"$path") =~ BEGIN\ BEADS\ INTEGRATION\ v([0-9.]+) ]]; then
    local version=${BASH_REMATCH[1]}
    purpose=$(beads_purpose "$hook") || {
      object "$path is a beads shim this script has no description for. Add one to beads_purpose."
      return 1
    }
    printf '| `%s` | beads shim v%s: `bd hooks run %s` | Never refuses on its own: %s, and passes the exit status of `bd` through. |\n' \
      "$hook" "$version" "$hook" "$purpose"
  else
    object "$path is neither a link to a checker nor a beads shim, so it cannot be documented."
    return 1
  fi
}

# Every hook in the directory, each once: ORDER first, then whatever is left.
hooks() {
  local name
  { printf '%s\n' "${ORDER[@]}"; ls -1 "$HOOKS"; } | awk '!seen[$0]++' |
    while IFS= read -r name; do
      [[ -f "$HOOKS/$name" || -L "$HOOKS/$name" ]] && echo "$name"
    done
}

render_hooks_doc() {
  stamp "$HOOKS"
  echo "| Hook | What runs | What it does, and what it refuses |"
  echo "| --- | --- | --- |"
  local name ok=0
  while IFS= read -r name; do
    row "$name" || ok=1
  done < <(hooks)
  return "$ok"
}

# The Claude Code hooks: which events, which command, what timeout -- from the
# settings file, since that is what registers them -- and then what each script
# behind them refuses, in its own words.
render_claude_hooks() {
  stamp "$SETTINGS and the scripts it names"
  local registrations
  # "-" for an absent field: a tab is whitespace to `read`, and two in a row
  # would collapse into one and shift every field after.
  registrations=$(python3 -c '
import json, sys
hooks = json.load(open(sys.argv[1])).get("hooks", {})
for event, matchers in hooks.items():
    for matcher in matchers:
        for hook in matcher.get("hooks", []):
            print(event, hook.get("timeout") or "-", matcher.get("matcher") or "-",
                  hook.get("command", ""), sep="\t")
' "$SETTINGS") || { object "could not read $SETTINGS."; return 1; }
  echo "| Event | Only for | Command | Timeout |"
  echo "| --- | --- | --- | --- |"
  local event timeout matcher command script scripts=""
  while IFS=$'\t' read -r event timeout matcher command; do
    [[ -n "$event" ]] || continue
    [[ "$matcher" == "-" ]] && matcher="every one"
    [[ "$timeout" == "-" ]] && timeout="none" || timeout="${timeout}s"
    printf '| `%s` | %s | `%s` | %s |\n' "$event" "$matcher" "$command" "$timeout"
    # The script, with the harness variable and the quoting stripped off:
    # `"$CLAUDE_PROJECT_DIR"/scripts/x.sh check` names scripts/x.sh.
    script=${command#\"}
    script=${script#\$CLAUDE_PROJECT_DIR}
    script=${script#\"}
    script=${script#/}
    scripts+="${script%% *}"$'\n'
  done <<<"$registrations"
  local ok=0 said
  while IFS= read -r script; do
    [[ -n "$script" ]] || continue
    said=$(describe_of "$script") || { ok=1; continue; }
    echo
    echo "What [\`$script\`](../$script) refuses, in its own words:"
    echo
    echo "| On | What it refuses, or asks |"
    echo "| --- | --- |"
    printf '%s\n' "$said" | awk -F'\t' '{ printf "| %s | %s |\n", $2, $3 }'
  done < <(printf '%s' "$scripts" | awk '!seen[$0]++')
  return "$ok"
}

[[ -f "$PAGE" ]] || refuse "$PAGE does not exist; the prose around the tables is a person's to write first."
page=$(<"$PAGE")
for name in "${BLOCKS[@]}"; do
  for marker in "<!-- $name: begin -->" "<!-- $name: end -->"; do
    [[ $'\n'"$page"$'\n' == *$'\n'"$marker"$'\n'* ]] ||
      refuse "$PAGE has no '$marker' line to write against."
  done
done

# Each block rendered, then spliced in: everything outside the markers as it
# was. The bodies go in through the environment rather than -v, because awk
# processes backslash escapes in a -v value, so a description holding `\n`
# would be quoted with a newline in it -- and the check would still pass,
# since it renders the same way.
wanted=$page
for name in "${BLOCKS[@]}"; do
  body=$("render_${name//-/_}") || exit 1
  wanted=$(BODY="$body" awk -v begin="<!-- $name: begin -->" -v end="<!-- $name: end -->" '
    $0 == begin { print; print ENVIRON["BODY"]; skipping = 1; next }
    $0 == end   { skipping = 0 }
    !skipping   { print }
  ' <<<"$wanted")
done

if [[ "$CHECK" -eq 1 ]]; then
  if [[ "$wanted" != "$page" ]]; then
    fail "$PAGE does not describe the hooks that run. Run scripts/hooks-doc.sh and commit the result:"
    diff -u <(printf '%s\n' "$page") <(printf '%s\n' "$wanted") | tail -n +3 | sed 's/^/      /'
  fi
  verdict "$PAGE describes the hooks that run." "the page is right"
fi

printf '%s\n' "$wanted" >"$PAGE"
echo "Wrote the hooks tables into $PAGE."
