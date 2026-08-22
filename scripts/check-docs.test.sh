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
# A script header restating a document is the failure this rule exists for, and
# the one that used to be invisible.
scene
printf '# One\n\n%s\n' "$PASSAGE" > one.md
mkdir -p scripts
printf '#!/usr/bin/env bash\n# %s\n' "$PASSAGE" > scripts/thing.sh
expect 1 "scripts/thing.sh" "a script header restating a document is found"

# A docstring is a comment too.
scene
printf '# One\n\n%s\n' "$PASSAGE" > one.md
mkdir -p scripts
printf '"""%s"""\n' "$PASSAGE" > scripts/thing.py
expect 1 "scripts/thing.py" "a docstring restating a document is found"

# Code is not prose, any more than a fenced block is.
scene
printf '# One\n\nledger append only row wrong stands somebody works out happened writes compensating\n' > one.md
mkdir -p scripts
printf '#!/usr/bin/env bash\nledger append only row wrong stands somebody works out happened writes compensating\n' > scripts/thing.sh
expect 0 "No prose repeated" "code that reads like the prose is not compared"

# A shebang and a shellcheck directive look like comments and are not.
scene
mkdir -p scripts
printf '#!/usr/bin/env bash\n# shellcheck disable=SC2064\n' > scripts/a.sh
printf '#!/usr/bin/env bash\n# shellcheck disable=SC2064\n' > scripts/b.sh
expect 0 "No prose repeated" "directives that only look like comments are skipped"
# --words, which no case could express until `expect` learned to carry a value.
# A shorter window finds what twelve would step over.
scene
printf '# One\n\nA sentence of its own that says a modest number of words here.\n' > one.md
printf '# Two\n\nA sentence of its own that says a modest number of words here.\n' > two.md
expect --words 8 -- 1 "say the same thing" "the window can be narrowed"

# A file added anywhere is read; only application source is left out. Listing
# the places to look meant a helper moving out of scripts/ stopped being
# checked, and nothing said so.
scene
printf '# One\n\n%s\n' "$PASSAGE" > one.md
mkdir -p .github/workflows
printf '# %s\n' "$PASSAGE" > .github/workflows/thing.yml
expect 1 ".github/workflows/thing.yml" "a workflow's comments are read"

scene
printf '# One\n\n%s\n' "$PASSAGE" > one.md
mkdir -p tools
printf '#!/usr/bin/env bash\n# %s\n' "$PASSAGE" > tools/moved.sh
expect 1 "tools/moved.sh" "a helper outside scripts/ is read too"

# Application source is read too. A docstring is where a decision record gets
# paraphrased, and the judgement about which of those are the code explaining
# itself is check-docs.allow's rather than the input set's.
scene
printf '# One\n\n%s\n' "$PASSAGE" > one.md
mkdir -p backend/src/inventory
printf '"""%s"""\n' "$PASSAGE" > backend/src/inventory/models.py
expect 1 "backend/src/inventory/models.py" "an application docstring is read"

scene
printf '# One\n\n%s\n' "$PASSAGE" > one.md
mkdir -p frontend/src
printf '// %s\n' "$PASSAGE" > frontend/src/thing.ts
expect 1 "frontend/src/thing.ts" "a TypeScript line comment is read"

scene
printf '# One\n\n%s\n' "$PASSAGE" > one.md
mkdir -p frontend/src
printf '/**\n * %s\n */\nexport const x = 1;\n' "$PASSAGE" > frontend/src/thing.tsx
expect 1 "frontend/src/thing.tsx" "a JSDoc block is read, asterisks and all"

# A Helm template spells a comment the same way, and used to be read as having
# none at all.
scene
printf '# One\n\n%s\n' "$PASSAGE" > one.md
mkdir -p infra
printf '{{- /*\n%s\n*/ -}}\nkind: Ingress\n' "$PASSAGE" > infra/thing.yaml
expect 1 "infra/thing.yaml" "a block comment in a template is read"

# The case that made reading application source expensive: two migrations
# install near-identical trigger bodies, and a triple-quoted string handed to
# RunSQL is code however much of it reads like a sentence.
scene
mkdir -p backend/src/inventory
printf 'from django.db import migrations\n\nSQL = migrations.RunSQL("""%s""")\n' "$PASSAGE" > backend/src/inventory/a.py
printf 'from django.db import migrations\n\nSQL = migrations.RunSQL("""%s""")\n' "$PASSAGE" > backend/src/inventory/b.py
expect 0 "No prose repeated" "a string a Python file uses is a value, not a docstring"

scene
mkdir -p backend/src/inventory
printf '"""%s"""\n' "$PASSAGE" > backend/src/inventory/a.py
printf 'def f() -> None:\n    """%s"""\n' "$PASSAGE" > backend/src/inventory/b.py
expect 1 "say the same thing" "a docstring is still read wherever it sits"

# A file that will not parse is still read, rather than silently contributing
# nothing to the comparison.
scene
printf '# One\n\n%s\n' "$PASSAGE" > one.md
mkdir -p backend/src/inventory
printf '"""%s"""\n\ndef (\n' "$PASSAGE" > backend/src/inventory/broken.py
expect 1 "backend/src/inventory/broken.py" "a Python file that does not parse is still read"

# A citation written as a link is already dropped as addressing, and in a
# comment the same citation is written bare. prose() says why dropping only
# the bracketed form would be the asymmetry.
scene
printf '# One\n\nSee [the record](docs/decisions/0016-invariants.md) and nothing else here at all.\n' > one.md
mkdir -p scripts
printf '#!/usr/bin/env bash\n# See docs/decisions/0016-invariants.md and nothing else here at all.\n' > scripts/thing.sh
expect 0 "No prose repeated" "a bare path in a comment is addressing, not prose"

# Long enough that the address alone is a run of its own, so only stripping it
# can keep the two apart.
LONG_URL="https://docs.example.invalid/en/6.0/howto/deployment/asgi/and/further/parts/here/"
scene
printf '# One\n\nThe generated entry point, documented at %s by its own authors.\n' "$LONG_URL" > one.md
mkdir -p scripts
printf '#!/usr/bin/env bash\n# Something else entirely, though %s covers that as well.\n' "$LONG_URL" > scripts/thing.sh
expect 0 "No prose repeated" "a bare URL is addressing too"

# Short on purpose: the two lines share nothing but the citation, so this
# says the two forms of it are read the same way and says nothing else.
scene
printf '# One\n\nSee [decision 0016](d.md) point 4 for the whole of it.\n' > one.md
mkdir -p scripts
printf '#!/usr/bin/env bash\n# See decision 0016 point 4 for the whole of it.\n' > scripts/thing.sh
expect 0 "No prose repeated" "a bare record citation is addressing on both sides"

# The other half of that, and the reason a citation is one word rather than
# none: deleting it shortened the prose around it, so an explanation just
# under the window passed by carrying a reference.
scene
printf '# One\n\nA volunteer is a pick-list entry and is never an account, per [decision 0012](d.md) point 5.\n' > one.md
mkdir -p scripts
printf '#!/usr/bin/env bash\n# A volunteer is a pick-list entry and is never an account, per decision 0012 point 5.\n' > scripts/thing.sh
expect 1 "say the same thing" "a citation buys no discount on the explanation beside it"


# One allowance covering more than two files, which is why it names a list
# rather than a pair: the same label on four modules cost six entries when an
# entry covered one pair.
scene
mkdir -p scripts
for f in one.md two.md; do
  printf '# X\n\nThe very same twelve words appear in every one of these three files here.\n' > "$f"
done
printf '#!/usr/bin/env bash\n# The very same twelve words appear in every one of these three files here.\n' > scripts/thing.sh
printf 'one.md\ntwo.md\nscripts/thing.sh\nThe very same twelve words appear in every one of these three files here.\n' > scripts/check-docs.allow
expect 0 "No prose repeated" "one allowance can name more than two files"

# And it is not a blanket: a fourth file saying it is still news.
printf '# Y\n\nThe very same twelve words appear in every one of these three files here.\n' > three.md
expect 1 "say the same thing" "a file the allowance does not name is still reported"

verdict
