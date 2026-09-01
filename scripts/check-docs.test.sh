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

# No commits are made here -- writing a file is enough to be read -- and the
# checker finds the repository from the working directory, so it can be run
# where it lives.
scene() {
  new_repo "$WORK/repo"
  mkdir -p "$WORK/repo/scripts"
  cd "$WORK/repo" || exit 1
}

PASSAGE="The ledger is append-only, so a row that is wrong stands until somebody
works out what happened and writes a compensating one against it."

# Most cases stage anyway, so that the two enumerations the corpus is built
# from are both exercised rather than only the untracked one. The block at the
# foot of this file is the half that stages nothing.
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

# --- what counts as a file worth reading ----------------------------------
#
# inventory-tng-1tt. The corpus was once a list of seven extensions, and the
# files that carry prose and no extension of their own -- the chart's helper
# template, the Dockerfiles, nginx.conf.template, .env.sample -- were the ones
# nothing read. It is now every tracked file less the ones that are not prose,
# and these pin that so a return to naming extensions is a red suite rather
# than a quiet gap.
for pair in \
  "infra/helm/inventory-tng/templates/_helpers.tpl:the chart's helper template" \
  "backend/Dockerfile:an extensionless Dockerfile" \
  "frontend/nginx.conf.template:a .template" \
  ".env.sample:the file every variable is explained in"
do
  path=${pair%%:*}
  what=${pair#*:}
  scene
  mkdir -p "$(dirname "$path")"
  printf '# One\n\n%s\n' "$PASSAGE" > one.md
  printf '# %s\n' "$PASSAGE" > "$path"
  expect 1 "say the same thing" "$what is read"
done

# The other side of it: a file that is not prose stays out, whatever it holds.
scene
printf '# One\n\n%s\n' "$PASSAGE" > one.md
mkdir -p frontend
printf '# %s\n' "$PASSAGE" > frontend/package-lock.json
expect 0 "No prose repeated" "and a lock file nobody writes by hand is not"

# AND A FILE NO EXTENSION LIST NAMES. What left one lying in the root, and why
# no list of extensions would ever have named it, is beside the guard in
# check-docs.py. What it cost is here: the run ended in UnicodeDecodeError and
# the checker reported that nothing at all had been checked. inventory-tng-2aor.
scene
printf '# One\n\n%s\n' "$PASSAGE" > one.md
printf 'Something else entirely, at length, about a different subject.\n' > two.md
printf '\xb5\xfe\xff\x00\x01not text at all' > trace.pb
expect 0 "No prose repeated" "a binary file no exclusion names is stepped over"

# The half that matters more: stepped OVER, not stopped AT. A reader that gave
# up on the first undecodable file would report this run as clean.
scene
printf '# One\n\n%s\n' "$PASSAGE" > one.md
printf '\xb5\xfe\xff\x00\x01not text at all' > trace.pb
printf '# Two\n\n%s\n' "$PASSAGE" > two.md
expect 1 "say the same thing" "and the files after it are still read"

# SAID, NOT SWALLOWED. A skip nobody is told about is this rule quietly ceasing
# to apply to a file, and the file it will happen to is not a stray binary -- it
# is a page somebody's editor saved as CP-1252, where one smart quote is enough.
# Reported green with the page never read is the worst of the three outcomes.
scene
printf '# One\n\n%s\n' "$PASSAGE" > one.md
printf 'Caf\xe9 na\xefve \xe9l\xe8ve, at length, about a different subject entirely.\n' > legacy.md
expect 0 "legacy.md could not be read as utf-8 text" "a file that is not utf-8 is named, not dropped in silence"

# THE OTHER WAY A FILE CANNOT BE READ. The first version of the guard named
# UnicodeDecodeError alone, so a file with no read permission still ended the
# run in "the reader failed, so nothing was checked" -- the precise failure
# inventory-tng-2aor was filed about, surviving its own fix.
#
# Skipped as root, who can read a mode-000 file and would find nothing to test.
if [[ $EUID -ne 0 ]]; then
  scene
  printf '# One\n\n%s\n' "$PASSAGE" > one.md
  printf '# Two\n\n%s\n' "$PASSAGE" > two.md
  printf 'unreadable\n' > locked.md
  chmod 000 locked.md
  expect 1 "say the same thing" "a file that cannot be read does not take the run down"
  chmod 644 locked.md
fi

# The one exclusion that is a path rather than a kind, pinned because it is the
# exception to the paragraph above and was described for a while as though it
# did not exist. If a second directory is ever added to the pattern, this is
# where somebody notices that "read by default" has stopped being the rule.
scene
printf '# One\n\n%s\n' "$PASSAGE" > one.md
mkdir -p .beads
printf '# %s\n' "$PASSAGE" > .beads/README.md
expect 0 "No prose repeated" "and neither is the tracker's own directory"

# --- the corpus is the checkout, not the index ----------------------------
#
# inventory-tng-hoc6. Every case above stages before it looks, which is what the
# enumeration used to need. A page written and not yet added was invisible, so
# an author ran this, saw green, committed, and watched CI fail on the file they
# had just checked. These stage nothing at all.
unstaged_check() {
  (cd "$WORK/repo" && "$CHECK" "$@")
}
check unstaged_check

scene
printf '# One\n\n%s\n' "$PASSAGE" > one.md
printf '# Two\n\n%s\n' "$PASSAGE" > two.md
expect 1 "say the same thing" "a file written and never added is read"

# The shape the bug actually took: the new page is the second copy, and the
# first has been in the repository for months.
scene
printf '# One\n\n%s\n' "$PASSAGE" > one.md
git add -A >/dev/null 2>&1
git commit -qm one >/dev/null 2>&1
printf '# Two\n\n%s\n' "$PASSAGE" > two.md
expect 1 "two.md" "a new page is weighed against what is already committed"

# And .gitignore still decides, so a build directory somebody's tooling left
# behind is not suddenly prose.
scene
printf '# One\n\n%s\n' "$PASSAGE" > one.md
printf 'build/\n' > .gitignore
mkdir -p build
printf '# Built\n\n%s\n' "$PASSAGE" > build/one.md
expect 0 "No prose repeated" "a file .gitignore covers is not read"

verdict
