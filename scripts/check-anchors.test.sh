#!/usr/bin/env bash
# What check-anchors.sh must notice, and what it must leave alone.
#
# The slug cases are the ones worth having: a checker that computes anchors by
# a rule of its own reports a heading GitHub answers to as missing, and the fix
# people reach for is renaming the heading to suit the checker.
#
# Usage: scripts/check-anchors.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
CHECK="$HERE/check-anchors.sh"
. "$HERE/testlib.sh"
workspace

# The checker finds the repository from the working directory, and reads
# untracked files, so writing one is enough for it to be read.
scene() {
  new_repo "$WORK/repo"
  mkdir -p "$WORK/repo/scripts"
  cd "$WORK/repo" || exit 1
}

run_check() {
  (cd "$WORK/repo" && "$CHECK" "$@")
}
check run_check

# This suite is in the corpus the checker reads, and every reference a case
# writes is to a page that exists only in its scene. Spelling the page through
# a variable keeps `.md#` out of this file, so the checker never reads them
# here; the scene it writes them into is where they are meant to be read.
G=GUIDE.md
O=OTHER.md
M=model.md
S=SKILL.md

echo "check-anchors.sh"

scene
printf '# Guide\n\n## Signing in\n\nText.\n' > GUIDE.md
printf '%s\n' "# See $G#signing-in for the account" > scripts/thing.sh
expect 0 "Every heading" "a comment naming a heading that exists passes"

scene
printf '# Guide\n\n## Signing in\n\nText.\n' > GUIDE.md
printf '%s\n' "# See $G#signing-on for the account" > scripts/thing.sh
expect 1 "no heading in GUIDE.md has the anchor #signing-on" "a comment naming a heading that does not exist is reported"
expect 1 "nearest: #signing-in" "the nearest heading is offered"

scene
printf '# Guide\n' > GUIDE.md
printf '%s\n' "# See $O#anything, twice: $O#else" > scripts/thing.sh
expect 1 "OTHER.md is not a page in this repository" "a page that does not exist is reported as that"
expect 1 "One thing to fix" "and reported once, not once per anchor"

scene
printf '# Guide\n\n## Signing in\n\nText.\n' > GUIDE.md
printf '%s\n' "# See $G#signing-in and $G#nowhere" > scripts/thing.sh
expect 1 "scripts/thing.sh:1: no heading" "the file and the line are named"

# GitHub's rule, one clause per case. Each heading here is one that exists in
# this repository's pages, or did.
scene
printf '# Guide\n\n### 1. One topic, one place\n' > GUIDE.md
printf '%s\n' "# $G#1-one-topic-one-place" > scripts/thing.sh
expect 0 "Every heading" "punctuation is dropped and a leading number stays"

scene
printf '# Guide\n\n### Amendment (2026-08-30) — the requirement is a default, not a rule\n' > GUIDE.md
printf '%s\n' "# $G#amendment-2026-08-30--the-requirement-is-a-default-not-a-rule" > scripts/thing.sh
expect 0 "Every heading" "a dash between two spaces leaves two hyphens"

scene
printf '# Guide\n\n## The `manage.py` commands\n' > GUIDE.md
printf '%s\n' "# $G#the-managepy-commands" > scripts/thing.sh
expect 0 "Every heading" "inline code loses its backticks and its dot, and keeps its text"

scene
printf '# Guide\n\n## What [the gate](x.md) refuses\n' > GUIDE.md
printf '%s\n' "# $G#what-the-gate-refuses" > scripts/thing.sh
expect 0 "Every heading" "a link in a heading keeps its label"

scene
printf '# Guide\n\n## Backend\n\n## Frontend\n\n## Backend\n' > GUIDE.md
printf '%s\n' "# $G#backend-1" > scripts/thing.sh
expect 0 "Every heading" "a repeated heading is numbered from -1"

scene
printf '# Guide\n\n## Backend\n' > GUIDE.md
printf '%s\n' "# $G#backend-1" > scripts/thing.sh
expect 1 "#backend-1" "a number a heading does not repeat enough to earn is missing"

scene
printf '# Guide\n\n```bash\n# every query the importer makes\n```\n' > GUIDE.md
printf '%s\n' "# $G#every-query-the-importer-makes" > scripts/thing.sh
expect 1 "#every-query-the-importer-makes" "a comment inside a fenced block is not a heading"

# The fence rule is review_cycle.py's `hidden`, so a fence shown inside a
# longer one does not end the outer one -- the shape a page takes the moment
# it quotes an example.
scene
printf '# Guide\n\n````\n```\n## not a heading\n```\n````\n\n## Real\n' > GUIDE.md
printf '%s\n' "# $G#not-a-heading and $G#real" > scripts/thing.sh
expect 1 "#not-a-heading" "nor one inside a fence nested in a longer one"

scene
printf '# Guide\n\n## Trailing hashes ##\n' > GUIDE.md
printf '%s\n' "# $G#trailing-hashes" > scripts/thing.sh
expect 0 "Every heading" "closing hashes on a heading are not part of it"

# The quoted form, which is how a shell comment cites a section.
scene
printf '# Guide\n\n## Signing in\n' > GUIDE.md
printf '%s\n' "# The rules are in $G \"Signing in\", which also says how to run this" > scripts/thing.sh
expect 0 "Every heading" "a quoted heading that exists passes"

scene
printf '# Guide\n\n## Signing in\n' > GUIDE.md
printf '%s\n' "# The rules are in $G 'Signing in'" > scripts/thing.sh
expect 0 "Every heading" "in single quotes too"

scene
printf '# Guide\n\n## Signing in\n' > GUIDE.md
printf '%s\n' "# The rules are in $G \"Signing on\"" > scripts/thing.sh
expect 1 'no heading in GUIDE.md is titled "Signing on"' "a quoted heading that does not exist is reported"
expect 1 'nearest: "Signing in"' "and the nearest heading offered"

scene
printf '# Guide\n\n## When a branch is ready to merge\n' > GUIDE.md
printf '%s\n' "# What it refuses is $G \"When a" "# branch is ready to merge\", and nothing else." > scripts/thing.sh
expect 0 "Every heading" "a quoted heading may break across two comment lines"

scene
printf '# Guide\n\n## When a branch is ready to merge\n' > GUIDE.md
printf '%s\n' "  # What it refuses is $G \"When a" "  # branch is ready to marge\"." > scripts/thing.sh
expect 1 "scripts/thing.sh:1: no heading" "and is reported on the line it starts"

scene
printf '# Guide\n\n## Signing in\n' > GUIDE.md
printf '%s\n' "deny \"See $G \\\"Signing on\\\" first.\"" > scripts/thing.sh
expect 1 'is titled "Signing on"' "a citation whose quotes are backslashed inside a shell string is read too"

scene
printf '# Guide\n\n## The `manage.py` commands\n' > GUIDE.md
printf '%s\n' "# See $G \"The manage.py commands\"" > scripts/thing.sh
expect 0 "Every heading" "inline code in a heading is quoted without its backticks"

scene
printf '# Guide\n\n## The `manage.py` commands\n' > GUIDE.md
printf '%s\n' "# See $G \"The \`manage.py\` commands\"" > scripts/thing.sh
expect 0 "Every heading" "or with them, as the page prints it"

# Where the page is.
scene
mkdir -p docs backend/src/app
printf '# Model\n\n## Migrating\n' > docs/model.md
printf '%s\n' "\"\"\"See [the model](../../../docs/$M#migrating).\"\"\"" > backend/src/app/thing.py
expect 0 "Every heading" "a reference starting with . is relative to the file that makes it"

scene
mkdir -p docs backend/src/app
printf '# Model\n\n## Migrating\n' > docs/model.md
printf '%s\n' "\"\"\"See [the model](../../docs/$M#migrating).\"\"\"" > backend/src/app/thing.py
expect 1 "../../docs/model.md is not a page" "one ../ too few is a page that is not there"

scene
mkdir -p docs backend/src/app
printf '# Model\n\n## Migrating\n' > docs/model.md
printf '%s\n' "# docs/$M#migrating" > backend/src/app/thing.py
expect 0 "Every heading" "anything else is relative to the repository root"

scene
mkdir -p .agents/skills/deploy
printf '# Deploy\n\n## Rolling back\n' > .agents/skills/deploy/SKILL.md
printf '%s\n' "# See .agents/skills/deploy/$S#rolling-back" > scripts/thing.sh
expect 0 "Every heading" "a leading dot that names a directory is not a step from the file"


# What is not read.
scene
printf '# Guide\n' > GUIDE.md
printf '# See GUIDE.md, and OTHER.md, for the account\n' > scripts/thing.sh
expect 0 "Every heading" "a reference with no fragment is not this rule"

scene
printf '# Guide\n\n## Signing in\n' > GUIDE.md
printf '%s\n' "# Deploy" "" "See [nowhere]($G#nowhere)." > OTHER.md
expect 0 "Every heading" "a page is not in the default corpus, because lychee reads it"

scene
printf '# Guide\n\n## Signing in\n' > GUIDE.md
mkdir -p .beads
printf '%s\n' "{\"description\":\"finds $G#anchor in files\"}" > .beads/issues.jsonl
expect 0 "Every heading" "the tracker quotes references as data and is not read"

scene
printf '# Guide\n\n## Signing in\n' > GUIDE.md
printf '\x89PNG\r\n\x1a\n %s' "$G#nowhere" > picture.bin
expect 0 "Every heading" "a file that will not decode is stepped over"

scene
printf '# Guide\n\n## Signing in\n' > GUIDE.md
printf '%s\n' "# $G#nowhere" > scripts/thing.sh
git add -A >/dev/null 2>&1
git commit -qm one >/dev/null 2>&1
printf '%s\n' "# $G#elsewhere" > scripts/other.sh
expect 1 "scripts/other.sh:1" "a file written and never added is read"

scene
printf '# Guide\n\n## Signing in\n' > GUIDE.md
printf 'build/\n' > .gitignore
mkdir -p build
printf '%s\n' "# $G#nowhere" > build/thing.sh
expect 0 "Every heading" "a file .gitignore covers is not read"

verdict
