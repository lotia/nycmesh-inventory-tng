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

HERE_SCRIPTS=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
GATE=$(readlink -f "$HERE_SCRIPTS/landing-gate.sh")

# The check the gate reads PAST when it asks whether the others are green, in
# both of the two places it asks. Taken from review_cycle rather than typed here
# so that renaming the job in ci.yml fails a case rather than going quiet -- the
# gate reads it from there too, and a suite carrying its own copy would agree
# with itself while disagreeing with the file under test.
REVIEW_CHECK=$(python3 "$HERE_SCRIPTS/review_cycle.py" --check-name)

workspace
# A batch branch, because that is where the work happens and because the gate
# now asks git which branch a push would land on rather than reading the word
# `main` off the command line. `$WORK/on-main` below is the other side of that.
new_repo "$WORK/repo" batch/test
REPO="$WORK/repo"
# A commit and an origin/main to measure against, because the merge guard now
# asks check-batch.sh whether the branch is finished and that question needs a
# range. A repository with neither is not a state any batch is ever in.
started() {
  local repo=$1
  git -C "$repo" commit -q --allow-empty -m "start: the commit a branch forks from"
  git -C "$repo" update-ref refs/remotes/origin/main HEAD
}
started "$REPO"
new_repo "$WORK/on-main" main
ON_MAIN="$WORK/on-main"
started "$ON_MAIN"

REAL_PATH=$PATH
BIN="$WORK/bin"
mkdir -p "$BIN"

# The pull request's head, and it has to be the repository's ACTUAL head now:
# the merge guard refuses to judge a branch that is not the one checked out,
# so a made-up sha would deny every case rather than exercise it.
HEAD_OID=$(git -C "$REPO" rev-parse HEAD)

# A gh that answers from files this suite writes, so a case says what GitHub
# said by writing it down rather than by reaching for it.
cat >"$BIN/gh" <<'STUB'
#!/usr/bin/env bash
[[ -n "${GH_FAILS:-}" ]] && exit 1
args="$*"
# A gh that answers everything EXCEPT the head. GH_FAILS above fails the first
# question the merge arm asks, which is now the body, so it can no longer reach
# the head comparison -- and that comparison being SKIPPED when gh could not
# answer is one of the three silent failures this suite exists for.
[[ -n "${GH_FAILS_HEAD:-}" && "$args" == *"--json headRefOid"* ]] && exit 1
case "$args" in
  # The combined form first: `record` asks for all three at once, so that the
  # head and the evidence come from the same moment.
  *"--json headRefOid,comments,reviews"*) cat "$GH_FIXTURES/pr.json" ;;
  # Before the looser `--json number` arm below, which this string also
  # matches: it asks for number,state,isDraft,... and would be answered with
  # a bare integer.
  *"isDraft"*)            cat "$GH_FIXTURES/stop.json" ;;
  *"--json headRefOid"*)  cat "$GH_FIXTURES/head" ;;
  # The review comments the record arm reads for `in_reply_to_id`, which is the
  # only field that tells a finding from a reply. Default is none; a case that
  # is about a review pass says what it left behind.
  *"/comments"*)          cat "$GH_FIXTURES/findings.json" ;;
  # The body the merge arm reads before anything else, so that a pull request
  # saying do-not-merge is refused for that rather than for its receipt. Each
  # case that is about the marker rewrites this; the default is a body without
  # one, which is what every other case here is about.
  *"--json body"*)        cat "$GH_FIXTURES/body.json" ;;
  *"--json number"*)      cat "$GH_FIXTURES/number" ;;
  # THE LIST, always, because that is what the gate now asks for: it counts what
  # is not green through review_cycle.settled rather than through a --jq it
  # passes here. A stub that answered with a number would be answering a
  # question the gate no longer asks.
  *"pr checks"*) cat "$GH_FIXTURES/checks.json" ;;
  *) exit 1 ;;
esac
STUB
chmod +x "$BIN/gh"

FIX="$WORK/fixtures"
mkdir -p "$FIX"
export GH_FIXTURES=$FIX
echo "$HEAD_OID" >"$FIX/head"
echo 7 >"$FIX/number"
# What `gh pr checks` hands back. The gate counts what is not green itself, so
# this is the list rather than a tally of it -- which is also what lets a case
# say WHICH checks the count reaches, not just how many.
checks_are() { printf '%s' "$1" >"$FIX/checks.json"; }
checks_are '[{"name": "Backend", "state": "SUCCESS"}]'
echo "{\"headRefOid\":\"$HEAD_OID\",\"comments\":[],\"reviews\":[]}" >"$FIX/pr.json"
echo '[]' >"$FIX/findings.json"

# What `gh api .../pulls/N/comments` hands back. A comment with no
# `in_reply_to_id` OPENS a thread and is a finding; one with it is an answer to
# a finding, and the pair is the whole of what this has to tell apart.
a_finding='{"id": 1, "user": {"login": "someone"}, "created_at": "2026-08-31T10:00:00Z", "path": "a.py"}'
a_reply='{"id": 2, "in_reply_to_id": 1, "user": {"login": "someone"}, "created_at": "2026-08-31T10:00:00Z", "path": "a.py"}'
findings_are() { printf '[%s]\n' "$1" >"$FIX/findings.json"; }

# What `gh pr view --json body` hands back. A body, through json.dumps, so a
# case can put a marker on a line of its own without the shell touching it.
body_is() { BODY=${1-} python3 -c '
import json, os, sys
sys.stdout.write(json.dumps({"body": os.environ["BODY"]}))' >"$FIX/body.json"; }
body_is "An ordinary batch."

# What the stop hook asks about: a ready pull request whose checks are green.
# Each case below rewrites this to the state it is about.
stop_scene() {
  STOP_STATE=${1:-OPEN} STOP_DRAFT=${2:-false} STOP_CHECK=${3:-SUCCESS} \
  STOP_HEAD=${4:-$HEAD_OID} STOP_CHECK_NAME=${STOP_CHECK_NAME:-Backend} \
  FIX=$FIX python3 -c '
import json, os
# ALWAYS NAMED, because every entry in a real statusCheckRollup carries a `name`
# or a `context`. The name is what the gate reads past, so the case that pins
# the exclusion sets it; the default is an ordinary job, which is the scene the
# rest of the cases are about.
check = {"conclusion": os.environ["STOP_CHECK"], "name": os.environ["STOP_CHECK_NAME"]}
rollup = [] if os.environ["STOP_CHECK"] == "none" else [check]
scene = {"number": 7, "state": os.environ["STOP_STATE"],
         "isDraft": os.environ["STOP_DRAFT"] == "true",
         "headRefOid": os.environ["STOP_HEAD"], "statusCheckRollup": rollup}
open(os.environ["FIX"] + "/stop.json", "w").write(json.dumps(scene))
'
}
stop_scene

# A PATH holding everything the gate needs, and one missing a named program.
# `env -i` is not used: the gate shells out to git, which wants a HOME.
full_path() { printf '%s:%s' "$BIN" "$REAL_PATH"; }

# Sets WITHOUT rather than printing it, for the reason on `borrow`: a function
# whose output is captured cannot report a program the machine does not have.
# Built once per dropped tool -- the contents never differ for a given one.
without() {
  local drop=$1
  WITHOUT="$WORK/without-$drop"
  [[ -d "$WITHOUT" ]] && return 0
  local dir=$WITHOUT
  # Real programs first, then the `gh` stub over the top -- `gh` is not in the
  # borrowed list because this scene never wants the one on the machine, and
  # naming it there would make the suite depend on a program it deliberately
  # replaces.
  # No `printf`: it is a shell builtin, so the gate uses its own whatever this
  # directory holds, and borrowing it only ever made a symlink to itself.
  local wanted=(git bash sed grep cat mktemp dirname readlink rm mkdir python3)
  local keep=() p
  for p in "${wanted[@]}"; do
    [[ "$p" == "$drop" ]] || keep+=("$p")
  done
  borrow "$dir" "${keep[@]}"
  [[ "$drop" == gh ]] || ln -sf "$BIN/gh" "$dir/gh"
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
    GH_DEADLINE=5 ${GH_FAILS:+GH_FAILS=1} ${GH_FAILS_HEAD:+GH_FAILS_HEAD=1} \
    "$GATE" "$@") 2>&1
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

echo
echo "and the one that actually got a merge through -- inventory-tng-mno6"
# A WRAPPER TAKING A POSITIONAL ARGUMENT, which the options-only pattern could
# not skip. This is the spelling that merged pull request 48 with no receipt;
# WRAP in landing-gate.sh says why it was the ordinary spelling rather than an
# exotic one.
case_is "timeout 180 gh pr merge 7 --rebase"     "No review cycle" "timeout, whose duration is a bare word"
case_is "timeout 2m gh pr merge 7"               "No review cycle" "and a duration carrying a unit"
case_is "timeout -k 10 180 gh pr merge 7"        "No review cycle" "and one carrying two, after a flag"
case_is "env timeout 180 gh pr merge 7"          "No review cycle" "and behind a second wrapper"
case_is "timeout 60 git push --force origin batch/x" "bare --force" "the push guard sees through it too"
# And the other direction, because a matcher that refuses everything guards
# nothing either: the words have to be in command position, not merely present.
case_is "echo timeout 180 gh pr merge 7"         PERMIT "naming the command in an echo is not running it"
# THE SIBLING HOLE, found by asking the same question of the rest of the list
# rather than stopping at the one that had been used. A flag carrying its value
# as a separate word breaks the options pattern exactly as a positional
# duration did.
case_is "nice -n 5 timeout 60 gh pr merge 7"     "No review cycle" "a flag whose value is a separate word"
case_is "xargs -n 1 gh pr merge 7"               "No review cycle" "and the same shape on xargs"
case_is "stdbuf -o 0 gh pr merge 7"              "No review cycle" "and on stdbuf"
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
echo
echo "every dependency that goes missing refuses rather than permits"
without python3
case_is "gh pr merge 7" "missing: python3" "no python3 refuses a merge" "" "$WITHOUT"
case_is "ls -la" PERMIT "no python3 still permits what the gate does not guard" "" "$WITHOUT"
without gh
case_is "gh pr merge 7" "missing: gh" "no gh refuses a merge" "" "$WITHOUT"
case_is "gh pr ready 7" "missing: gh" "no gh refuses marking a pull request ready" "" "$WITHOUT"

# Not a --force push: that one is refused by reading the command line alone and
# never needs git. This is the arm that has to ASK git which branch the push
# lands on, so a missing git leaves it unable to tell -- and it must refuse.
without git
case_is "git push origin batch/x" "missing: git" \
  "no git refuses a push, rather than permitting every command" "" "$WITHOUT"

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
checks_are '[{"name": "Backend", "state": "FAILURE"},
             {"name": "Frontend", "state": "FAILURE"}]'
case_is "gh pr ready 7"                          "is not green" "a pull request with failing checks is refused"
checks_are '[{"name": "Backend", "state": "SUCCESS"}]'
case_is "gh pr ready 7"                          PERMIT "a green pull request may be marked ready"

# WHICH checks that question counts. Marking a batch ready is what INVITES the
# review, so the check that stays red until the review has happened cannot be
# part of it -- the stop hook reads past the same name for the same reason, and
# two greenness readers in one file that disagreed was the defect. Both ask
# review_cycle.settled now, so these cases pin the shared answer.
checks_are "[{\"name\": \"$REVIEW_CHECK\", \"state\": \"FAILURE\"},
             {\"name\": \"Backend\", \"state\": \"SUCCESS\"}]"
case_is "gh pr ready 7"                          PERMIT \
  "a red review-cycle check does not hold a batch back from the review that would fix it"

checks_are "[{\"name\": \"$REVIEW_CHECK\", \"state\": \"FAILURE\"},
             {\"name\": \"Backend\", \"state\": \"FAILURE\"}]"
case_is "gh pr ready 7"                          "is not green" \
  "and reading past it does not drop the question for every other check"

# THE STATE THE TWO READERS DISAGREED ABOUT while they were two. A neutral check
# was green to the stop hook and red here, and nothing failed when it was.
checks_are '[{"name": "Backend", "state": "NEUTRAL"}]'
case_is "gh pr ready 7"                          PERMIT \
  "a neutral check is green to this arm, as it always was to the stop hook"

checks_are '[{"name": "Backend", "state": "SUCCESS"}]'

# ---------------------------------------------------------------------------
# record, and the evidence it stores
# ---------------------------------------------------------------------------

RECEIPTS="$REPO/.claude/.review-receipts.json"

record() { in_repo "" "" record "${1:-7}"; }

pr_json() { # <code-review evidence?> <simplify evidence?>
  # THE TWO SHAPES A REVIEW PASS REALLY LEAVES, which `evidence` in
  # scripts/review_cycle.py argues; the empty-bodied review that used to stand
  # for one is neither. inventory-tng-bahi.
  #
  #   review   a review submitted with prose in it
  #   finding  an inline comment opening a thread, which is what
  #            `/code-review --comment` actually leaves
  echo '[]' >"$FIX/findings.json"
  [[ "$1" == "finding" ]] && findings_are "$a_finding"
  # The head goes in the same document, because `record` now reads all three
  # fields from one answer rather than asking twice.
  CR=$1 SIMP=$2 HEAD_NOW="$(cat "$FIX/head")" python3 -c '
import json, os
comments, reviews = [], []
if os.environ["CR"] == "review":
    reviews.append({"id": "R_1", "author": {"login": "someone"},
                    "submittedAt": "2026-08-24T10:00:00Z",
                    "commit": {"oid": "b" * 40}, "state": "COMMENTED",
                    "body": "Two things below, and the rest looks sound."})
elif os.environ["CR"] == "quoted":
    comments.append({"id": "C_3", "author": {"login": "someone"},
                     "createdAt": "2026-08-24T10:00:00Z", "url": "http://x/3",
                     "body": "my comment carried no `<!-- review-cycle: code-review -->` marker, "
                             "which is why nothing recorded"})
elif os.environ["CR"] == "fenced":
    comments.append({"id": "C_4", "author": {"login": "someone"},
                     "createdAt": "2026-08-24T10:00:00Z", "url": "http://x/4",
                     "body": "post the findings with:\n\n    <!-- review-cycle: code-review -->\n\nand it records"})
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

# WHAT TO DO ABOUT IT DIFFERS BY STAGE. The refusal offered one cure for both
# until inventory-tng-d854, and `record_receipt` carries the argument for why
# that was the wrong one. What is pinned here is the pair either half of the
# fix could quietly undo: the review stage naming the command, and never the
# marker.
assert "$out" "$status" 1 "/code-review 7 --comment" \
  "a missing review pass says to ask a person, with the number filled in"
refute "$out" "$status" 1 "review-cycle: code-review" \
  "and never offers the marker for the stage that submits its own evidence"

pr_json review none
out=$(record); status=$?
assert "$out" "$status" 1 "simplify pass ran" "a review pass alone is not the whole cycle"
refute "$out" "$status" 1 "code-review pass ran" "and the stage that did run is not complained about"
assert "$out" "$status" 1 "review-cycle: simplify" \
  "a missing simplify pass does still name the marker, which is how it is evidenced"

# HALF A CYCLE IS STILL WORTH WRITING DOWN -- inventory-tng-gfkr. It used to
# exit before the write, so nothing on disk knew the review had happened, and
# the Stop hook -- which reads the receipt and never the pull request -- went
# on naming every stage. `record_receipt` carries the argument; what is pinned
# here is that the write happens at all.
assert "$(cat "$RECEIPTS")" 0 0 "R_1" \
  "the stage that did run is kept, so the stop hook can tell what is outstanding"
refute "$out" "$status" 1 "Merging is unblocked" \
  "and keeping it is not the same as recording the cycle"
assert "$out" "$status" 1 "does not unblock the merge" "which the refusal still says in as many words"
assert "$out" "$status" 1 "Kept what was found" "and says that something was kept, which is the new half"

# THE HALF THAT MUST NOT MOVE. A partial receipt is a truthful nudge and never
# a licence: the merge guard reads the same file and is all or nothing.
case_is "gh pr merge 7 --rebase" "No review cycle" \
  "a half-written receipt does not unblock the merge"

# AND `status` MUST NOT READ IT AS WHOLE, which is the shape it got wrong for
# most of the receipts on a real machine. The reader carries why.
out=$(in_repo "" "" status); status=$?
assert "$out" "$status" 0 "nothing found" "status names the stage that has nothing behind it"
assert "$out" "$status" 0 "review by someone" "and still names the evidence it does have"

# THE SHAPES A RECEIPTS FILE CAN TAKE WITHOUT RAISING, which the `status` arm
# argues about. Both used to reach `.items()` on something that has none.
printf '[]' >"$RECEIPTS"
out=$(in_repo "" "" status); status=$?
assert "$out" "$status" 0 "No receipts" "a receipts file that is a list is read as empty, not a traceback"
refute "$out" "$status" 0 "Traceback" "and nothing is raised at the person asking"

printf '{"7": "corrupted"}' >"$RECEIPTS"
out=$(in_repo "" "" status); status=$?
assert "$out" "$status" 0 "nothing found" "a receipt that is not an object is reported as holding nothing"
refute "$out" "$status" 0 "Traceback" "rather than raised at them"

# THE ONE ANSWER THIS MUST NEVER GIVE BY ACCIDENT: an empty stage list, which
# means nothing is missing. `receipt_status` carries what that costs.
echo "an empty stage list refuses rather than reporting a clean cycle"
# A COPY WITH THE STAGES EMPTIED, not an environment variable. The gate reads
# the pair from scripts/review_cycle.py -- which the required check reads too,
# so the two cannot disagree about what a complete cycle is -- and an inherited
# value steers neither. A test written that way would pass whatever the reader
# did, which is what the first version of this case did.
#
# BOTH files are copied, because the gate imports its sibling by directory: a
# copy of the gate alone would fail on the import and pass this case for the
# wrong reason.
BROKEN="$WORK/broken"
mkdir -p "$BROKEN"
cp "$GATE" "$BROKEN/landing-gate.sh"
sed 's/^STAGES = ("code-review", "simplify")$/STAGES = ()/' \
  "$HERE_SCRIPTS/review_cycle.py" >"$BROKEN/review_cycle.py"
grep -q '^STAGES = ()$' "$BROKEN/review_cycle.py" \
  || fail_case "the fixture did not empty the stages, so the case below proves nothing"
printf '{}' >"$RECEIPTS"
out=$( (cd "$REPO" && env PATH="$(full_path)" GH_FIXTURES="$FIX" \
  CLAUDE_PROJECT_DIR="$REPO" "$BROKEN/landing-gate.sh" status) 2>&1 ); status=$?
# Non-zero as well as the message, and the two are a pair rather than belt and
# braces: `status` ended in a flat `exit 0`, so the reader could die, print its
# complaint, and still report success. This case is what noticed.
assert "$out" "$status" 1 "names no stages" "an emptied stage list says so and exits non-zero"
refute "$out" "$status" 1 "nothing found" "rather than reporting on no stages at all"
# SAID, NOT RAISED. review_cycle refuses to import at all on an empty stage
# list, and it raises SystemExit to do it -- so the sentence reaches whoever ran
# this with no caller catching anything, which is why no guard stands here and
# no `except` stands at the five places that import it. An AssertionError shown
# to somebody is a reader dying on them instead of telling them, which is the
# shape the rest of this file is about.
refute "$out" "$status" 1 "Traceback" "and it is said rather than raised at them"
rm -f "$RECEIPTS"

pr_json review marker
out=$(record); status=$?
assert "$out" "$status" 0 "Merging is unblocked" "both stages evidenced records the cycle"
assert "$(cat "$RECEIPTS")" 0 0 '"head"' "the receipt names the head"
assert "$(cat "$RECEIPTS")" 0 0 "R_1" "the receipt names the review it found"
assert "$(cat "$RECEIPTS")" 0 0 "C_2" "the receipt names the comment it found"
refute "$(cat "$RECEIPTS")" 0 0 '"stages"' "and stores no hardcoded stage list"

# THE OTHER SHAPE, and the one `/code-review --comment` actually leaves: an
# inline comment opening a thread. Held separately because a bodied review and
# an inline finding reach the stage by different halves of the rule.
#
# AFTER the assertions above rather than between them: `record` rewrites the
# receipts file, so a case wedged into that group left them reading a receipt
# written by a different scenario than the one they name.
pr_json finding marker
record >/dev/null 2>&1
assert "$(cat "$RECEIPTS")" 0 0 "finding" "and an inline finding is evidence in its own right"

# AND THE HAZARD, which is the whole of inventory-tng-bahi: answering findings
# is what the documented procedure tells an agent to do, and doing it must not
# be mistaken for the pass that made them.
#
# `findings_are` AFTER `pr_json`, which rewrites the same file: written the
# other way round, the reply never reached the reader and this case pinned an
# empty pull request instead of the hazard it names.
pr_json none marker
findings_are "$a_reply"
out=$(record 2>&1); status=$?
assert "$out" "$status" 1 "code-review" "a reply to a finding is not a finding"

pr_json marker marker
out=$(record); status=$?
assert "$out" "$status" 0 "Merging is unblocked" "a marker comment is evidence for code-review too"

# QUOTING THE MARKER IS NOT POSTING IT -- inventory-tng-egh4.1. A substring
# test over the body made a comment that merely mentioned the marker into
# evidence that the pass had run. It is not hypothetical: pull request 48
# merged with no code-review marker, and the correction comment posted
# afterwards -- the one reporting the receipt was missing -- quoted the marker
# while explaining the omission and became the evidence. A comment reporting an
# absence created the thing it reported absent.
pr_json quoted marker
out=$(record); status=$?
assert "$out" "$status" 1 "code-review pass ran" "a marker quoted inside a sentence is not evidence"

pr_json fenced marker
out=$(record); status=$?
assert "$out" "$status" 1 "code-review pass ran" "nor one quoted in a fenced block explaining it"

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
assert "$out" 0 0 "could not find out" "a gh that answers nothing refuses, rather than skipping what it could not ask"
# The FIRST question the merge arm asks is the body, so that is the one a gh
# answering nothing at all leaves unanswered.
assert "$out" 0 0 "says do not merge" "and it says which question went unanswered"
GH_FAILS=

# THE HEAD COMPARISON'S OWN UNAVAILABILITY, which a gh that fails everything can
# no longer reach: it is refused at the body first. So this one answers the body
# and refuses only the head, which is the shape inventory-tng-3sp was -- the
# comparison SKIPPED because gh could not answer, letting a receipt from a
# superseded head permit the merge.
GH_FAILS_HEAD=1
out=$(decision "$(gate "gh pr merge 7 --rebase")")
assert "$out" 0 0 "to check it is what was reviewed" "a gh that cannot name the current head refuses, rather than skipping the comparison"
unset GH_FAILS_HEAD

echo
echo "a pull request that says do not merge"
# What this asks, and why it asks it here when a required check already does,
# is on the merge arm. inventory-tng-g4dh.
body_is "A spike, kept for the meeting.

<!-- do-not-merge -->"
case_is "gh pr merge 7 --rebase"                 "posts the do-not-merge marker" "a marked pull request is refused at the merge"
case_is "gh pr merge 7 --rebase"                 "opened to be read" "and is told not to try making the check green"

# Before the receipt, which is the ordering the merge arm explains. The file is
# REMOVED rather than rewritten: putting a valid receipt back is the state every
# case around this one is already in, so the case would have asserted nothing
# about the ordering it names.
rm -f "$RECEIPTS"
case_is "gh pr merge 7 --rebase"                 "posts the do-not-merge marker" "even with no receipt, the marker is the answer"
printf '%s' "$RECEIPTS_OLD" >"$RECEIPTS"

# A READER THAT COULD NOT READ IS NOT A PULL REQUEST THAT OBJECTED. Any
# non-zero used to be reported as the marker being posted, so a gh answering
# something unparseable -- or a review_cycle.py gone from beside the reader --
# accused a body by name of carrying a line it does not have.
printf 'not json' >"$FIX/body.json"
case_is "gh pr merge 7 --rebase"                 "could not read whether" "a reader that cannot answer refuses as itself, not as the marker"
body_is "An ordinary batch."

echo
echo "and the same pull request offered for review"
# `gh pr ready` refused it before this, but with "wait for the checks, or fix
# them" -- which points an agent at making the do-not-merge check green, the one
# check that must never be. Told apart by name rather than by counting.
checks_are '[{"name": "Backend", "state": "SUCCESS"},
             {"name": "Not marked do-not-merge", "state": "FAILURE"}]'
case_is "gh pr ready 7"                          "posts the do-not-merge marker" "a marked pull request is not told to go and fix it"
refute "$(decision "$(gate "gh pr ready 7")")" 0 0 "Wait for the checks" \
  "and is not sent to make the one check that must stay red go green"

# An ordinary red check still says what it always said.
checks_are '[{"name": "Backend", "state": "FAILURE"}]'
case_is "gh pr ready 7"                          "Wait for the checks" "an ordinary failing check still is"
checks_are '[{"name": "Backend", "state": "SUCCESS"}]'
refute "$(decision "$(gate "gh pr merge 7 --rebase")")" 0 0 "posts the do-not-merge marker" \
  "and does not say the body posts a marker nobody read"

body_is "Writing about the marker is free: I removed the \`<!-- do-not-merge -->\` line."
refute "$(decision "$(gate "gh pr merge 7 --rebase")")" 0 0 "posts the do-not-merge marker" \
  "and a body that only mentions it is not refused for it"
body_is "An ordinary batch."
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

# ---------------------------------------------------------------------------
# the gate's own source, mid-conflict
# ---------------------------------------------------------------------------
#
# inventory-tng-ghqk, and the one place this file fails OPEN rather than
# closed. `own_source_is_mid_conflict` argues why; these cases pin that it
# takes BOTH conditions, because either alone would switch the guard off far
# too easily.
echo
echo "a gate whose own source is mid-conflict"

# gate_copy <path> <text> -- the real gate, with <text> dropped into its python
# half. Two cases below want one, differing only in what breaks it, and the
# assertion that the matcher still starts where this expects belongs to both:
# written twice it is one edit away from a fixture that silently stops being a
# copy of anything.
gate_copy() {
  python3 - "$GATE" "$1" "$2" <<'PY'
import sys
src = open(sys.argv[1]).read()
needle = "import json, re, sys\n"
assert needle in src, "the matcher's first line moved; these fixtures need updating"
open(sys.argv[2], "w").write(src.replace(needle, needle + sys.argv[3], 1))
PY
  chmod +x "$1"
}

# Markers in the PYTHON half. Bash still parses the file -- they arrive inside a
# single-quoted string -- so the script runs and the matcher is what fails,
# which is the shape that was hit. Written with `$'...'` so this suite does not
# carry conflict markers of its own at the start of a line.
CONFLICTED="$WORK/repo/conflicted-gate.sh"
gate_copy "$CONFLICTED" $'<<<<<<< HEAD\n=======\n>>>>>>> other\n'

# The verdict and the explanation go to different streams, and here they must
# be read apart: a broken matcher puts python's own SyntaxError on stderr, and
# a `2>&1` that mixed it into stdout would leave nothing that parses as JSON.
run_copy() {
  payload "$2" | (cd "$REPO" && env PATH="$(full_path)" GH_FIXTURES="$FIX" \
    CLAUDE_PROJECT_DIR="$REPO" "$1" check)
}
run_conflicted() { run_copy "$CONFLICTED" "$1"; }
conflicted() { printf '%s' "$(run_conflicted "$1" 2>/dev/null)"; }
conflicted_said() { run_conflicted "$1" 2>&1 >/dev/null; }

# A second copy whose matcher fails for a reason that is not a rebase: it
# carries no markers anywhere. This is the condition the cases below could not
# see -- swap the marker test in `own_source_is_mid_conflict` for `return 0` and
# every one of them stayed green, because each either had markers or had no
# operation in flight.
UNMARKED="$WORK/repo/unmarked-gate.sh"
gate_copy "$UNMARKED" $'raise SystemExit(2)\n'

# Markers, but nothing in flight: still a refusal. A stray `<<<<<<<` in a
# comment must not be enough to disarm the gate.
out=$(decision "$(conflicted "gh pr merge 7")")
assert "$out" 0 0 "could not find out" "markers alone do not stand the gate down"

# Now a rebase actually stopped here. `--git-path` answers relative to the cwd
# it is asked from, so it is asked from inside the repository.
(cd "$REPO" && : > "$(git rev-parse --git-path MERGE_HEAD)")
out=$(decision "$(conflicted "gh pr merge 7")")
assert "$out" 0 0 "PERMIT" "mid-conflict and mid-operation, the gate stands down"
out=$(conflicted_said "gh pr merge 7")
assert "$out" 0 0 "standing down" "and says so, rather than going quiet"

# The trap `own_source_is_mid_conflict` warns about, pinned: REBASE_HEAD is a
# leftover rather than a signal, and the first draft of that function listed it
# among the in-flight ones, which quietly cost the guard its second condition.
(cd "$REPO" && rm -f "$(git rev-parse --git-path MERGE_HEAD)" \
  && : > "$(git rev-parse --git-path REBASE_HEAD)")
out=$(decision "$(conflicted "gh pr merge 7")")
assert "$out" 0 0 "could not find out" "a leftover REBASE_HEAD is not an operation in flight"
(cd "$REPO" && rm -f "$(git rev-parse --git-path REBASE_HEAD)")
(cd "$REPO" && : > "$(git rev-parse --git-path MERGE_HEAD)")

# The other half of "both, never either alone", and the one nothing held: an
# operation in flight, a matcher that failed, and a source with nothing wrong
# with it. A rebase somewhere else in the tree is not a reason to believe a
# refusal came from this file.
out=$(decision "$(run_copy "$UNMARKED" "gh pr merge 7" 2>/dev/null)")
assert "$out" 0 0 "could not find out" "a matcher failing with no markers here still refuses"

# The unbroken gate, mid-operation, guards exactly as it did: an operation in
# flight is not on its own a reason to stop.
case_is "gh pr merge 7 --rebase" "No review cycle" "an intact gate mid-rebase still guards"
(cd "$REPO" && rm -f "$(git rev-parse --git-path MERGE_HEAD)")

# ---------------------------------------------------------------------------
# the finished question, asked at the merge
# ---------------------------------------------------------------------------

# Point the stub at a head and give it both markers, which is what `record`
# needs before there is a receipt to merge against.
head_is() {
  echo "$1" >"$FIX/head"
  pr_json marker marker
}

# A branch still carrying a fixup is not ready. This is the ONLY place that is
# asked now -- CI deliberately stays quiet about it, which is inventory-tng-et6o
# -- so if this case goes, nothing anywhere refuses an uncollapsed merge.
(cd "$REPO" && git commit -q --allow-empty -m 'fixup! start: the commit a branch forks from')
head_is "$(git -C "$REPO" rev-parse HEAD)"
record >/dev/null
case_is "gh pr merge 7 --rebase" "not finished" \
  "a branch with a commit waiting to be folded in is not merged"

(cd "$REPO" && git reset -q --hard HEAD~1)
head_is "$HEAD_OID"
record >/dev/null
case_is "gh pr merge 7 --rebase" PERMIT "a collapsed branch with a receipt merges"

# THE HOLE THE REVIEW FOUND. `gh pr merge <n>` will merge a pull request that is
# not what is checked out, and a tidy branch elsewhere has nothing waiting to be
# folded in -- so the checker would have passed and permitted a merge it never
# looked at. Simulated by recording against the head, then moving the checkout
# off it, which is the same disagreement from the other side.
record >/dev/null
(cd "$REPO" && git commit -q --allow-empty -m 'start: a commit the pull request does not have')
case_is "gh pr merge 7 --rebase" "is not what is checked out" \
  "a pull request that is not the branch in hand is refused rather than judged elsewhere"
(cd "$REPO" && git reset -q --hard "$HEAD_OID")

# Put the receipts back as they were found. The stop-mode cases below are about
# a batch with NO recorded cycle, and a receipt left here would satisfy them.
in_repo "" "" clear >/dev/null

# ---------------------------------------------------------------------------
# stop mode
# ---------------------------------------------------------------------------

# The Stop hook's payload, and the gate's answer to it. Sets STOP_OUT and
# STOP_STATUS, because every case here asserts on both and a helper that
# printed only the output would leave each one capturing the status by hand.
stopped() {
  local body=${1:-'{"stop_hook_active":false}'} repo=${2:-$REPO} path=${3:-$(full_path)}
  STOP_OUT=$(printf '%s' "$body" | (cd "$repo" && env PATH="$path" GH_FIXTURES="$FIX" \
    CLAUDE_PROJECT_DIR="$repo" GH_DEADLINE=5 ${GH_FAILS:+GH_FAILS=1} "$GATE" stop) 2>&1)
  STOP_STATUS=$?
}

forget_nudges() { rm -f "$REPO/.claude/.stop-nudges.json"; }

# The case this hook exists for: a batch that is ready, green and unreviewed.
forget_nudges
stopped
assert "$STOP_OUT" "$STOP_STATUS" 0 '"decision":"block"' \
  "a ready, green, unreviewed batch is not allowed to end the turn"
# WHOSE EACH STAGE IS, which is inventory-tng-d854 and the reason the two are
# no longer printed as one interchangeable list. An agent reading this has to
# be able to tell the half it must ask for from the half it should already have
# run, without going to read DEVELOPERS.md first.
#
# Whole lines, so the layout is held too. Asserting the stage names alone said
# nothing the two below do not already say -- `assert` is a substring test, so
# neither could fail while these pass -- and left the prefix, the label column
# and the continuation indent untested, which is the half that actually broke
# while this was being written.
assert "$STOP_OUT" "$STOP_STATUS" 0 "  missing: code-review  ask a person to run: /code-review 7 --comment" \
  "the review stage says to ask a person, and carries the pull request number"
assert "$STOP_OUT" "$STOP_STATUS" 0 "           simplify     yours to run: /simplify" \
  "the simplify stage says it is the agent's own, lined up under the first"

# ONCE PER HEAD. The refusal above has been spent; a second attempt is allowed
# through, so that meaning it costs one exchange rather than the session.
stopped
refute "$STOP_OUT" "$STOP_STATUS" 0 "decision" "the same head is not refused twice"

# A new head is a new question, because what was reviewed is no longer there.
stop_scene OPEN false SUCCESS bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
stopped
assert "$STOP_OUT" "$STOP_STATUS" 0 '"decision":"block"' \
  "a head nobody has been asked about is refused again"
stop_scene
forget_nudges

# The harness says it is already continuing because a Stop hook blocked it.
# Honouring that is what keeps a refusal from being a loop.
stopped '{"stop_hook_active":true}'
refute "$STOP_OUT" "$STOP_STATUS" 0 "decision" \
  "a stop the hook itself caused is not refused again"

# Work in progress. Stopping in the middle of a draft is ordinary.
stop_scene OPEN true
stopped
refute "$STOP_OUT" "$STOP_STATUS" 0 "decision" "a draft is not refused"
stop_scene
forget_nudges

# Nothing to review yet.
stop_scene OPEN false FAILURE
stopped
refute "$STOP_OUT" "$STOP_STATUS" 0 "decision" "a batch whose checks are red is not refused"
stop_scene
forget_nudges

# EXCEPT THE ONE CHECK THAT IS ABOUT THIS. The review-cycle check is red
# PRECISELY WHILE the cycle has not run, so counting it among the checks that
# have to be green silenced this nudge for the only state it is registered to
# catch.
STOP_CHECK_NAME=$REVIEW_CHECK stop_scene OPEN false FAILURE
stopped
assert "$STOP_OUT" "$STOP_STATUS" 0 '"decision":"block"' \
  "a red review-cycle check does not silence the nudge about the review cycle"
stop_scene
forget_nudges

stop_scene OPEN false none
stopped
refute "$STOP_OUT" "$STOP_STATUS" 0 "decision" \
  "a batch whose checks have not reported is not refused"
stop_scene
forget_nudges

# Not a batch at all.
stopped '{"stop_hook_active":false}' "$ON_MAIN"
refute "$STOP_OUT" "$STOP_STATUS" 0 "decision" "a branch that is not a batch is not refused"

# A recorded cycle against this head is the whole point, and ends the turn.
mkdir -p "$REPO/.claude"
# A receipt for one head, evidencing the stages named -- both when none are.
#
# It took a head and nothing else until the half-cycle case below needed one
# stage evidenced and the other not, and wrote its own copy of this heredoc
# rather than saying so. The shape of a receipt is then in two places in one
# file, and a receipt this file gets wrong reads as NO receipt rather than as a
# broken one -- so the case would go on blocking, go on passing, and assert
# nothing about the state it was written for.
receipt_for() {
  local head=$1
  shift
  RECEIPT_HEAD="$head" RECEIPT_STAGES="${*:-code-review simplify}" python3 -c '
import json, os
print(json.dumps({"7": {
    "head": os.environ["RECEIPT_HEAD"],
    "evidence": {s: [{"kind": "comment"}] for s in os.environ["RECEIPT_STAGES"].split()},
}}))
' >"$RECEIPTS"
}
receipt_for "$HEAD_OID"
forget_nudges
stopped
refute "$STOP_OUT" "$STOP_STATUS" 0 "decision" "a batch that has been through its cycle ends the turn"

# A receipt for a head that has since been pushed over is not a receipt -- and
# it is the one state where NO stage is missing, so the stage list must not be
# printed for it. Both halves are asked of the one refusal rather than of two:
# the setup is identical, and a second `stopped` here bought two strings at the
# cost of a whole extra gate run.
receipt_for cccccccccccccccccccccccccccccccccccccccc
stopped
assert "$STOP_OUT" "$STOP_STATUS" 0 '"decision":"block"' \
  "a receipt from a superseded head does not end the turn"
assert "$STOP_OUT" "$STOP_STATUS" 0 "recorded against an earlier head" \
  "and is described as stale rather than as a missing stage"
refute "$STOP_OUT" "$STOP_STATUS" 0 "ask a person" \
  "so nobody is sent to fetch a person over a cycle that has already run"
rm -f "$RECEIPTS"
forget_nudges

# HALF A CYCLE, which is the shape inventory-tng-d854 is actually about: the
# agent has run its own pass and only the person's is outstanding. The message
# must then carry that one stage and NOT the other, because a list naming both
# is what sent a whole finished batch back.
receipt_for "$HEAD_OID" simplify
stopped
assert "$STOP_OUT" "$STOP_STATUS" 0 "ask a person to run: /code-review 7 --comment" \
  "with only the review pass outstanding, that is what the refusal asks for"
refute "$STOP_OUT" "$STOP_STATUS" 0 "yours to run" \
  "and it does not ask again for the pass that has already run"
forget_nudges

# BOTH AT ONCE, which inventory-tng-gfkr made reachable: a partial receipt is
# now written, so it can also be left behind by a push. Reporting one of the
# two would send somebody to do half the work and stop -- run the missing pass
# and never record, or record and never run it.
receipt_for cccccccccccccccccccccccccccccccccccccccc simplify
stopped
# Whole lines on both, because the stale label is padded to `missing_block`'"'"'s
# column by hand and they are adjacent for the first time -- so a change to
# that column would show as a ragged block in the one message this issue
# exists to make readable, and nothing would catch it.
assert "$STOP_OUT" "$STOP_STATUS" 0 "  stale:   the cycle was recorded against an earlier head; record it again" \
  "a stale partial receipt says the head moved"
assert "$STOP_OUT" "$STOP_STATUS" 0 "  missing: code-review  ask a person to run: /code-review 7 --comment" \
  "and still names the stage that has nothing behind it, lined up under it"
rm -f "$RECEIPTS"
forget_nudges

# THE DIRECTION THIS MODE FAILS IN, and it is the opposite of every other case
# in this file. A missing program or an unreachable gh must let the turn END:
# failing closed here leaves a session that cannot stop and cannot fix what
# would release it, because fixing it also ends in stopping.
for missing in python3 gh; do
  without "$missing"
  stopped '{"stop_hook_active":false}' "$REPO" "$WITHOUT"
  refute "$STOP_OUT" "$STOP_STATUS" 0 "decision" \
    "a missing $missing lets the turn end rather than trapping the session"
  assert "$STOP_OUT" "$STOP_STATUS" 0 "not checking the review cycle" \
    "a missing $missing says on stderr that it did not look"
  forget_nudges
done

GH_FAILS=1 stopped
refute "$STOP_OUT" "$STOP_STATUS" 0 "decision" "a gh that cannot answer lets the turn end"
unset GH_FAILS
forget_nudges

# gh that answers with something nobody can parse. The turn still ends -- but
# it has to SAY so, which is the half the first version of this could not do:
# the refusal hung off `read`, and a herestring made from empty output still
# returns 0, so it never fired and the mode failed open in silence.
printf 'not json' >"$FIX/stop.json"
stopped
refute "$STOP_OUT" "$STOP_STATUS" 0 "decision" "an answer nobody can parse lets the turn end"
assert "$STOP_OUT" "$STOP_STATUS" 0 "could not read what gh said" \
  "and says so, rather than failing open in silence"
stop_scene
forget_nudges

# ---------------------------------------------------------------------------
# the registration itself
# ---------------------------------------------------------------------------

# A gate nobody has registered guards nothing, and the failure is silent: every
# case above passes against a settings.json that stopped mentioning this file.
# That is the same reason CI runs the commit hook this repository ships rather
# than only its suite -- what a clone is handed is the thing being tested.
#
# The real file, not a fixture, because a fixture would be a second copy of the
# arrangement and could agree with itself while the shipped one had drifted.
SHIPPED_SETTINGS=$(readlink -f "$(dirname "$GATE")/../.claude/settings.json")
registered=$(SETTINGS="$SHIPPED_SETTINGS" python3 -c '
import json, os
try:
    with open(os.environ["SETTINGS"]) as fh:
        hooks = json.load(fh).get("hooks") or {}
except (OSError, ValueError) as exc:
    raise SystemExit("unreadable: %s" % exc)
for event in ("PreToolUse", "Stop"):
    commands = [inner.get("command", "")
                for entry in hooks.get(event) or []
                for inner in entry.get("hooks") or []]
    print(event, "yes" if any("landing-gate.sh" in c for c in commands) else "no",
          " ".join(c.rsplit("landing-gate.sh", 1)[-1].strip() for c in commands
                   if "landing-gate.sh" in c))
' 2>&1)
assert "$registered" 0 0 "PreToolUse yes check" \
  "the shipped settings register the gate on every Bash call"
assert "$registered" 0 0 "Stop yes stop" \
  "the shipped settings register the gate on the end of a turn"

verdict
