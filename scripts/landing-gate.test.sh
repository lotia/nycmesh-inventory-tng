#!/usr/bin/env bash
# The landing gate's suite.
#
# What it is mostly about is the failure modes, because those are what
# inventory-tng-3sp was: the gate had three ways to stop working and every one
# of them permitted the merge, silently. A guard whose broken state is
# indistinguishable from its satisfied state is the thing being tested here, so
# each dependency gets a case that removes it and asserts a REFUSAL.
#
# Everything is hermetic. gh is a stub on PATH answering from fixtures, so no
# case reaches the network, and every case runs inside a throwaway repository
# so the receipts it writes are its own.

set -uo pipefail
. "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/testlib.sh"

GATE=$(readlink -f "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/landing-gate.sh")

workspace
# A batch branch, because that is where the work happens and because the gate
# now asks git which branch a push would land on rather than reading the word
# `main` off the command line. `$WORK/on-main` below is the other side of that.
new_repo "$WORK/repo" batch/test
REPO="$WORK/repo"
new_repo "$WORK/on-main" main
ON_MAIN="$WORK/on-main"

REAL_PATH=$PATH
BIN="$WORK/bin"
mkdir -p "$BIN"

HEAD_OID=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa

# A gh that answers from files this suite writes, so a case says what GitHub
# said by writing it down rather than by reaching for it.
cat >"$BIN/gh" <<'STUB'
#!/usr/bin/env bash
[[ -n "${GH_FAILS:-}" ]] && exit 1
args="$*"
case "$args" in
  # The combined form first: `record` asks for all three at once, so that the
  # head and the evidence come from the same moment.
  *"--json headRefOid,comments,reviews"*) cat "$GH_FIXTURES/pr.json" ;;
  *"--json headRefOid"*)  cat "$GH_FIXTURES/head" ;;
  *"--json number"*)      cat "$GH_FIXTURES/number" ;;
  *"pr checks"*)          cat "$GH_FIXTURES/failing" ;;
  *) exit 1 ;;
esac
STUB
chmod +x "$BIN/gh"

FIX="$WORK/fixtures"
mkdir -p "$FIX"
export GH_FIXTURES=$FIX
echo "$HEAD_OID" >"$FIX/head"
echo 7 >"$FIX/number"
echo 0 >"$FIX/failing"
echo "{\"headRefOid\":\"$HEAD_OID\",\"comments\":[],\"reviews\":[]}" >"$FIX/pr.json"

# A PATH holding everything the gate needs, and one missing a named program.
# `env -i` is not used: the gate shells out to git, which wants a HOME.
full_path() { printf '%s:%s' "$BIN" "$REAL_PATH"; }

without() {
  local drop=$1
  local dir="$WORK/without-$drop"
  mkdir -p "$dir"
  local p
  for p in git bash sed grep cat mktemp dirname readlink rm mkdir printf python3 gh; do
    [[ "$p" == "$drop" ]] && continue
    if [[ "$p" == gh ]]; then
      ln -sf "$BIN/gh" "$dir/gh"
    else
      ln -sf "$(command -v "$p" 2>/dev/null)" "$dir/$p" 2>/dev/null
    fi
  done
  printf '%s' "$dir"
}

# ---------------------------------------------------------------------------
# check mode
# ---------------------------------------------------------------------------

# The hook payload a Bash tool call arrives as.
payload() {
  PAYLOAD_CMD=$1 python3 -c '
import json, os
print(json.dumps({"tool_input": {"command": os.environ["PAYLOAD_CMD"]}}))'
}

# in_repo [repo] [PATH] -- <args to the gate>  -> everything it printed
#
# The one place that knows how to invoke the gate, because there were five and
# the five dependency cases -- the ones this suite exists for -- were the ones
# written the long way.
#
# CLAUDE_PROJECT_DIR is set explicitly rather than inherited: the gate prefers
# it over `git rev-parse`, and a suite that let the surrounding session's value
# through would quietly test against the real repository. `status` and `clear`
# never reach gh, so the gh variables are harmless to them.
in_repo() {
  local repo=${1:-$REPO} path=${2:-$(full_path)}
  shift 2
  (cd "$repo" && env PATH="$path" GH_FIXTURES="$FIX" CLAUDE_PROJECT_DIR="$repo" \
    GH_DEADLINE=5 ${GH_FAILS:+GH_FAILS=1} "$GATE" "$@") 2>&1
}

# A hook payload written out by hand, for the envelopes `payload` cannot make.
payload_raw() { printf '%s' "$1" | in_repo "" "" check; }

# gate <command line> [PATH] [repo]  -> what the hook printed
gate() {
  local cmd=$1 path=${2:-} repo=${3:-$REPO}
  printf '%s' "$(payload "$cmd" | in_repo "$repo" "$path" check)"
}

# The decision, as a word, so a case reads as what it means.
decision() {
  local out=$1
  [[ -z "$out" ]] && { echo PERMIT; return; }
  DECISION_JSON=$out python3 -c '
import json, os, sys
try:
    d = json.loads(os.environ["DECISION_JSON"])
except ValueError:
    print("MALFORMED: " + os.environ["DECISION_JSON"][:200]); sys.exit()
print(d["hookSpecificOutput"]["permissionDecision"].upper() + ": "
      + d["hookSpecificOutput"]["permissionDecisionReason"])'
}

# case_is <command> <PERMIT|substring of the refusal> <name> [repo] [PATH]
#
# `PERMIT` needs no branch of its own: when the expectation is the word, the
# word is what `$2` already holds.
case_is() {
  assert "$(decision "$(gate "$1" "${5:-}" "${4:-$REPO}")")" 0 0 "$2" "$3"
}

echo "what the gate reads as a guarded action"
case_is "ls -la"                                 PERMIT "an unrelated command is permitted"
case_is "git push"                               PERMIT "a push to a batch branch is free"
case_is "git push --force-with-lease"            PERMIT "--force-with-lease is free"
case_is "git push --force-with-lease origin batch/x" PERMIT "and stays free with a remote and a branch after it"
case_is "bd create --title='gh pr merge 7 fails'" PERMIT "the words inside a quoted string are data"
case_is "git push --force origin batch/x"        "bare --force" "a bare --force is refused"
case_is "git push -f origin batch/x"             "bare --force" "-f is the same flag and is refused with it"
case_is "git push --follow-tags"                 PERMIT "a flag that merely contains -f is not it"
case_is "gh pr merge 7 --rebase"                 "No review cycle" "an unrecorded merge is refused"

echo
echo "the prefilter matches words, not substrings"
# The words that made this cost something: every one of them contains `gh` or
# `push`, and under the old substring prefilter every one started a python3 that
# could only ever print nothing -- on 15% of every Bash command in a session.
case_is "echo caught enough through straight"    PERMIT "English prose that merely contains the letters"
case_is "npx playwright test --workers=1"        PERMIT "and a program whose name does"
case_is "pushd /tmp && ls"                       PERMIT "and a builtin that starts with one"
# The other direction is the one that must not be got wrong, and it is covered
# by every guarded spelling above and below this block: the indirections put
# other WORDS in front of `gh`, never other letters, so all thirty still land.
# `/usr/bin/gh pr merge 7` is the case that pins the boundary itself -- `/` is
# not a word character, so a pathed program is still seen.

echo
echo "which branch a push lands on, asked of git rather than pattern-matched"
case_is "git push origin main"                   "lands on: main" "a push naming main is refused"
case_is "git push origin HEAD:main"              "lands on: main" "and so is the refspec spelling"
case_is "git push origin batch/main-fix"         PERMIT "a branch that merely contains the word is not main"
case_is "git push origin feature/main-page"      PERMIT "nor is one that contains it in the middle"
case_is "git push" "lands on: main" "a bare push on a checked-out main is refused" "$ON_MAIN"
case_is "git push origin HEAD" "lands on: main" "and so is 'origin HEAD' there" "$ON_MAIN"

echo
echo "indirections that used to walk past the matcher"
case_is "env gh pr merge 7"                      "No review cycle" "env gh"
case_is "command gh pr merge 7"                  "No review cycle" "command gh"
case_is "/usr/bin/gh pr merge 7"                 "No review cycle" "an absolute path to gh"
case_is "echo 7 | xargs gh pr merge"             "No review cycle" "a pipe into xargs"
case_is "bash -c 'gh pr merge 7 --rebase'"       "No review cycle" "bash -c, whose payload is a quoted span"
case_is "gh api -X PUT repos/o/r/pulls/7/merge"  "No review cycle" "the REST spelling of a merge"

echo
echo "and the five the review of PR #30 found still open"
case_is "GH_TOKEN=x gh pr merge 7"               "No review cycle" "a leading VAR=value assignment"
case_is "GIT_DIR=x gh pr merge 7"                "No review cycle" "and another, before an unwrapped program"
case_is "(gh pr merge 7 --rebase)"               "No review cycle" "a subshell is command position"
case_is "{ gh pr merge 7 --rebase; }"            "No review cycle" "so is a brace group"
case_is "if true; then gh pr merge 7; fi"        "No review cycle" "so is the body of an if"
case_is "git -C /tmp/repo push --force origin batch/x" "bare --force" "git -C, whose flag takes a separate value"
case_is "git -c user.name=x push --force"        "bare --force" "and git -c, which does the same"
case_is 'gh api -X PUT "repos/o/r/pulls/7/merge"' "No review cycle" "a REST merge whose URL is quoted"
# The other side of that fix, and it bit within the hour of being written:
# searching the fully raw text reached into heredoc bodies too. `classify` in
# landing-gate.sh says why the two kinds of quoting part company here.
case_is "$(printf 'cat > f.sh <<%sEOF%s\ngh api -X PUT repos/o/r/pulls/7/merge\nEOF\ngh api graphql -f query=x\n' "'" "'")" \
  PERMIT "a heredoc quoting the REST URL is a document, not a merge"
case_is "gh pr merge --rebase 7"                 "No review cycle" "a pull request number that follows a flag"
case_is "gh pr merge 7 --repo other/repo"        "names another repository" "another repository is refused, not guessed"
case_is "gh pr merge 7 -R other/repo"            "names another repository" "and the short spelling of that flag"

echo
echo "every dependency that goes missing refuses rather than permits"
case_is "gh pr merge 7" "missing: python3" "no python3 refuses a merge" "" "$(without python3)"
case_is "ls -la" PERMIT "no python3 still permits what the gate does not guard" "" "$(without python3)"
case_is "gh pr merge 7" "missing: gh" "no gh refuses a merge" "" "$(without gh)"
case_is "gh pr ready 7" "missing: gh" "no gh refuses marking a pull request ready" "" "$(without gh)"

# Not a --force push: that one is refused by reading the command line alone and
# never needs git. This is the arm that has to ASK git which branch the push
# lands on, so a missing git leaves it unable to tell -- and it must refuse.
case_is "git push origin batch/x" "missing: git" \
  "no git refuses a push, rather than permitting every command" "" "$(without git)"

# The matcher's own failure, which used to be `except Exception: sys.exit(0)`
# and therefore a permit. The envelope carries the words the prefilter looks
# for but not the key the matcher reads, which is the shape of a hook contract
# that moved underneath it.
out=$(decision "$(payload_raw '{"tool_input":{"cmd":"gh pr merge 7"}}')")
assert "$out" 0 0 "could not find out" "a payload the matcher cannot read refuses"

GH_FAILS=1
out=$(decision "$(gate "gh pr ready 7")")
assert "$out" 0 0 "could not find out" "a gh that cannot answer refuses ready, rather than reading it as green"
refute "$out" 0 0 "Install an answer" "and it does not tell anybody to install an answer"
unset GH_FAILS

echo
echo "marking ready"
echo 2 >"$FIX/failing"
case_is "gh pr ready 7"                          "is not green" "a pull request with failing checks is refused"
echo 0 >"$FIX/failing"
case_is "gh pr ready 7"                          PERMIT "a green pull request may be marked ready"

# ---------------------------------------------------------------------------
# record, and the evidence it stores
# ---------------------------------------------------------------------------

RECEIPTS="$REPO/.claude/.review-receipts.json"

record() { in_repo "" "" record "${1:-7}"; }

pr_json() { # <code-review evidence?> <simplify evidence?>
  # The head goes in the same document, because `record` now reads all three
  # fields from one answer rather than asking twice.
  CR=$1 SIMP=$2 HEAD_NOW="$(cat "$FIX/head")" python3 -c '
import json, os
comments, reviews = [], []
if os.environ["CR"] == "review":
    reviews.append({"id": "R_1", "author": {"login": "someone"},
                    "submittedAt": "2026-08-24T10:00:00Z",
                    "commit": {"oid": "b" * 40}, "state": "COMMENTED"})
elif os.environ["CR"] == "marker":
    comments.append({"id": "C_1", "author": {"login": "someone"},
                     "createdAt": "2026-08-24T10:00:00Z", "url": "http://x/1",
                     "body": "findings\n<!-- review-cycle: code-review -->"})
if os.environ["SIMP"] == "marker":
    comments.append({"id": "C_2", "author": {"login": "someone"},
                     "createdAt": "2026-08-24T11:00:00Z", "url": "http://x/2",
                     "body": "<!-- review-cycle: simplify -->\nfindings"})
print(json.dumps({"headRefOid": os.environ["HEAD_NOW"],
                  "comments": comments, "reviews": reviews}))' >"$FIX/pr.json"
}

echo
echo "record stores what it found, and refuses when it found nothing"
rm -f "$RECEIPTS"
pr_json none none
out=$(record); status=$?
assert "$out" "$status" 1 "code-review pass ran" "a pull request carrying nothing cannot be recorded"
refute "$out" "$status" 1 "Merging is unblocked" "and it does not claim to have recorded anything"
assert "$([[ -f "$RECEIPTS" ]] && echo exists || echo absent)" 0 0 "absent" "and it writes no receipt"

pr_json review none
out=$(record); status=$?
assert "$out" "$status" 1 "simplify pass ran" "a review pass alone is not the whole cycle"
refute "$out" "$status" 1 "code-review pass ran" "and the stage that did run is not complained about"

pr_json review marker
out=$(record); status=$?
assert "$out" "$status" 0 "Merging is unblocked" "both stages evidenced records the cycle"
assert "$(cat "$RECEIPTS")" 0 0 '"head"' "the receipt names the head"
assert "$(cat "$RECEIPTS")" 0 0 "R_1" "the receipt names the review it found"
assert "$(cat "$RECEIPTS")" 0 0 "C_2" "the receipt names the comment it found"
refute "$(cat "$RECEIPTS")" 0 0 '"stages"' "and stores no hardcoded stage list"

pr_json marker marker
out=$(record); status=$?
assert "$out" "$status" 0 "Merging is unblocked" "a marker comment is evidence for code-review too"

GH_FAILS=1
out=$(record); status=$?
assert "$out" "$status" 1 "Could not read pull request" "a gh that cannot answer records nothing"
unset GH_FAILS

echo
echo "the head the receipt was written against"
pr_json review marker
record >/dev/null
case_is "gh pr merge 7 --rebase"                 PERMIT "a merge at the recorded head is permitted"

# The shape the gate itself used to write: a head, and stages as a literal
# with nothing behind them. It has to be refused, or every receipt already on
# somebody disk goes on permitting the merges this change exists to stop.
RECEIPTS_OLD=$(cat "$RECEIPTS")
HEAD_OID="$HEAD_OID" python3 -c '
import json, os, sys
print(json.dumps({"7": {"head": os.environ["HEAD_OID"],
                        "stages": ["code-review", "simplify"],
                        "recorded_at": "2026-08-24T09:00:00Z"}}))' >"$RECEIPTS"
case_is "gh pr merge 7 --rebase"                 "No review cycle" "a receipt from the old gate, with a head but no evidence, is refused"
printf '%s' "$RECEIPTS_OLD" >"$RECEIPTS"

echo "$HEAD_OID" >"$FIX/head.keep"
echo cccccccccccccccccccccccccccccccccccccccc >"$FIX/head"
case_is "gh pr merge 7 --rebase"                 "has moved since it was reviewed" "a head that moved after the record is refused"

GH_FAILS=1
out=$(decision "$(gate "gh pr merge 7 --rebase")")
assert "$out" 0 0 "could not find out" "a gh that cannot name the current head refuses, rather than skipping the comparison"
assert "$out" 0 0 "to check it is what was reviewed" "and it says which question went unanswered"
unset GH_FAILS
cp -f "$FIX/head.keep" "$FIX/head"

echo
echo "status and clear"
out=$(in_repo "" "" status)
assert "$out" 0 0 "code-review" "status names the evidence behind each receipt"
# A receipts file that is valid JSON but not an object: `pop` raises, the file
# is left alone, and this used to print "Cleared the receipt" anyway -- during
# the one operation somebody runs because they already suspect the receipts are
# wrong.
echo '["not", "an", "object"]' >"$RECEIPTS"
out=$(in_repo "" "" clear 7)
status=$?
refute "$out" "$status" 1 "Cleared the receipt" "a receipts file it cannot edit does not report success"
printf '%s' "$RECEIPTS_OLD" >"$RECEIPTS"

out=$(in_repo "" "" clear 7)
assert "$out" 0 0 "Cleared the receipt" "a single receipt can be cleared"
case_is "gh pr merge 7 --rebase"                 "No review cycle" "and the merge is refused again afterwards"

verdict
