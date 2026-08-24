#!/usr/bin/env bash
# What check-setup.sh must say about a checkout it is shown.
#
# A throwaway repository per case, because what it reads is a real index, a
# real symlink and a real git configuration -- and because run against this
# repository the answer would be whatever this machine happens to be set up
# like, which is the one thing a test must not depend on.
#
# The pair of cases that matter most are the two ways a runner differs from a
# clone somebody works in: it has the tracked hook and it has no core.hooksPath
# at all. If --shipped-only ever stopped separating those, CI would either fail
# on every push or stop checking anything.
#
# Usage: scripts/check-setup.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
CHECK="$HERE/check-setup.sh"
. "$HERE/testlib.sh"
workspace

# A clone that has been through bootstrap: the hook tracked as a link, the
# checker where the link leads, and git pointed at the directory holding both.
# Each case below takes one of those away.
scene() {
  new_repo "$WORK/repo"
  mkdir -p "$WORK/repo/scripts" "$WORK/repo/.beads/hooks"
  printf '#!/usr/bin/env bash\n' > "$WORK/repo/scripts/check-commit.sh"
  chmod +x "$WORK/repo/scripts/check-commit.sh"
  ln -s ../../scripts/check-commit.sh "$WORK/repo/.beads/hooks/commit-msg"
  git -C "$WORK/repo" add -A
  git -C "$WORK/repo" config --local core.hooksPath .beads/hooks
  cd "$WORK/repo" || exit 1
}

hook() { printf '%s' "$WORK/repo/.beads/hooks/commit-msg"; }

unwired() { git -C "$WORK/repo" config --local --unset core.hooksPath; }

check "$CHECK"

echo "check-setup.sh"

scene
expect 0 "read before it becomes one" "a clone that has been through bootstrap is wired"

scene
unwired
expect 1 "has not been told where" "a clone git was never pointed at is told so"

scene
unwired
expect 1 "mise run setup" "and told what to run about it"

# The CI case, and the reason the flag exists: a fresh checkout is wired in
# every way a checkout can be, and has no hooks path because nothing there
# commits.
scene
unwired
expect --shipped-only -- 0 "read before it becomes one" "a checkout nobody commits from is not held to a hooks path"

scene
mkdir -p "$WORK/repo/.githooks"
git -C "$WORK/repo" config --local core.hooksPath .githooks
expect 1 "core.hooksPath names .githooks" "a hooks path leading somewhere else is reported"

scene
mkdir -p "$WORK/repo/.githooks"
git -C "$WORK/repo" config --local core.hooksPath .githooks
expect 1 "in your local configuration" "and whose setting it is, because a global one overrides nothing"

# A global value is one git is holding, so reading only the local scope would
# call this clone unwired while its hooks were somebody else's.
scene
printf '[core]\n\thooksPath = %s/elsewhere\n' "$WORK" > "$WORK/gitconfig"
mkdir -p "$WORK/elsewhere"
GIT_CONFIG_GLOBAL="$WORK/gitconfig" git -C "$WORK/repo" config --local --unset core.hooksPath
GIT_CONFIG_GLOBAL="$WORK/gitconfig" expect 1 "in your global configuration" "a global hooks path is seen, and named as global"

# Two spellings of one absent directory. `-ef` is false when neither side can
# be stat'd, so an equality this obvious was reported as a clash between a path
# and itself, with a note explaining a conflict that did not exist.
scene
rm -rf "$WORK/repo/.beads/hooks"
expect 1 "no such directory" "a hooks path naming a directory that is not there says that"

scene
rm -rf "$WORK/repo/.beads/hooks"
refute "$($CHECK)" $? 1 "Only one of the two" "and does not call it a clash with itself"

# core.hooksPath is one setting for every worktree of a repository, so in a
# linked worktree it names the main checkout's directory -- and the hooks there
# are this repository's own and are the ones git runs.
scene
git -C "$WORK/repo" commit -q -m scene
git -C "$WORK/repo" worktree add -q -b side "$WORK/side" 2>/dev/null
cd "$WORK/side" || exit 1
expect 0 "read before it becomes one" "a linked worktree is wired by the hooks path it shares"

# Spelled differently, same directory. beads writes the absolute form, so this
# is what most working clones actually hold.
scene
git -C "$WORK/repo" config --local core.hooksPath "$WORK/repo/.beads/hooks"
expect 0 "read before it becomes one" "an absolute hooks path reaching the same directory is the same directory"

# The tracked half, which is what a clone is given before anybody configures
# anything -- so these are failures on a runner as much as on a laptop.
scene
git -C "$WORK/repo" rm -q --cached .beads/hooks/commit-msg
expect 1 "is not tracked" "a hook left untracked would not reach the next clone"

scene
git -C "$WORK/repo" rm -q --cached .beads/hooks/commit-msg
expect --shipped-only -- 1 "is not tracked" "and a runner says so too"

# A copy passes every test a link passes, right up until the checker changes.
scene
rm -f "$(hook)"
cp scripts/check-commit.sh "$(hook)"
git -C "$WORK/repo" add -A
expect 1 "stored as a file of its own" "a copy of the checker is not a link to it"

scene
rm -f "$(hook)"
ln -s ../../scripts/gone-away.sh "$(hook)"
expect 1 "reaches nothing, pointing as it does at ../../scripts/gone-away.sh" \
  "a hook leading nowhere is reported rather than trusted"

scene
rm -f "$(hook)"
expect 1 "reaches nothing." "so is one that is not there at all"

scene
rm -f "$(hook)"
ln -s ../../scripts/check-batch.sh "$(hook)"
printf '#!/usr/bin/env bash\n' > "$WORK/repo/scripts/check-batch.sh"
expect 1 "does not reach scripts/check-commit.sh" "so is one leading to the wrong checker"

scene
chmod -x scripts/check-commit.sh
expect 1 "cannot be executed" "a checker git could not run is reported"

# Run from somewhere that is not the top of the tree, by the path a person
# would type from there. The script cd's to the repository root before it does
# anything, so resolving its own name after that reached a `report.sh`
# belonging to whatever happened to be one directory up -- nobody's, usually,
# and it then reported nothing at all while exiting 127.
scene
mkdir -p "$WORK/repo/sub"
rel=$(realpath --relative-to="$WORK/repo/sub" "$CHECK")
cd "$WORK/repo/sub" || exit 1
output=$("$rel" 2>&1)
status=$?
cd "$WORK/repo" || exit 1
assert "$output" "$status" 0 "read before it becomes one" \
  "it can be run by a relative path from a subdirectory"

scene
expect --nonsense -- 2 "unknown option" "an option it does not know stops it rather than being ignored"

# The cost of letting one through is the CI step this script is wired into: a
# one-dash typo there would fail every push complaining about the hooks, which
# reads as a broken clone rather than as a typo in a workflow.
scene
expect -shipped-only -- 2 "unknown option" "so does one a dash short"

scene
expect --shipped-only junk -- 2 "unknown option" "and a word left over after the flag"

verdict
