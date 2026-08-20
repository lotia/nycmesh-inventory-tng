#!/usr/bin/env bash
# What check-docs.sh must notice, and what it must leave alone.
#
# The second half is the important half. A duplication checker that cries about
# repeated commands, repeated table headers and repeated citations is one
# everybody learns to ignore, and an ignored gate is worse than no gate.
#
# Usage: scripts/check-docs.test.sh

set -uo pipefail

CHECK=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/check-docs.sh
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

passed=0
failed=0

# No commits are made here -- check-docs.sh reads `git ls-files`, so staging is
# enough -- and it finds the repository from the working directory, so it can be
# run where it lives.
scene() {
  rm -rf "$WORK/repo"
  mkdir -p "$WORK/repo/scripts"
  cd "$WORK/repo" || exit 1
  git init -q .
}

# expect <exit status> <substring> <what this case is called>
expect() {
  local want_status=$1 want_text=$2 name=$3 output status
  git -C "$WORK/repo" add -A >/dev/null 2>&1
  output=$(cd "$WORK/repo" && "$CHECK" 2>&1)
  status=$?
  if [[ "$status" -eq "$want_status" && "$output" == *"$want_text"* ]]; then
    printf '  ok   %s\n' "$name"
    passed=$((passed + 1))
  else
    printf '  FAIL %s\n' "$name"
    printf '       wanted exit %s and %q\n' "$want_status" "$want_text"
    printf '       got exit %s:\n%s\n' "$status" "$output"
    failed=$((failed + 1))
  fi
}

PASSAGE="The ledger is append-only, so a row that is wrong stands until somebody
works out what happened and writes a compensating one against it."

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
expect 1 "2 passages live" "both duplications between one pair of files are reported"

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
printf '# allowed on purpose\n%s\n' "$PASSAGE" > scripts/check-docs.allow
expect 0 "No prose repeated" "an allowed passage is allowed"

# The window that finds a repeated stretch can reach past either end of the
# line written in the allowlist, so containment is what must be matched.
scene
printf '# One\n\nBefore it. %s Also after it.\n' "$PASSAGE" > one.md
printf '# Two\n\nBefore it. %s Also after it.\n' "$PASSAGE" > two.md
printf '%s\n' "Before it. $PASSAGE Also after it." > scripts/check-docs.allow
expect 0 "No prose repeated" "an allowance covers the words either side of it"

echo
if [[ "$failed" -eq 0 ]]; then
  echo "$passed passed."
  exit 0
fi
echo "$failed failed, $passed passed."
exit 1
