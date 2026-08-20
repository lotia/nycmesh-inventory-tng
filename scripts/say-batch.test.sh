#!/usr/bin/env bash
# What say-batch.sh makes of a listing.
#
# Only the half that needs no network -- the same split repo-settings.test.sh
# makes, and for the same reason. The half with logic is the table, and it only
# ever ran in CI on a real pull request, so the first time it was wrong it
# would have been wrong in front of everybody, with the workflow's
# `|| echo "::warning::"` swallowing it.
#
# Usage: scripts/say-batch.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/testlib.sh"
workspace

# Sourcing gets body_from and stops before anything that wants a pull request.
. "$HERE/say-batch.sh"

echo "say-batch.sh"

listing=$(printf 'abc1234\tinventory-tng-aaa\taaa: Extract the decode loop\n')
body=$(body_from "$listing")

if [[ "$body" == "<!-- batch-contents -->"* ]]; then
  pass "the body opens with the marker it is found again by"
else
  fail_case "the body opens with the marker it is found again by" "$body"
fi

if [[ "$body" == *'| `abc1234` | `inventory-tng-aaa` | Extract the decode loop |'* ]]; then
  pass "a row carries the sha, the issue, and the summary without its prefix"
else
  fail_case "a row carries the sha, the issue, and the summary without its prefix" "$body"
fi

# The summary already opens with the issue's short form, so a row that kept it
# would say the same thing twice in adjacent columns.
if [[ "$body" != *"| aaa: Extract"* ]]; then
  pass "the short form is not repeated beside the full identifier"
else
  fail_case "the short form is not repeated beside the full identifier" "$body"
fi

two=$(printf 'abc1234\tinventory-tng-aaa\taaa: One\ndef5678\tinventory-tng-bbb\tbbb: Two\n')
body=$(body_from "$two")
rows=$(grep -c '^| `' <<<"$body")
if [[ "$rows" -eq 2 ]]; then
  pass "one row per commit"
else
  fail_case "one row per commit" "$body"
fi

# check-batch.sh --list says nothing at all about an empty range, and a table
# with a header and no rows is what should come back if it ever does.
body=$(body_from "")
rows=$(grep -c '^| `' <<<"$body")
if [[ "$rows" -eq 0 ]]; then
  pass "an empty listing makes no rows"
else
  fail_case "an empty listing makes no rows" "$body"
fi

verdict
