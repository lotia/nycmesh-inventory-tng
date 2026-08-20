#!/usr/bin/env bash
# What check-docs.sh must notice, and what it must leave alone.
#
# The second half is the important half. A duplication checker that cries about
# repeated commands, repeated table headers and repeated citations is one
# everybody learns to ignore, and an ignored gate is worse than no gate.
#
# Usage: scripts/check-docs.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
CHECK="$HERE/check-docs.sh"
. "$HERE/testlib.sh"
workspace

# No commits are made here -- check-docs.sh reads `git ls-files`, so staging is
# enough -- and it finds the repository from the working directory, so it can be
# run where it lives.
scene() {
  new_repo "$WORK/repo"
  mkdir -p "$WORK/repo/scripts"
  cd "$WORK/repo" || exit 1
}

PASSAGE="The ledger is append-only, so a row that is wrong stands until somebody
works out what happened and writes a compensating one against it."

# What is written has to be staged first: check-docs.sh reads `git ls-files`,
# which is also why no case here ever commits.
staged_check() {
  git -C "$WORK/repo" add -A >/dev/null 2>&1
  (cd "$WORK/repo" && "$CHECK" "$@")
}
check staged_check

echo "check-docs.sh"

scene
printf '# One\n\n%s\n' "$PASSAGE" > one.md
printf '# Two\n\n%s\n' "$PASSAGE" > two.md
expect 1 "say the same thing" "the same explanation in two files is found"

scene
printf '# One\n\n%s\n' "$PASSAGE" > one.md
printf '# Two\n\nSomething else entirely, at length, about a different subject.\n' > two.md
expect 0 "No prose repeated" "two files saying different things are left alone"

# A passage repeated inside one document is a writing problem. This rule is
# about a topic having one home, and a file is its own home.
scene
printf '# One\n\n%s\n\nAnd again:\n\n%s\n' "$PASSAGE" "$PASSAGE" > one.md
expect 0 "No prose repeated" "a passage repeated within one file is not this rule"

scene
printf '# One\n\n```bash\nuv run pytest --cov --cov-report=term-missing --maxfail=1\n```\n' > one.md
printf '# Two\n\n```bash\nuv run pytest --cov --cov-report=term-missing --maxfail=1\n```\n' > two.md
expect 0 "No prose repeated" "the same command in two files is not duplicated prose"

scene
printf '# One\n\n| Topic | Where |\n| --- | --- |\n| Code style and linting rules | Somewhere |\n' > one.md
printf '# Two\n\n| Topic | Where |\n| --- | --- |\n| Code style and linting rules | Somewhere |\n' > two.md
expect 0 "No prose repeated" "the same table in two files is not duplicated prose"

# Two documents citing the same work repeat its title by definition, and both
# linking to the same section of this guide is the rule working.
scene
printf '# One\n\nSee [OAuth 2.0 for Browser-Based Applications](https://example.invalid/a) — IETF.\n' > one.md
printf '# Two\n\nSee [OAuth 2.0 for Browser-Based Applications](https://example.invalid/a) — IETF.\n' > two.md
expect 0 "No prose repeated" "the same citation in two files is addressing, not prose"

# Two files can disagree with the rule twice over, and burying the second under
# the first is how one of them survives a review.
SECOND="A label is revoked rather than deleted, because the sticker on the
shelf outlives the row and somebody will scan it again next winter."

scene
printf '# One\n\n%s\n\nUnrelated filler in between.\n\n%s\n' "$PASSAGE" "$SECOND" > one.md
printf '# Two\n\n%s\n\nDifferent filler entirely.\n\n%s\n' "$PASSAGE" "$SECOND" > two.md
expect 1 "2 things to fix" "both duplications between one pair of files are reported"

# The excerpt is the repeated stretch, not the twelve-word window that found it.
scene
printf '# One\n\n%s\n' "$PASSAGE" > one.md
printf '# Two\n\n%s\n' "$PASSAGE" > two.md
expect 1 "compensating one against it" "the whole passage is quoted, not one window of it"

scene
printf '# One\n\n%s\n' "$PASSAGE" > one.md
ln -s one.md copy.md
expect 0 "No prose repeated" "a symlink is not a second copy of its target"

scene
printf '# One\n\n%s\n' "$PASSAGE" > one.md
printf '# Two\n\n%s\n' "$PASSAGE" > two.md
printf 'one.md\ntwo.md\n%s\n' "$PASSAGE" > scripts/check-docs.allow
expect 0 "No prose repeated" "an allowed passage is allowed"

# The window that finds a repeated stretch can reach past either end of the
# line written in the allowlist, so containment is what must be matched.
scene
printf '# One\n\nBefore it. %s Also after it.\n' "$PASSAGE" > one.md
printf '# Two\n\nBefore it. %s Also after it.\n' "$PASSAGE" > two.md
printf 'one.md\ntwo.md\n%s\n' "Before it. $PASSAGE Also after it." > scripts/check-docs.allow
expect 0 "No prose repeated" "an allowance covers the words either side of it"

# See scripts/check-docs.allow on what an allowance covers.
scene
printf '# One\n\n%s\n' "$PASSAGE" > one.md
printf '# Two\n\n%s\n' "$PASSAGE" > two.md
printf '# Three\n\n%s\n' "$PASSAGE" > three.md
printf 'one.md\ntwo.md\n%s\n' "$PASSAGE" > scripts/check-docs.allow
expect 1 "three.md" "a third copy is refused though the first two are allowed"

# And an allowance nothing matches is a baseline that has outlived its debt.
scene
printf '# One\n\nSomething entirely its own, at length, about a subject.\n' > one.md
printf '# Two\n\nA different thing altogether, said differently, at length.\n' > two.md
printf 'one.md\ntwo.md\n%s\n' "$PASSAGE" > scripts/check-docs.allow
expect 0 "no longer repeat each other" "an allowance that matches nothing is reported"

# The case that made the old report wrong -- see check-docs.sh on why a third
# copy is what breaks reading staleness off the collisions.
scene
printf '# a\n\n%s\n' "$PASSAGE" > a.md
printf '# b\n\n%s\n' "$PASSAGE" > b.md
printf '# c\n\n%s\n' "$PASSAGE" > c.md
printf 'b.md\nc.md\n%s\n' "$PASSAGE" > scripts/check-docs.allow
output=$(staged_check)
status=$?
if [[ "$output" != *"no longer repeat"* ]]; then
  pass "an allowance is not called stale while its files still repeat"
else
  fail_case "an allowance is not called stale while its files still repeat" "$output"
fi
assert "$output" "$status" 1 "a.md" "and the third copy is still reported"

# An unrelated duplication between the same two files used to hide a genuinely
# spent allowance.
scene
printf '# One\n\n%s\n\n%s\n' "$SECOND" "Filler that is entirely its own and says nothing twice." > one.md
printf '# Two\n\n%s\n\n%s\n' "$SECOND" "Different filler, also its own, also saying nothing twice." > two.md
printf 'one.md\ntwo.md\n%s\n' "$PASSAGE" > scripts/check-docs.allow
expect 1 "no longer repeat each other" "a spent allowance is reported even when the pair collides otherwise"

# An entry in the old one-paragraph form parses as two paths and no passage.
scene
printf '# One\n\n%s\n' "$PASSAGE" > one.md
printf '# Two\n\n%s\n' "$PASSAGE" > two.md
printf '%s\n' "$PASSAGE" > scripts/check-docs.allow
expect 1 "not in the documented form" "an allowance in the old form is said, not swallowed"

scene
printf '# One\n\n%s\n' "$PASSAGE" > one.md
printf 'one.md\nnowhere.md\n%s\n' "$PASSAGE" > scripts/check-docs.allow
expect 0 "which is not read here" "an allowance naming a file nothing reads is said"
# --words, which no case could express until `expect` learned to carry a value.
# A shorter window finds what twelve would step over.
scene
printf '# One\n\nA sentence of its own that says a modest number of words here.\n' > one.md
printf '# Two\n\nA sentence of its own that says a modest number of words here.\n' > two.md
expect --words 8 -- 1 "say the same thing" "the window can be narrowed"

verdict
