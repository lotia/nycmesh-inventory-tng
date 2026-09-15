#!/usr/bin/env bash
# The configuration checker, held to both halves of its job.
#
# The half worth the effort is what it must LEAVE ALONE, for the reason
# scripts/check-docs.test.sh gives about its own subject. Here the trap is
# specific: a rule demanding a comment on the line above every value reports
# three times as many faults as exist, because these files group related values
# under one comment naming each in turn -- which is the better way to write
# them. Every leave-alone case below is one a naive checker fails.
#
# Usage: scripts/check-config.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
CHECK="$HERE/check-config.sh"
. "$HERE/testlib.sh"
workspace

# The four files the checker reads, built from scratch each case so that a case
# says what the tree holds rather than inheriting it.
scene() {
  rm -rf "$WORK/repo"
  mkdir -p "$WORK/repo/scripts" \
           "$WORK/repo/infra/helm/inventory-tng/templates" \
           "$WORK/repo/docs"
  : >"$WORK/repo/.env.sample"
  printf 'services:\n  backend:\n    environment:\n' >"$WORK/repo/compose.yaml"
  : >"$WORK/repo/infra/helm/inventory-tng/values.yaml"
  # A helper that renders SOMETHING, and a table that documents it. An empty
  # helper is refused on purpose -- see the case about a pattern that has
  # stopped matching -- so the baseline tree has to be a realistic one rather
  # than an empty file every case then has to work around.
  printf -- '- name: BASELINE\n' >"$WORK/repo/infra/helm/inventory-tng/templates/_helpers.tpl"
  printf '| `BASELINE` | no | chart | the tree these cases start from |\n' \
    >"$WORK/repo/docs/deployment.md"
}

run_check() { "$CHECK" "$WORK/repo"; }
check run_check

echo "check-config.sh"

# --------------------------------------------------------------------------
# What it has to notice
# --------------------------------------------------------------------------

scene
printf 'LONELY=1\n' >>"$WORK/repo/.env.sample"
expect 1 "LONELY is set with nothing saying what it is for" \
  "a variable with no comment at all is refused"

scene
printf '# What this one is for.\nEXPLAINED=1\n\nBARE=2\n' >>"$WORK/repo/.env.sample"
expect 1 "BARE" "a blank line breaks the comment away from what follows it"

scene
printf '      SILENT: x\n' >>"$WORK/repo/compose.yaml"
expect 1 "SILENT" "an unexplained compose entry is refused"

scene
printf 'quiet: 1\n' >>"$WORK/repo/infra/helm/inventory-tng/values.yaml"
expect 1 "quiet" "an unexplained chart value is refused"

# The rule that matters to an operator rather than a reader, and the one that
# found CLIENT_REPORT_RATE: set, rendered, and absent from the document that
# lists what may be set.
scene
printf -- '- name: RENDERED_NOWHERE\n' >>"$WORK/repo/infra/helm/inventory-tng/templates/_helpers.tpl"
expect 1 "docs/deployment.md never mentions it" \
  "a variable the chart renders and the document omits is refused"

# --------------------------------------------------------------------------
# What it has to leave alone
# --------------------------------------------------------------------------

scene
printf '# What this is for.\nEXPLAINED=1\n' >>"$WORK/repo/.env.sample"
expect 0 "says what it is for" "a comment above the value is enough"

# THE CASE A NAIVE CHECKER FAILS. One comment, several adjacent values.
scene
cat >>"$WORK/repo/.env.sample" <<'ENV'
# Three names the database answers to. The image reads them once, at first
# start, and changing one afterwards renames nothing.
FIRST=a
SECOND=b
THIRD=c
ENV
expect 0 "says what it is for" "one comment covering a group of adjacent values is enough"

# The YAML half of the same idea: a comment on an enclosing key covers what is
# nested beneath it, so a block does not need it repeated on every leaf.
scene
cat >>"$WORK/repo/infra/helm/inventory-tng/values.yaml" <<'YAML'
# What the scheduler reserves and what the kernel kills over.
resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    memory: 512Mi
YAML
expect 0 "says what it is for" "a comment on an enclosing key covers the leaves beneath it"

scene
printf -- '- name: DOCUMENTED\n' >>"$WORK/repo/infra/helm/inventory-tng/templates/_helpers.tpl"
printf '| `DOCUMENTED` | no | chart | what it does |\n' >>"$WORK/repo/docs/deployment.md"
expect 0 "says what it is for" "a rendered variable with a row in the table is enough"

# --------------------------------------------------------------------------
# The allowlist, and the line on it that buys nothing
# --------------------------------------------------------------------------

scene
printf 'EXCUSED=1\n' >>"$WORK/repo/.env.sample"
printf '.env.sample:EXCUSED: it is the compose database on a laptop and holds invented data.\n' \
  >>"$WORK/repo/scripts/check-config.allow"
expect 0 "says what it is for" "an allowed value with a reason is left alone"

scene
printf 'EXCUSED=1\n' >>"$WORK/repo/.env.sample"
printf '.env.sample:EXCUSED\n' >>"$WORK/repo/scripts/check-config.allow"
expect 1 "EXCUSED" "an allowlist line with no reason excuses nothing"

# --------------------------------------------------------------------------
# A compose value .env cannot reach
# --------------------------------------------------------------------------
# The third fault in check-config.py's header.

scene
printf '# The hosts Django admits.\n      HOSTS: localhost,127.0.0.1\n' >>"$WORK/repo/compose.yaml"
expect 1 "HOSTS is a literal .env cannot reach" "a literal is refused, however well it is explained"

scene
printf '# Explained.\n      HOSTS: backend,${HOSTS:-}\n' >>"$WORK/repo/compose.yaml"
expect 1 "HOSTS is a literal" "and so is a literal with an interpolation after it"

scene
printf '# Explained.\n      QUOTED: "true"\n' >>"$WORK/repo/compose.yaml"
expect 1 "QUOTED is a literal" "a quoted literal is one too"

scene
printf '# Each declared here, as the rule below asks.\nDEFAULTED=\nDEFAULTED_UNSET=\nREQUIRED=\nREQUIRED_UNSET=\nALTERNATIVE=\nALTERNATIVE_UNSET=\nNESTED=\nOTHER=\nDEEPER=\nBARE=\nDOLLAR=\nTRAILING=\n' \
  >>"$WORK/repo/.env.sample"
cat >>"$WORK/repo/compose.yaml" <<'YAML'
      # Every spelling compose gives an interpolation.
      DEFAULTED: ${DEFAULTED:-x}
      DEFAULTED_UNSET: ${DEFAULTED_UNSET-x}
      REQUIRED: ${REQUIRED:?set it}
      REQUIRED_UNSET: ${REQUIRED?set it}
      ALTERNATIVE: ${ALTERNATIVE:+set}
      ALTERNATIVE_UNSET: ${ALTERNATIVE_UNSET+set}
      NESTED: ${NESTED:-${OTHER:-${DEEPER:-x}}-suffix}
      BARE: ${BARE}
      DOLLAR: $DOLLAR
      ALIASED: *database-url
      TRAILING: ${TRAILING:-x}   # a comment after it is not a literal
YAML
expect 0 "says what it is for" "a whole-value interpolation, in any spelling, or an alias is reachable"

# YAML forces quotes on a default that holds `: ` or ` #`; compose interpolates
# inside them all the same, so the quotes are not what makes a literal.
scene
printf '# Declared.\nLEVELS=\nSINGLE=\n' >>"$WORK/repo/.env.sample"
cat >>"$WORK/repo/compose.yaml" <<'YAML'
      # Quoted because of the colon, and reachable all the same.
      LEVELS: "${LEVELS:-django.request: WARNING}"
      SINGLE: '${SINGLE:-x}'
YAML
expect 0 "says what it is for" "a quoted whole-value interpolation is reachable"

scene
printf '# Explained.\n      TAIL: "${TAIL}-suffix"\n' >>"$WORK/repo/compose.yaml"
expect 1 "TAIL is a literal" "quotes do not turn a literal tail into an interpolation"

scene
printf '# Wiring, not a setting.\n      ORIGIN: http://backend:8000\n' >>"$WORK/repo/compose.yaml"
printf 'compose.yaml:ORIGIN=literal: the backend by service name, dialled by nothing outside the network.\n' \
  >>"$WORK/repo/scripts/check-config.allow"
expect 0 "says what it is for" "a literal excused by name, with a reason, is left alone"

# The two excuses are different decisions, so one does not stand for the other.
scene
printf '      ORIGIN: http://backend:8000\n' >>"$WORK/repo/compose.yaml"
printf 'compose.yaml:ORIGIN: obvious enough.\n' >>"$WORK/repo/scripts/check-config.allow"
expect 1 "ORIGIN is a literal" "an excuse from prose does not excuse a literal"

scene
printf '      ORIGIN: http://backend:8000\n' >>"$WORK/repo/compose.yaml"
printf 'compose.yaml:ORIGIN=literal: wiring.\n' >>"$WORK/repo/scripts/check-config.allow"
expect 1 "ORIGIN is set with nothing saying what it is for" "nor does an excuse for a literal excuse it from prose"

# The name inside the braces: w5r7 with one letter missing has the right shape
# and reaches nothing.
scene
printf '# Explained.\nHOSTS=\n' >>"$WORK/repo/.env.sample"
printf '# Explained.\n      HOSTS: ${HOST:-localhost}\n' >>"$WORK/repo/compose.yaml"
printf 'compose.yaml:HOSTS=literal: not what this is.\ncompose.yaml:HOSTS: nor this.\n' >>"$WORK/repo/scripts/check-config.allow"
expect 1 'HOSTS reads ${HOST}, which .env.sample never declares' "a name .env.sample never declares is refused, however well shaped, and neither excuse reaches it"

scene
printf '# Explained, and left unset on purpose.\n#FORMAT=console\n' >>"$WORK/repo/.env.sample"
printf '# Explained.\n      FORMAT: ${FORMAT:-json}\n' >>"$WORK/repo/compose.yaml"
expect 0 "says what it is for" "a declaration commented out is still a declaration"

scene
printf 'EXPLAINED=1\n\n#FORMAT=console\n' >>"$WORK/repo/.env.sample"
expect 1 "FORMAT is set with nothing saying what it is for" "and is held to the comment rule like a live one"

scene
printf '# Explained.\nOUTER=\n' >>"$WORK/repo/.env.sample"
printf '# Explained.\n      OUTER: ${OUTER:-${INNER:-x}}\n' >>"$WORK/repo/compose.yaml"
expect 1 'reads ${INNER}' "every name in a nested default is held to it"


scene
printf '# Explained.\nEXTRA=\n' >>"$WORK/repo/.env.sample"
printf '# Explained.\n      EXTRA: backend,${EXTRAA:-}\n' >>"$WORK/repo/compose.yaml"
printf 'compose.yaml:EXTRA=literal: a prefix, with the interpolation after it.\n' >>"$WORK/repo/scripts/check-config.allow"
expect 1 'EXTRA reads ${EXTRAA}' "a literal excused for its prefix is still held to the name after it"

# What is not a name: compose spells a literal dollar `$$`, and a trailing
# comment is prose.
scene
printf '# Explained.\nSECRET=\nHOSTS=\n' >>"$WORK/repo/.env.sample"
cat >>"$WORK/repo/compose.yaml" <<'YAML'
      # A literal dollar in the default.
      SECRET: ${SECRET:-pa$$word}
      # A dollar in the comment.
      HOSTS: ${HOSTS:-localhost} # was $OLD_HOSTS
YAML
expect 0 "says what it is for" "an escaped dollar and one in a trailing comment read nothing"

# --------------------------------------------------------------------------
# The ways this could stop guarding without saying so
# --------------------------------------------------------------------------

# THE FAIL-OPEN THE REVIEW FOUND. Anchored at column zero, the pattern matched
# nothing the moment the helper was indented, and a rule matching nothing
# reports nothing and passes.
scene
printf -- '  - name: INDENTED_AND_UNDOCUMENTED\n' \
  >>"$WORK/repo/infra/helm/inventory-tng/templates/_helpers.tpl"
expect 1 "docs/deployment.md never mentions it" \
  "an indented entry is still read, rather than silently skipped"

# And the other half: a pattern that has stopped matching altogether is a
# checker that has stopped checking, which must be louder than a pass.
scene
printf -- 'env:\n  - nom: NOT_THE_KEY\n' \
  >"$WORK/repo/infra/helm/inventory-tng/templates/_helpers.tpl"
expect 2 "stopped matching" "a helper it can find nothing in is refused, not passed"

# An excuse belongs to one value in one file. A YAML leaf name is not unique.
scene
cat >>"$WORK/repo/infra/helm/inventory-tng/values.yaml" <<'YAML'
backend:
  port: 8000
frontend:
  port: 8080
YAML
printf 'infra/helm/inventory-tng/values.yaml:backend.port: obvious enough.\n' \
  >>"$WORK/repo/scripts/check-config.allow"
expect 1 "frontend.port" "an excuse names one path, and does not silence its namesake"

# A surface that has been renamed is named, rather than dying with a stack.
scene
rm -f "$WORK/repo/.env.sample"
expect 2 "cannot read .env.sample" "a file it cannot read is named"

# --------------------------------------------------------------------------
# The repository this ships in
# --------------------------------------------------------------------------

# Run against the real tree, which is what CI does with it, so that the suite
# passing and the repository passing cannot come apart.
real_check() { "$CHECK" "$HERE/.."; }
check real_check
expect 0 "says what it is for" "this repository's own configuration passes"

verdict
