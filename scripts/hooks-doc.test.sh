#!/usr/bin/env bash
# What hooks-doc.sh renders, what it refuses to, and that --check tells them
# apart.
#
# The render is exercised against a hooks directory this suite builds, not the
# repository's own: the whole point of the script is that a hook added or
# changed makes the page wrong, so the cases here add and change hooks and ask
# whether the page went wrong. The one case against the real repository is the
# last, and it is the case CI runs.
#
# Usage: scripts/hooks-doc.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
SCRIPT="$HERE/hooks-doc.sh"
. "$HERE/testlib.sh"
workspace

# A repository with a hooks directory, a checker that answers --describe, and
# a page with the block to write into. hooks-path.sh names the directory, so
# the scene is built where it will look.
scene() {
  new_repo "$WORK/repo"
  mkdir -p "$WORK/repo/.beads/hooks" "$WORK/repo/scripts" "$WORK/repo/docs"
  cat >"$WORK/repo/scripts/check-thing.sh" <<'CHECKER'
#!/usr/bin/env bash
[[ "${1:-}" == --describe ]] && { echo "Refuses a thing that is wrong, over 7 of them."; exit 0; }
exit 0
CHECKER
  chmod +x "$WORK/repo/scripts/check-thing.sh"
  ln -sf ../../scripts/check-thing.sh "$WORK/repo/.beads/hooks/commit-msg"
  shim "$WORK/repo/.beads/hooks/pre-commit" pre-commit
  printf '# Hooks\n\nProse before.\n\n<!-- hooks-doc: begin -->\n<!-- hooks-doc: end -->\n\nProse after.\n' \
    >"$WORK/repo/docs/git-hooks.md"
}

# shim <path> <hook> -- a file shaped like one of beads' shims.
shim() {
  printf '#!/usr/bin/env sh\n# --- BEGIN BEADS INTEGRATION v9.9.9 ---\nbd hooks run %s "$@"\n# --- END BEADS INTEGRATION v9.9.9 ---\n' "$2" >"$1"
  chmod +x "$1"
}

run() { (cd "$WORK/repo" && "$SCRIPT" "$@" 2>&1); }
page() { cat "$WORK/repo/docs/git-hooks.md"; }

echo "the render"

scene
out=$(run); status=$?
assert "$out" "$status" 0 "Wrote the hooks table" "the table is written into the page"
assert "$(page)" 0 0 "Prose before." "the prose before the block is kept"
assert "$(page)" 0 0 "Prose after." "and the prose after it"
assert "$(page)" 0 0 '| `commit-msg` | [`scripts/check-thing.sh`](../scripts/check-thing.sh) | Refuses a thing that is wrong, over 7 of them. |' \
  "a linked checker is described in its own words"
assert "$(page)" 0 0 '| `pre-commit` | beads shim v9.9.9: `bd hooks run pre-commit` |' \
  "a beads shim is described as one, with its version"
# pre-commit fires before commit-msg, and the table says so by its order.
first=$(grep -n '^| `pre-commit`' "$WORK/repo/docs/git-hooks.md" | cut -d: -f1)
second=$(grep -n '^| `commit-msg`' "$WORK/repo/docs/git-hooks.md" | cut -d: -f1)
if [[ -n "$first" && -n "$second" && "$first" -lt "$second" ]]; then
  pass "rows are in the order git fires them"
else
  fail_case "rows are in the order git fires them" "$(page)"
fi

out=$(run); status=$?
assert "$out" "$status" 0 "Wrote the hooks table" "rendering again is a no-op"
equals "$(grep -c 'hooks-doc: begin' "$WORK/repo/docs/git-hooks.md")" 1 "and leaves one block, not two"

echo
echo "--check"

out=$(run --check); status=$?
assert "$out" "$status" 0 "describes the hooks that run" "a page matching the render passes"

# A hook changes what it refuses: the page is stale the moment it does.
sed -i 's/over 7 of them/over 8 of them/' "$WORK/repo/scripts/check-thing.sh"
out=$(run --check); status=$?
assert "$out" "$status" 1 "does not describe the hooks that run" "a checker that changed its account fails the check"
assert "$out" "$status" 1 "+| \`commit-msg\`" "and the diff shows the row that moved"
assert "$out" "$status" 1 "Run scripts/hooks-doc.sh" "and says what to run"
run >/dev/null
out=$(run --check); status=$?
assert "$out" "$status" 0 "describes the hooks that run" "after which the check passes again"

# A hook arrives -- a beads upgrade, a new checker -- and the page is silent
# about it until somebody runs the script.
shim "$WORK/repo/.beads/hooks/pre-push" pre-push
out=$(run --check); status=$?
assert "$out" "$status" 1 "+| \`pre-push\`" "a hook added to the directory fails the check"
rm -f "$WORK/repo/.beads/hooks/pre-push"

# A stray edit inside the block does not survive.
sed -i 's/Refuses a thing/Refuses a THING/' "$WORK/repo/docs/git-hooks.md"
out=$(run --check); status=$?
assert "$out" "$status" 1 "does not describe" "an edit typed inside the block fails the check"

echo
echo "what it refuses to render"

scene
printf '#!/bin/sh\nexit 0\n' >"$WORK/repo/.beads/hooks/post-commit"
chmod +x "$WORK/repo/.beads/hooks/post-commit"
out=$(run); status=$?
assert "$out" "$status" 1 "neither a link to a checker nor a beads shim" "a hook it cannot classify is refused by name"
assert "$out" "$status" 1 "post-commit" "naming the file"

scene
shim "$WORK/repo/.beads/hooks/post-rewrite" post-rewrite
out=$(run); status=$?
assert "$out" "$status" 1 "no description for" "a beads shim it has no words for is refused rather than guessed at"

scene
printf '#!/usr/bin/env bash\nexit 0\n' >"$WORK/repo/scripts/check-thing.sh"
out=$(run); status=$?
assert "$out" "$status" 1 "does not answer --describe" "a linked checker that cannot describe itself is refused"

scene
rm -f "$WORK/repo/docs/git-hooks.md"
out=$(run); status=$?
assert "$out" "$status" 1 "does not exist" "a missing page is refused rather than invented"

scene
printf '# Hooks\n\nNo block here.\n' >"$WORK/repo/docs/git-hooks.md"
out=$(run); status=$?
assert "$out" "$status" 1 "has no" "a page without the block is refused"

out=$(run --bogus); status=$?
assert "$out" "$status" 2 "usage" "an unknown flag is refused"

echo
echo "this repository"
# The case CI runs, run here too so the suite is red on the same day the
# Documentation job would be.
out=$(cd "$HERE/.." && "$SCRIPT" --check 2>&1); status=$?
assert "$out" "$status" 0 "describes the hooks that run" "docs/git-hooks.md matches the hooks this repository has"

verdict
