#!/usr/bin/env bash
# What repository.sh insists on before its guards will work at all.
#
# The file had no suite because everything in it talks to git or to GitHub, and
# the two callers' suites cover it from above. What they cannot cover is the
# part that runs before either of them gets a say: repository.sh refuses to be
# read into a shell that has not sourced report.sh first. Its own header argues
# for that, and says what an absent guard would do instead of refusing.
#
# WHAT THAT ARGUMENT LACKED WAS A CASE. The refusal had never been executed --
# no caller can reach it, because every one of them sources the two files in
# the right order -- and it protects against failing OPEN, where a broken guard
# is indistinguishable from a satisfied one. So it is exercised here, from
# outside, by building the wrong order on purpose.
#
# `listing_cut_short` is asked about $ISSUE_LIMIT rather than about 1000. A
# suite that wrote the number would be the fourth copy of it, which is the thing
# inventory-tng-cwpa.14 moved it here to stop.
#
# Usage: scripts/repository.test.sh

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/testlib.sh"
workspace

# NEITHER FILE IS SOURCED INTO THIS SHELL. testlib's `issue_limit` gives one
# reason -- the two files define a `verdict` this harness already has -- and the
# reason here is the stronger one: the refusal under test happens AT SOURCE
# TIME, so the only shell that can observe it is one this suite is not running
# in. Every case gets its own.
#
# sourced <what that shell is given first> -- what repository.sh does when read
# into a shell holding exactly that.
sourced() {
  printf '%s\n. "%s/repository.sh"\necho "read to the end"\n' "$1" "$HERE" >"$WORK/scene.sh"
  bash "$WORK/scene.sh" 2>&1
}

echo "the order the two files are sourced in"

out=$(sourced ':'); status=$?
assert "$out" "$status" 2 "source report.sh before this file" \
  "read on its own, it says what is missing rather than defining broken guards"
assert "$out" "$status" 2 "guards need refuse" "and names the one it looked for first"
refute "$out" "$status" 2 "read to the end" "and stops there rather than carrying on"

# THE ARM THE SECOND NAME ADDED, and the reason it is a case of its own: a shell
# holding `refuse` looks from the inside like one that sourced report.sh, and it
# is the OTHER half that `listing_cut_short` needs. repository.sh's own guard
# says what an absent one answers instead of refusing.
out=$(sourced 'refuse() { :; }'); status=$?
assert "$out" "$status" 2 "guards need count_lines" \
  "a shell holding only half of what the guards need is refused too"
refute "$out" "$status" 2 "read to the end" "and it never gets as far as defining anything"

out=$(sourced ". \"$HERE/report.sh\""); status=$?
assert "$out" "$status" 0 "read to the end" "and in the order every caller uses, it is read without complaint"

echo
echo "a page that came back exactly full"
# The rule three scripts now share. Asked in terms of $ISSUE_LIMIT, so that
# raising the limit moves the boundary these cases pin along with it.

cat >"$WORK/cut-short.sh" <<'SCENE'
#!/usr/bin/env bash
# repository.sh sourced the way its callers source it, then asked about a
# listing of $2 lines -- $2 being an arithmetic expression, so a case can say
# "one under the limit" without knowing what the limit is.
here=$1
. "$here/report.sh"
. "$here/repository.sh"
# `seq` and a command substitution, which drops the trailing newline for us.
# Accumulating the lines by hand meant a final trim whose only job was to undo
# what the loop had just added -- and getting that one character wrong moves the
# boundary every case below is here to pin.
listing_cut_short "$(seq 1 "$(($2))")"
SCENE

cut_short() { bash "$WORK/cut-short.sh" "$HERE" "$1"; }

cut_short 0
exits $? 1 "nothing at all is not a full page"

cut_short 'ISSUE_LIMIT - 1'
exits $? 1 "and neither is one line short of the limit"

cut_short 'ISSUE_LIMIT'
exits $? 0 "a page filling the limit is one whose end nothing has seen"

# GitHub cannot return more than was asked for, so this is not a case about
# reality. It is a case about the comparison being `-ge`: written `-eq`, the
# guard would be true of exactly one length and silently false either side of a
# limit that had drifted from the number actually sent.
cut_short 'ISSUE_LIMIT + 1'
exits $? 0 "and so is anything past it, whatever put it there"

# AND THE ADVICE IS NOT EMPTY, which is worth a case because nothing else would
# notice: all three callers hand it to `note`, and `note ""` prints a bare
# bullet. A refusal that says what is wrong and then offers a blank line where
# the way out should be is the failure this is cheapest to catch here.
printf '. "%s/report.sh"\n. "%s/repository.sh"\nprintf %%s "$ISSUE_LIMIT_ADVICE"\n' \
  "$HERE" "$HERE" >"$WORK/advice.sh"
advice=$(bash "$WORK/advice.sh")
assert "$advice" 0 0 "repository.sh" "the advice names the file the constant is actually in"

verdict
