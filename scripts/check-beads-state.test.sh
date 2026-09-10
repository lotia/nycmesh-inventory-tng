#!/usr/bin/env bash
# What check-beads-state.sh must refuse, and what it must leave alone.
#
# The case this suite exists for is `a new state directory`: the checker's
# whole point is that it catches a directory nobody taught it about, because
# the directory the tracker keeps its database in changes with the backend
# mode and has already been documented wrongly once.
#
# Usage: check-beads-state.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/testlib.sh"

workspace

# A repository shaped like this one's .beads, built rather than copied: the
# real one changes underneath a suite that borrows it.
scene() {
  new_repo "$WORK/repo"
  mkdir -p "$WORK/repo/.beads/dolt" "$WORK/repo/.beads/hooks"
  printf 'dolt/\nembeddeddolt/\nproxieddb/\nbackup/\n' >"$WORK/repo/.beads/.gitignore"
  printf '{\n  "dolt_mode": "proxied-server"\n}\n' >"$WORK/repo/.beads/metadata.json"
  printf 'export.auto: true\nsync:\n    remote: "git+ssh://example.invalid/x.git"\n' >"$WORK/repo/.beads/config.yaml"
  : >"$WORK/repo/.beads/hooks/commit-msg"
  git -C "$WORK/repo" add -A >/dev/null 2>&1
  git -C "$WORK/repo" commit -qm "scene" >/dev/null 2>&1
}

check() { (cd "$WORK/repo" && "$HERE/check-beads-state.sh" 2>&1); }

echo "a workspace that is in order"

scene
out=$(check); status=$?
assert "$out" "$status" 0 "where git cannot publish it" "an ignored database and tracked hooks pass"

echo
echo "a state directory nobody taught it about"

# The failure this is for: a mode switch, or a new bd version, puts the
# database somewhere .beads/.gitignore has never heard of.
scene
mkdir -p "$WORK/repo/.beads/somethingnew"
: >"$WORK/repo/.beads/somethingnew/data"
out=$(check); status=$?
assert "$out" "$status" 1 "neither tracked nor ignored" "a new directory is refused"
assert "$out" "$status" 1 "somethingnew" "and is named, so it can be acted on"
assert "$out" "$status" 1 "One thing to fix" "and the count is reported rather than crashing on it"

echo
echo "the directory the mode says it needs"

scene
rm -rf "$WORK/repo/.beads/dolt"
out=$(check); status=$?
assert "$out" "$status" 1 "does not exist" "a mode whose storage is missing is refused"
assert "$out" "$status" 1 "proxied-server" "and the mode is named"

scene
printf '{\n  "dolt_mode": "embedded"\n}\n' >"$WORK/repo/.beads/metadata.json"
out=$(check); status=$?
assert "$out" "$status" 1 "embeddeddolt" "embedded mode wants a different directory, and says which"

scene
printf '{\n  "database": "dolt"\n}\n' >"$WORK/repo/.beads/metadata.json"
out=$(check); status=$?
assert "$out" "$status" 1 "names no dolt_mode" "metadata with no mode at all is refused"

scene
printf '{\n  "dolt_mode": "something-later"\n}\n' >"$WORK/repo/.beads/metadata.json"
out=$(check); status=$?
assert "$out" "$status" 0 "has not been taught" "a mode from a later bd is reported, not failed"

# THE CHECKOUT CI ACTUALLY GETS. metadata.json is gitignored and the database
# with it, so a clone has neither and has done nothing wrong. Without this case
# the checker failed every run on a runner while this suite went on passing.
scene
rm -f "$WORK/repo/.beads/metadata.json"
rm -rf "$WORK/repo/.beads/dolt"
out=$(check); status=$?
assert "$out" "$status" 0 "has not been initialised" "a clone that has never run bd init passes"

echo
echo "what a failure says"

# `verdict` takes the good sentence AND what the fixing is before; a caller
# that omits the second dies on `set -u` and never prints the count at all.

echo
echo "the setting that keeps the committed export honest"

# bd deleted this once already, and nothing noticed but a person who happened
# to be looking. check-batch.sh reads the file it keeps current.
# THE SHAPES bd ACCEPTS, all three of them. The four failure cases below could
# not have caught a check that was too narrow -- they only ever assert that a
# wrong config is refused -- and the first version of this check matched the
# flat spelling alone while bd's own template documents the nested one.
scene
printf 'sync:\n    remote: "x"\nexport:\n    auto: true\n' >"$WORK/repo/.beads/config.yaml"
out=$(check); status=$?
assert "$out" "$status" 0 "where git cannot publish it" "the nested form bd's template documents"

scene
printf 'sync:\n    remote: "x"\nexport: {auto: true}\n' >"$WORK/repo/.beads/config.yaml"
out=$(check); status=$?
assert "$out" "$status" 0 "where git cannot publish it" "and the flow mapping"

scene
printf 'sync:\n    remote: "x"\nother: 1\nexport:\n    auto: true\n    path: issues.jsonl\nmore: 2\n' >"$WORK/repo/.beads/config.yaml"
out=$(check); status=$?
assert "$out" "$status" 0 "where git cannot publish it" "and a block with neighbours on both sides"

scene
printf 'sync:\n    remote: "x"\n' >"$WORK/repo/.beads/config.yaml"
out=$(check); status=$?
assert "$out" "$status" 1 "does not set export.auto" "a config without it is refused"
assert "$out" "$status" 1 "check-batch" "and the refusal says what reads the file that goes stale"

scene
printf 'sync:\n    remote: "x"\nexport.auto: false\n' >"$WORK/repo/.beads/config.yaml"
out=$(check); status=$?
assert "$out" "$status" 1 "does not set export.auto" "and so is one that turns it off"

scene
printf 'sync:\n    remote: "x"\n#export.auto: true\n' >"$WORK/repo/.beads/config.yaml"
out=$(check); status=$?
assert "$out" "$status" 1 "does not set export.auto" "a commented-out setting is not the setting"

scene
printf 'export.auto: true\n' >"$WORK/repo/.beads/config.yaml"
out=$(check); status=$?
assert "$out" "$status" 1 "names no sync remote" "the other setting that run deleted is checked too"

scene
rm -f "$WORK/repo/.beads/config.yaml"
out=$(check); status=$?
assert "$out" "$status" 1 "config.yaml is missing" "and a workspace that has lost the file altogether"

echo
echo "not every repository has one"

new_repo "$WORK/bare"
out=$(cd "$WORK/bare" && "$HERE/check-beads-state.sh" 2>&1); status=$?
assert "$out" "$status" 0 "no .beads directory" "a checkout without beads is not a failure"

verdict
