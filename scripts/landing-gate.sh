#!/usr/bin/env bash
# The landing gate.
#
# Guards the one step GitHub cannot see: branch protection knows whether the
# checks are green and the branch current, and cannot know whether anybody
# reviewed it.
#
# Which commands it refuses, what it needs installed, how the receipt is
# recorded and what it does not claim to be are all DEVELOPERS.md "When a
# branch is ready to merge". None of that is repeated here. What follows is
# only what a reader of this file needs and that document does not carry.
#
# WHY IT LIVES HERE. It was a local, gitignored file for its first month, while
# AGENTS.md came to let an agent merge a batch without asking and to name this
# gate as what makes that safe. A mechanism nobody but its author has is not
# something a rule may rest on -- a fresh clone, a fork, a CI runner and every
# worktree got the permission and none of the gate. So it moved in beside the
# other checkers, with a suite CI runs like theirs: inventory-tng-3sp.
#
# WHAT MOVED WITH IT is the failure direction, which is the half worth reading
# the code for. `deny_dependency` is where that is argued.

set -uo pipefail

# Where the receipts live.
#
# This used to be `git rev-parse --show-toplevel || exit 0`, which was a fourth
# fail-open beside the three this file was rewritten to remove, and the review
# of PR #30 found it by simply taking git off the PATH: exit 0 with nothing
# printed is what the harness reads as PERMITTED, so a session with no git
# could merge anything. It also made the gate depend on the working directory
# of whatever ran the command rather than on the project it guards.
#
# $CLAUDE_PROJECT_DIR is set by the harness that registers this hook and is the
# right answer when it is there. git is the fallback for a person running the
# script by hand. Failing to resolve either is not decided here -- `check` mode
# refuses the commands it guards, and the subcommands below say so themselves --
# because refusing at this line would refuse every command in the session.
REPO_ROOT=${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}
RECEIPTS="$REPO_ROOT/.claude/.review-receipts.json"

# The markers the review cycle leaves on a pull request, and the only thing
# `record` will accept as evidence that a stage ran. Named here rather than
# typed into the reader below and the documentation separately.
#
# The pattern is this repository's own: the batch-contents comment CI posts is
# found by `<!-- batch-contents -->` for exactly this reason -- a marker is
# stable in a way that prose is not, and a comment carrying one is a durable
# public artifact somebody can go and read afterwards.
MARK_REVIEW='<!-- review-cycle: code-review -->'
MARK_SIMPLIFY='<!-- review-cycle: simplify -->'

# Where the cycle is written down, rather than the cycle.
#
# This held its own copy of all five steps until the review of PR #30 pointed
# out that the file's own header says "None of that is repeated here" four
# lines above it, that AGENTS.md rule 1 forbids the second copy outright, and
# that the copy had ALREADY drifted -- "collapse each issue own commits", where
# AGENTS.md says "collapsing an issue's own commits". check-docs.sh missed it
# because the run is under twelve words, which is the rule failing exactly
# where it was meant to bite.
CYCLE='The cycle is DEVELOPERS.md "One review pass, findings filed per issue",
and .agents/skills/pull-requests/SKILL.md is the procedure for running it. It
ends with:

  scripts/landing-gate.sh record <pr>

as its own command, after the last push. It records the head it sees, so
anything published afterwards invalidates it.'

# ---------------------------------------------------------------------------
# Saying no
# ---------------------------------------------------------------------------

# JSON string escaping, so that `deny` needs nothing but bash.
#
# This is not general: it covers the five characters that can appear in the
# refusals written in this file, which are all literals here rather than
# anything a caller supplies. It exists so the refusal path has NO dependency
# at all -- see deny_dependency.
json_escape() {
  local s=$1
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\n'/\\n}
  s=${s//$'\r'/\\r}
  s=${s//$'\t'/\\t}
  printf '%s' "$s"
}

deny() {
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' \
    "$(json_escape "$1")"
  exit 0
}

# The rewrite this file exists for.
#
# The old version ran under `set -uo pipefail` with no -e and checked nothing
# it called. A missing jq made `deny` itself print nothing and exit 0, which
# the harness reads as "permitted"; a missing python3 sent the matcher's error
# to /dev/null and produced an empty verdict, read as "nothing to guard"; a gh
# that was rate-limited or unauthenticated returned an empty head, and the
# comparison against the receipt was then SKIPPED rather than failed. Three
# separate ways to stop working, all of them silent, and all of them permitting
# the merge -- which is the one direction a guard must never fail in, because
# nothing downstream notices that it has.
#
# So every dependency is checked at the point of use and every absence refuses.
# The refusal names the missing program, because "the gate is broken" is not
# something to work out from a merge that will not go through.
deny_dependency() {
  deny "The landing gate cannot check this, so it is refusing it.

  missing: $1
  needed:  $2

This refuses rather than permits deliberately. A guard that fails open is
worse than no guard, because the rule that rests on it goes on being believed
-- see AGENTS.md \"Git\" and DEVELOPERS.md \"When a branch is ready to merge\".

Install $1, or make it reachable from the PATH this session started with.
Nothing else in this repository is blocked: only the commands this gate
guards are, and only while it cannot see them."
}

# The other half of the same refusal, and it is a genuinely different case.
#
# `deny_dependency` is for a program that is not there. This is for one that is
# there and could not answer -- gh unauthenticated, rate-limited, offline, or
# asked about a pull request whose checks have not been reported yet, which is
# precisely the state somebody is in when they reach for `gh pr ready`. Both
# refuse, so the failure direction is the same; what differs is the advice, and
# the review of PR #30 caught this file telling people to "Install an answer
# from gh, or make it reachable from the PATH", which is not advice at all.
deny_unavailable() {
  deny "The landing gate could not find out, so it is refusing this.

  wanted:  $1
  from:    $2

That is not the same as $2 being missing -- it answered, and the answer was an
error. The usual causes are an expired or absent credential, a rate limit, no
network, or a pull request whose checks have not been reported yet.

  gh auth status

This refuses rather than permits deliberately: a guard that cannot see is not
a guard that has nothing to object to. See DEVELOPERS.md \"When a branch is
ready to merge\"."
}

have() { command -v "$1" >/dev/null 2>&1; }

# Is this gate's own source in the middle of being rewritten?
#
# THE ONE PLACE FAILING OPEN IS RIGHT -- and it buys less than the reason first
# given for it, which was that a half-written gate refuses every Bash command in
# the session, the git commands needed to finish the rebase included. That was
# asserted rather than measured, and measuring it split this file's two ways of
# breaking apart:
#
#   Markers in the SHELL half do block everything, because bash exits 2 on the
#   syntax error and 2 is the harness's own "block this call". Nothing in this
#   file runs then, so nothing in this file can help, and this function makes no
#   claim to. inventory-tng-ghqk says what would.
#
#   Markers in the inlined PYTHON, which is the half that reaches here, block
#   nothing of the sort. The file parses, the prefilter answers first, and every
#   command that does not say `gh` or `push` as a word -- rebase --continue,
#   --abort, add, status, mergetool -- was permitted throughout. Only a guarded
#   command ever reached the matcher and was refused.
#
# So what standing down actually buys is the guarded pair itself: the
# `git push --force-with-lease` that ends a collapse, and the merge this gate
# exists to hold. Thinner than "the session is stranded", and stated at its
# real size, because the cost is the same size -- a merge waved through while
# nothing can read it. Both are small because reaching here is: it takes an
# operation in flight AND markers in this very file.
#
# TWO CONDITIONS HERE, because either alone is too easily true. An operation
# that can halt half-way has to actually be in flight, AND this file has to be
# one of the ones carrying markers. Without the first, a stray `<<<<<<<` in a
# comment would switch the guard off; without the second, any conflicted rebase
# anywhere in the tree would. THREE for the stand-down, because this is asked
# only where the matcher has already failed: an intact gate mid-rebase guards
# exactly as it always did.
#
# NOT `REBASE_HEAD`, and this is the trap: git LEAVES IT BEHIND when a rebase
# finishes, so it is present in any repository that has ever rebased and says
# nothing about now. Listing it would make the first condition permanently true
# and quietly reduce this to "does my source contain markers", which is the
# single condition the paragraph above rejects. The rebase directories are the
# in-flight signal; MERGE_HEAD and the other two are removed on completion.
own_source_is_mid_conflict() {
  have git || return 1
  local self op
  self=$(readlink -f "${BASH_SOURCE[0]}") || return 1
  for op in rebase-merge rebase-apply MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD; do
    if [[ -e "$(git rev-parse --git-path "$op" 2>/dev/null)" ]]; then
      grep -qE '^(<{7}|={7}|>{7})([ \t]|$)' "$self"
      return $?
    fi
  done
  return 1
}

# ---------------------------------------------------------------------------
# Reading GitHub
# ---------------------------------------------------------------------------

# Every gh read goes through here, so that "gh could not answer" is one thing
# rather than five, and so that no caller can accidentally reintroduce the
# 2>/dev/null that made an unauthenticated gh look like a satisfied check.
# Prints what gh printed; returns non-zero if gh failed or said nothing.
#
# THE DEADLINE IS PART OF FAILING CLOSED, which is not obvious and was found by
# the review of PR #30. A PreToolUse hook that exceeds its own timeout is KILLED
# by the harness, and a killed hook prints no verdict -- which is permitted.
# So the slow path has to be refused by this script, in time to say so, rather
# than by the harness. GH_DEADLINE is comfortably inside the timeout registered
# in .claude/settings.json, and the two are meant to be read together.
#
# `timeout` is coreutils and is normally there; if it is not, the call runs
# unbounded rather than being refused, because a missing timeout is not a
# reason to refuse every guarded command and the harness timeout is still
# behind it.
GH_DEADLINE=${GH_DEADLINE:-8}

gh_json() {
  local out
  if have timeout; then
    out=$(timeout "$GH_DEADLINE" gh "$@" 2>/dev/null) || return 1
  else
    out=$(gh "$@" 2>/dev/null) || return 1
  fi
  [[ -n "$out" ]] || return 1
  printf '%s' "$out"
}

pr_head() { gh_json pr view "$1" --json headRefOid --jq .headRefOid; }

# The number of the pull request for the current branch, when a command did not
# name one.
pr_current() { gh_json pr view --json number --jq .number; }

# ---------------------------------------------------------------------------
# record / status / clear
# ---------------------------------------------------------------------------

# Gather the evidence for one pull request and write the receipt.
#
# The old version wrote stages: ["code-review", "simplify"] as a hardcoded
# literal, so a session that had run neither pass could record and merge -- and
# three pull requests were merged on its word before anybody noticed. The cycle
# genuinely had run on all three; the point is that the receipt could not have
# known, which makes it a note to self rather than a record.
#
# What it stores now is what it FOUND: the marker comments and the review
# submissions that are on the pull request, by id, author and timestamp, so the
# receipt points at artifacts anybody can go and read. It refuses to write one
# at all when a stage has nothing behind it.
#
# This is still a guardrail. Somebody determined to merge unreviewed work can
# post an empty comment carrying the marker, and the gate will believe it. What
# it can no longer do is believe a stage ran when the pull request carries
# nothing whatsoever -- which is the case it was actually failing in.
record_receipt() {
  local pr=$1 payload
  have python3 || { echo "landing-gate: python3 is needed to read the pull request." >&2; return 1; }
  have gh || { echo "landing-gate: gh is needed to read the pull request." >&2; return 1; }
  [[ -n "$REPO_ROOT" ]] || {
    echo "landing-gate: could not tell where this repository is." >&2
    echo "  Run it from inside the checkout, or set CLAUDE_PROJECT_DIR." >&2
    return 1
  }

  # One round trip, not two.
  #
  # The head and the evidence used to be fetched by separate `gh pr view` calls,
  # which is a wasted round trip and, worse, a race: a push landing between them
  # produced a receipt whose head was OLDER than the evidence gathered against
  # it -- a receipt that then permitted merging a head nobody had looked at.
  # Found by the review of PR #30.
  payload=$(gh_json pr view "$pr" --json headRefOid,comments,reviews) || {
    echo "Could not read pull request $pr. Is gh authenticated?" >&2
    return 1
  }

  # The directory, but NOT the file: an empty receipts file written before the
  # evidence is weighed is a receipt that exists for a cycle that did not run,
  # which is a smaller version of the bug this whole change is about. The
  # reader below creates it, and only once it has something to put in it.
  mkdir -p "$REPO_ROOT/.claude"

  # One reader, not two. Pulling `headRefOid` out here used to be a python3 of
  # its own, started to read one field from a document the reader below then
  # parsed again -- 22 ms and a second copy of the `--json` field list to keep in
  # step. The reader has the whole payload, so it says what it found.
  MARK_REVIEW="$MARK_REVIEW" MARK_SIMPLIFY="$MARK_SIMPLIFY" \
  RECEIPTS="$RECEIPTS" PR="$pr" \
    python3 -c '
import json, os, sys

payload = json.load(sys.stdin)
mark_review = os.environ["MARK_REVIEW"]
mark_simplify = os.environ["MARK_SIMPLIFY"]

# Before the evidence, because a receipt without a head vouches for nothing and
# there is no point telling anybody which stage is missing until there is a
# commit to pin the answer to.
head = payload.get("headRefOid") or ""
if not head:
    sys.exit("Could not read the head of pull request %s. Is gh authenticated?"
             % os.environ["PR"])

# Both are the first page gh returns rather than every one that exists, and
# that is left as it is on purpose: a marker this does not see is a stage that
# looks unevidenced, so truncation can only ever produce a REFUSAL to record.
# The failure direction is the safe one, and the way out is to post the
# findings comment again -- which is cheap, and which a paginating reader would
# be buying at the cost of a second API shape to keep working.
comments = payload.get("comments") or []
reviews = payload.get("reviews") or []


def marked(mark):
    return [
        {
            "kind": "comment",
            "id": c.get("id"),
            "author": (c.get("author") or {}).get("login"),
            "at": c.get("createdAt"),
            "url": c.get("url"),
        }
        for c in comments
        if mark in (c.get("body") or "")
    ]


# A review submitted through the API is evidence in its own right: it is what
# `/code-review --comment` leaves behind, and its body is not ours to mark.
submitted = [
    {
        "kind": "review",
        "id": r.get("id"),
        "author": (r.get("author") or {}).get("login"),
        "at": r.get("submittedAt"),
        "commit": (r.get("commit") or {}).get("oid"),
    }
    for r in reviews
]

evidence = {
    "code-review": marked(mark_review) + submitted,
    "simplify": marked(mark_simplify),
}

missing = [stage for stage, found in evidence.items() if not found]
if missing:
    for stage in missing:
        mark = mark_review if stage == "code-review" else mark_simplify
        print(
            "Nothing on pull request %s shows the %s pass ran." % (os.environ["PR"], stage),
            file=sys.stderr,
        )
        print("", file=sys.stderr)
        print("  Post its findings to the pull request with this line in the body:", file=sys.stderr)
        print("", file=sys.stderr)
        print("      %s" % mark, file=sys.stderr)
        print("", file=sys.stderr)
    print(
        "The receipt records what it finds, so a stage with nothing behind it\n"
        "cannot be recorded. See DEVELOPERS.md \"Pull requests\".",
        file=sys.stderr,
    )
    sys.exit(1)

path = os.environ["RECEIPTS"]
try:
    with open(path) as fh:
        receipts = json.load(fh)
except (OSError, ValueError):
    receipts = {}
if not isinstance(receipts, dict):
    receipts = {}

receipts[os.environ["PR"]] = {"head": head, "evidence": evidence}
with open(path, "w") as fh:
    json.dump(receipts, fh, indent=2, sort_keys=True)
    fh.write("\n")

for stage, found in sorted(evidence.items()):
    print("  %-12s %d on the pull request" % (stage, len(found)))
print()
print("Recorded the review cycle for pull request %s at %s. Merging is unblocked."
      % (os.environ["PR"], head))
' <<<"$payload" || return 1
}

# What the receipts say about one pull request: its head, and which stages have
# nothing behind them.
#
# One reader, because there were two -- `stop_mode` and the merge guard asked
# the same twelve lines of the same file for the same thing and differed only
# in the last of them. This file's own header carries the story of `CYCLE`
# being deduplicated for exactly this reason, and records that the copy had
# already drifted before anybody noticed.
#
# Prints "<head>|<missing,stages>", and the separator is `|` rather than a
# space for a reason worth keeping: either half may be empty, and `read` with
# its default IFS drops a leading blank field, so a receipt that does not exist
# put the missing-stage list into the head and told the merge guard the branch
# had moved. A non-whitespace separator preserves the empty field.
receipt_status() {
  RECEIPTS="$RECEIPTS" PR="$1" python3 -c '
import json, os
try:
    with open(os.environ["RECEIPTS"]) as fh:
        receipts = json.load(fh)
except (OSError, ValueError):
    receipts = {}
if not isinstance(receipts, dict):
    receipts = {}
receipt = receipts.get(os.environ["PR"])
if not isinstance(receipt, dict):
    receipt = {}
evidence = receipt.get("evidence")
if not isinstance(evidence, dict):
    evidence = {}
missing = [stage for stage in ("code-review", "simplify") if not evidence.get(stage)]
print("%s|%s" % (receipt.get("head") or "", ",".join(missing)))
'
}

# ---------------------------------------------------------------------------
# stop mode: invoked by the Stop hook when a session tries to end a turn
# ---------------------------------------------------------------------------

# Where a nudge is remembered, so that it is a nudge and not a wall.
NUDGES="$REPO_ROOT/.claude/.stop-nudges.json"

# THIS ONE FAILS OPEN, AND IT IS THE ONLY MODE THAT MAY.
#
# Everywhere else in this file a dependency that is missing or an answer that
# cannot be got REFUSES, because the thing being guarded is a merge and a guard
# that fails open lets unreviewed work onto main while the rule that rests on it
# goes on being believed. `deny_dependency` argues that at length.
#
# Here the asymmetry reverses, and it reverses completely. What this mode
# guards is the END OF A TURN. A missing python3, an unauthenticated gh, a rate
# limit or a network that is down would, failing closed, leave a session unable
# to stop -- and unable to fix the thing that would release it, because fixing
# it is work that also ends in trying to stop. The cost of failing open is a
# nudge not delivered; the cost of failing closed is a session that cannot be
# ended by anybody. So every uncertain answer below lets the turn end, and says
# on stderr that it could not look.
stop_open() {
  [[ -n "${1:-}" ]] && echo "landing-gate: not checking the review cycle -- $1" >&2
  exit 0
}

# Refusing to end the turn. The Stop hook's own shape, which is not the
# PreToolUse one `deny` writes.
stop_block() {
  printf '{"decision":"block","reason":"%s"}\n' "$(json_escape "$1")"
  exit 0
}

stop_mode() {
  local payload branch pr state draft head checks recorded
  IFS= read -r -d '' payload

  # THE BRANCH FIRST, and the order is the point rather than tidiness. This
  # runs at the end of EVERY turn, and most turns do not end on a batch. Asking
  # git one question and leaving is the common path, so nothing heavier is
  # required until there is genuinely something to check -- otherwise every
  # turn on `main` pays for a python3 that was never going to matter, and a
  # machine without one is told so on every turn about a check that would have
  # been skipped anyway. A warning that fires when nothing is wrong is a
  # warning people learn to skip past.
  have git || stop_open "git is not on the PATH"

  # `symbolic-ref`, not `rev-parse --abbrev-ref`, and the push guard below
  # already asks this way for the same reason: a branch with no commits on it
  # yet has no HEAD to resolve, so rev-parse fails where the branch name is
  # perfectly well known. A batch is usually not in that state, but a mode
  # that fails open would then be quietly failing open on every stop.
  branch=$(git symbolic-ref --quiet --short HEAD 2>/dev/null) \
    || stop_open "git could not say which branch this is"
  [[ "$branch" == batch/* ]] || exit 0

  have python3 || stop_open "python3 is not on the PATH"

  # ALREADY NUDGED IN THIS STOP SEQUENCE. The harness sets this when the
  # session is continuing *because* a Stop hook blocked it, and honouring it is
  # what keeps a refusal from being a loop with no way out.
  # Answered by the exit status rather than printed and matched, because this
  # runs on every stop taken while a batch is in hand and piping into `grep`
  # spent a second process on one boolean. The same reasoning as the Bash
  # matcher's prefilter above, on the same grounds.
  printf '%s' "$payload" | python3 -c '
import json, sys
try:
    sys.exit(0 if json.load(sys.stdin).get("stop_hook_active") else 1)
except Exception:
    sys.exit(1)
' && exit 0

  have gh || stop_open "gh is not on the PATH"

  # One round trip for everything the decision needs, for the reason
  # `record_receipt` gives about not asking twice and racing itself.
  payload=$(gh_json pr view --json number,state,isDraft,headRefOid,statusCheckRollup) \
    || stop_open "gh could not describe the pull request for $branch"

  # Captured and tested, NOT `read ... || stop_open`: a herestring built from
  # empty output is still a line, so read assigns nothing and returns 0 and
  # that refusal could never fire. The mode failed open correctly anyway and
  # said nothing at all, which is the silent half of exactly what this file was
  # rewritten to remove.
  scene=$(printf '%s' "$payload" | python3 -c '
import json, sys
pr = json.load(sys.stdin)
rollup = pr.get("statusCheckRollup") or []
def settled(check):
    verdict = (check.get("conclusion") or check.get("state") or "").upper()
    return verdict in ("SUCCESS", "NEUTRAL", "SKIPPED")
print(pr.get("number") or 0,
      pr.get("state") or "",
      "draft" if pr.get("isDraft") else "ready",
      pr.get("headRefOid") or "",
      "green" if rollup and all(settled(c) for c in rollup) else "not-green")
')
  [[ -n "$scene" ]] || stop_open "could not read what gh said about $branch"
  read -r pr state draft head checks <<<"$scene"

  # A draft is work in progress and stopping in the middle of it is ordinary.
  # Red or unreported checks mean there is nothing to review yet.
  [[ "$state" == "OPEN" ]] || exit 0
  [[ "$draft" == "ready" ]] || exit 0
  [[ "$checks" == "green" ]] || exit 0
  [[ -n "$head" ]] || stop_open "gh did not say what the head of #$pr is"

  # The same question `check` asks before a merge, asked earlier.
  local recorded_head missing
  IFS="|" read -r recorded_head missing <<<"$(receipt_status "$pr")"

  [[ "$recorded_head" == "$head" && -z "$missing" ]] && exit 0

  # ONCE PER HEAD, keyed on the commit rather than the pull request. Why that
  # bound is here, and why this mode is a nudge where `check` is a lock, is
  # DEVELOPERS.md "When a branch is ready to merge" with the rest of what this
  # gate refuses.
  [[ -d "$REPO_ROOT/.claude" ]] || mkdir -p "$REPO_ROOT/.claude"
  NUDGES="$NUDGES" PR="$pr" HEAD_SHA="$head" python3 -c '
import json, os, sys
path = os.environ["NUDGES"]
try:
    with open(path) as fh:
        nudged = json.load(fh)
except (OSError, ValueError):
    nudged = {}
if not isinstance(nudged, dict):
    nudged = {}
if nudged.get(os.environ["PR"]) == os.environ["HEAD_SHA"]:
    sys.exit(1)
nudged[os.environ["PR"]] = os.environ["HEAD_SHA"]
with open(path, "w") as fh:
    json.dump(nudged, fh, indent=2, sort_keys=True)
    fh.write("\n")
' || exit 0

  [[ -n "$missing" ]] || missing="code-review,simplify"
  stop_block "Pull request #$pr is ready and green, and its review cycle has not been recorded.

  branch:  $branch
  missing: ${missing//,/ and }

A batch is finished when it is merged. Stopping here leaves work that looks
done and is not, which is the state this hook exists to make visible.

$CYCLE

If you are stopping deliberately, say so and stop again -- this asks once per
head, not once per attempt."
}

# ---------------------------------------------------------------------------
# check mode: invoked by the PreToolUse hook with the payload on stdin
# ---------------------------------------------------------------------------

case "${1:-check}" in
  stop)
    stop_mode
    exit 0
    ;;
  record)
    pr=${2:?usage: landing-gate.sh record <pr-number>}
    record_receipt "$pr"
    exit $?
    ;;
  clear)
    have python3 || { echo "landing-gate: python3 is needed to edit the receipts." >&2; exit 1; }
    if [[ -n "${2:-}" ]]; then
      # The success line used to print whatever happened, because the exit
      # status of the reader was never looked at -- so a receipts file holding
      # anything but an object made `pop` raise, left the file untouched, and
      # still said "Cleared the receipt". During the one operation somebody runs
      # precisely because they already suspect the receipts are wrong. Found by
      # the review of PR #30.
      #
      # `record` and `merge` read an unparseable receipts file as no receipts at
      # all, which is the safe direction there: no evidence, so no merge. Here
      # the same coercion would be the unsafe one -- it would REWRITE a file
      # nobody could read, discarding whatever was in it, and say it had cleared
      # one pull request's receipt. So this refuses instead and names the file;
      # DEVELOPERS.md "When a branch is ready to merge" says what to do about it.
      RECEIPTS="$RECEIPTS" PR="$2" python3 -c '
import json, os, sys
path, pr = os.environ["RECEIPTS"], os.environ["PR"]
try:
    with open(path) as fh:
        receipts = json.load(fh)
except FileNotFoundError:
    receipts = {}
except (OSError, ValueError) as exc:
    sys.exit(str(exc))
if not isinstance(receipts, dict):
    sys.exit("the receipts are a %s, not an object" % type(receipts).__name__)
receipts.pop(pr, None)
with open(path, "w") as fh:
    json.dump(receipts, fh, indent=2, sort_keys=True)
    fh.write("\n")
' || {
        echo "landing-gate: could not rewrite $RECEIPTS -- nothing was cleared." >&2
        echo "  'landing-gate.sh clear' with no pull request removes the file whole." >&2
        exit 1
      }
      echo "Cleared the receipt for pull request $2."
    else
      rm -f "$RECEIPTS"
      echo "Cleared every receipt."
    fi
    # The nudges go with them. They are a record of having asked rather than
    # evidence of anything, so there is nothing to preserve and a stale one
    # would mean the next stop said nothing about a cycle that is now missing.
    rm -f "$NUDGES"
    exit 0
    ;;
  status)
    if [[ ! -f "$RECEIPTS" ]]; then
      echo "No receipts. Merging is blocked."
      exit 0
    fi
    have python3 || { echo "landing-gate: python3 is needed to read the receipts." >&2; exit 1; }
    RECEIPTS="$RECEIPTS" python3 -c '
import json, os
try:
    with open(os.environ["RECEIPTS"]) as fh:
        receipts = json.load(fh)
except (OSError, ValueError):
    receipts = {}
if not receipts:
    print("No receipts. Merging is blocked.")
for pr, receipt in sorted(receipts.items()):
    print("pr #%s  %s" % (pr, (receipt.get("head") or "")[0:12]))
    for stage, found in sorted((receipt.get("evidence") or {}).items()):
        for item in found:
            print("    %-12s %s by %s at %s" % (
                stage, item.get("kind"), item.get("author"), item.get("at")))
'
    exit 0
    ;;
  check) ;;
  *)
    echo "usage: landing-gate.sh [check|record <pr>|status|clear [<pr>]]" >&2
    exit 1
    ;;
esac

# `read`, not `$(cat)`: this runs before every Bash command in the session, and
# a command substitution here is a fork and an exec paid by every one of them.
# The measurement is in the simplify pass on PR #30 -- 2.67 ms to 1.90 ms, which
# is 29% of what the common case costs, and it takes the gate from two processes
# to one. `read` returns non-zero at EOF having assigned, which is why the
# status is discarded rather than checked.
IFS= read -r -d '' payload

# A cheap prefilter, before anything is required to exist.
#
# This hook sees EVERY Bash command, and the matcher below needs python3. If a
# missing python3 refused everything, a machine without one could run no
# commands at all -- which is failing closed far past the point of usefulness.
# So: a command whose raw text does not so much as mention the two programs
# this gate guards cannot be one of the guarded actions, whatever it is doing,
# and is permitted without python3 being consulted at all.
#
# WORD boundaries, not a substring. This was `*gh*` and `*push*`, defended on
# the grounds that "the only cost of a false positive here is one python3
# invocation" -- and the simplify pass on PR #30 went and measured that cost.
# A miss is 2.65 ms and a hit is 27.1 ms, so the false positive is ten times the
# whole hook; and `through`, `caught`, `enough`, `straight`, `night`, `might`,
# `eight` and `Playwright` all contain one of the two words, which over 10,484
# real Bash calls meant 18.1% reached python3 where 2.9% had any business
# doing so. Every guarded spelling the suite asserts still matches: the
# indirections put other WORDS in front of `gh`, never other letters. `-` is
# deliberately NOT a word character here: it costs nothing to let a hyphen-
# adjacent spelling through to the matcher, and the error to avoid on this line
# is only ever the one that lets a command past unread.
if [[ ! "$payload" =~ (^|[^A-Za-z0-9_])(gh|push)([^A-Za-z0-9_]|$) ]]; then
  exit 0
fi

have python3 || deny_dependency python3 "reading what a command actually runs"

# What the command actually runs, as opposed to what it merely mentions.
#
# Quoted spans and heredoc bodies are data -- a bead description or a heredoc
# carrying documentation about this very workflow was once refused for
# containing the words -- so they are stripped before anything is matched, and
# what survives must additionally be in command position.
#
# Then the indirections. The old matcher looked for the literal token `gh` at
# the start of a command, so `env gh`, `command gh`, `/usr/bin/gh`, a pipe into
# `xargs gh` and `bash -c 'gh pr merge 1'` all walked straight past it. That
# was a fair trade while a human ask was the real control and this only had to
# stop a forgetful agent; it is not one now that the permission to merge rests
# here. Each of those is closed below.
#
# `bash -c` is the interesting one, because its payload is exactly the quoted
# span the first rule throws away. Recursing into it only when a shell is
# genuinely being invoked closes it without bringing back the false positives
# that stripping quotes exists to prevent.
#
# This does not become airtight and is not meant to; a command line has more
# spellings than a reader has patterns, and DEVELOPERS.md says so plainly.
verdict=$(printf '%s' "$payload" | python3 -c '
import json, re, sys

# Exit 2, never 0. A payload this cannot read is a command it cannot judge, and
# printing nothing is what the shell below reads as "nothing to guard" -- the
# same silent fail-open as the 2>/dev/null this file was rewritten to remove,
# and the last one left in it. The caller turns a non-zero exit into a refusal.
# This is past the prefilter, so only a command mentioning gh or push gets here.
try:
    cmd = json.load(sys.stdin)["tool_input"]["command"]
except Exception:
    sys.exit(2)

QUOTED = re.compile(r"\x27([^\x27]*)\x27|\"([^\"]*)\"")
SHELL_C = re.compile(r"\b(?:ba|z|k|da)?sh\s+(?:-\w+\s+)*-\w*c\w*\s+")

# What may sit between command position and the program without changing which
# program runs. Two kinds, and the review of PR #30 found that only the second
# was handled: a leading assignment (`GH_TOKEN=... gh pr merge 7`, which is the
# ordinary way to run gh as another identity) walked straight past, because
# assignments were only allowed AFTER a wrapper keyword.
ASSIGN = r"(?:\w+=\S*\s+)*"
WRAP = (ASSIGN
        + r"(?:(?:env|command|exec|sudo|doas|nohup|nice|time|xargs|stdbuf|setsid)"
          r"\s+(?:-\S+\s+|\w+=\S*\s+)*)*")
# The program, however it is spelt: a bare name, or any path ending in it.
def prog(name):
    return r"(?:[\w./~+-]*/)?" + name


# Flags that come before a git subcommand, including the two that take a value
# as a separate word. `git -C /tmp/repo push --force` was not recognised as a
# push at all, because `(?:-\S+\s+)*` eats `-C ` and then has to match `push`
# against `/tmp/repo`. Same shape for `git -c user.name=x push`.
GIT_FLAGS = r"(?:-[cC]\s+\S+\s+|--(?:git-dir|work-tree|namespace|exec-path)(?:=|\s+)\S+\s+|-\S+\s+)*"


def strip_heredocs(text):
    """Remove heredoc bodies. They are a document being written, never a
    command being run -- nothing in a heredoc executes here, whatever it says."""
    return re.sub(r"<<-?\s*[\x27\"]?(\w+)[\x27\"]?.*?^\1$", " ", text,
                  flags=re.S | re.M)


def strip(text):
    """Remove heredoc bodies and quoted spans -- both are data, not commands."""
    return QUOTED.sub(" ", strip_heredocs(text))


def shell_payloads(text):
    """The quoted argument of an actual `sh -c`, which is a command again."""
    out = []
    for m in SHELL_C.finditer(text):
        q = QUOTED.match(text, m.end())
        if q:
            out.append(q.group(1) if q.group(1) is not None else q.group(2))
    return out


# Command position: the start, or just after anything that begins a command.
#
# The first version had separators only, so a subshell, a brace group and every
# compound-command keyword were not command position -- `(gh pr merge 7)`,
# `{ gh pr merge 7; }` and `if true; then gh pr merge 7; fi` all walked past,
# which is the same class as the `env gh` and `xargs gh` spellings this file
# already closed. Found by the review of PR #30.
#
# `&&`, `||` and `$(` are not listed: the class already holds `&`, `|` and `(`,
# and since every use of this is an existence search, the one-character
# alternative matches wherever the two-character one would. Spelling them out
# again looked like coverage and was three more things to keep in step.
SEP = r"(?:^|[;&|(){]|\b(?:then|else|elif|do|in)\s)\s*"


# The pull request a gh subcommand names, which is the first bare number after
# it rather than the word immediately following it.
#
# `gh pr merge --rebase 7` used to yield no number at all, and the shell then
# fell back to "the pull request of the current branch" -- so on a branch with
# a valid receipt of its own, that command merged an unreviewed pull request 7.
# Found by the review of PR #30. Returning None here means "named none", which
# is the only case the fallback is for.
def numbered(cmd, after):
    m = re.search(after, cmd)
    if not m:
        return None
    for token in cmd[m.end():].split():
        if token.isdigit():
            return token
        if not token.startswith("-"):
            return None
    return None


def classify(raw):
    cmd = strip(raw)

    def runs(pattern):
        return re.search(SEP + WRAP + pattern, cmd, flags=re.M) is not None

    # --repo points gh at a repository whose pull requests this gate knows
    # nothing about: the receipts are keyed by number within THIS repository, so
    # a receipt for 7 here would vouch for 7 somewhere else.
    elsewhere = re.search(r"(?:^|\s)(?:-R|--repo)(?:=|\s+)\S+", cmd) is not None

    if runs(prog("git") + r"\s+" + GIT_FLAGS + r"push\b"):
        # A bare --force has no lease, so it overwrites whatever arrived while
        # you were not looking. -f is the same flag and is caught with it.
        #
        # The two boundaries are what keep --force-with-lease -- a different
        # flag, and a free one -- out of both patterns: the lookahead stops
        # --force matching its prefix, and the lookbehind stops -f matching the
        # one inside it. --follow-tags is the same shape and the same reason.
        if re.search(r"\bpush\b[^;&|]*(?:--force(?![\w-])|(?<![\w-])-f(?![\w-]))", cmd):
            return "push-force"
        # Whether this push lands on main is NOT decided here.
        #
        # It used to be, by looking for the word `main` anywhere in the command,
        # which was wrong in both directions and the review of PR #30 caught
        # both: `git push origin batch/main-fix` was refused with advice that
        # made no sense, and `git push` on a checked-out main, and `git push
        # origin HEAD`, were permitted -- the two likeliest ways anybody
        # actually pushes to main. A branch name is not a substring question.
        # So the arguments go back to the shell, which has git and can ask.
        m = re.search(r"\bpush\b([^;&|]*)", cmd)
        return "push " + " ".join((m.group(1) if m else "").split())

    if runs(prog("gh") + r"\s+pr\s+merge\b"):
        if elsewhere:
            return "merge-elsewhere"
        return "merge " + (numbered(cmd, r"pr\s+merge\b") or "")

    # The REST spelling of the same thing: gh api -X PUT .../pulls/N/merge.
    #
    # Searched with the QUOTED SPANS LEFT IN, because quoting the URL -- the
    # ordinary way to write it -- hid it from the stripped text and turned a
    # refusal into a permit. Found by the review of PR #30.
    #
    # But heredoc bodies are still stripped, and that distinction is the whole
    # point rather than a detail. A quoted span is an ARGUMENT of the command
    # about to run; a heredoc body is a document being written to a file, and
    # nothing in it executes. Searching the fully raw text conflated the two,
    # and the first thing it did was refuse `gh api graphql` for a command whose
    # heredoc quoted this very URL while replying to the review that asked for
    # the fix. `runs` is still against the fully stripped text, so `gh api` has
    # to be in command position before any of this applies.
    if runs(prog("gh") + r"\s+api\b"):
        m = re.search(r"/pulls/(\d+)/merge\b", strip_heredocs(raw))
        if m:
            return "merge-elsewhere" if elsewhere else "merge " + m.group(1)

    if runs(prog("gh") + r"\s+pr\s+ready\b"):
        if elsewhere:
            return "merge-elsewhere"
        return "ready " + (numbered(cmd, r"pr\s+ready\b") or "")

    return None


for candidate in [cmd] + shell_payloads(cmd):
    found = classify(candidate)
    if found:
        print(found)
        break
') || {
  # Two causes, one refusal: python3 vanished between the check above and here,
  # or the matcher could not read the payload and exited 2. Neither is a reason
  # to permit, and the old code's `2>/dev/null` plus an empty verdict is exactly
  # how the second one used to be read as "nothing to guard".
  have python3 || deny_dependency python3 "reading what a command actually runs"
  # The matcher could not be read, and the reason may be that this file is the
  # one the rebase stopped in. Said on stderr rather than passed over quietly:
  # the guard really is off, and that is worth seeing in the transcript.
  if own_source_is_mid_conflict; then
    echo "landing-gate: this file is mid-conflict, so the gate is standing down" >&2
    echo "  until the markers are gone. Finish or abort the rebase; nothing is guarded" >&2
    echo "  meanwhile. See DEVELOPERS.md \"When a branch is ready to merge\"." >&2
    exit 0
  fi
  deny_unavailable "a reading of what this command runs" "the matcher"
}

[[ -n "$verdict" ]] || exit 0

# The verdict is an action and, for some actions, the rest of what was matched:
# a pull request number, or the arguments of a push. `read` splits it in one
# line and leaves `rest` empty for a bare action, which four lines of `${...%%}`
# and `${...#}` were doing by hand -- one of them (`rest=${rest# }`) unable to
# fire at all, since `classify` never emits a double space at the split.
read -r action rest <<<"$verdict"
pr=$rest


case "$action" in
  push-force)
    deny "A bare --force overwrites whatever arrived while you were not looking.

Use --force-with-lease instead. It does the same job and refuses if the remote
moved since you last fetched, which is the only difference between them and the
whole reason AGENTS.md puts one on the free row and the other behind an ask.

  git push --force-with-lease

If the lease genuinely has to be broken -- somebody else pushed, and you have
agreed with them that their commits go -- then that is the ask, and it is a
person's to answer rather than yours. See AGENTS.md \"Git\"."
    ;;

  push)
    # THIS ARM IS A SHORTCUT, NOT THE ENFORCEMENT, and it is the only one here
    # that is. `scripts/repo-settings.sh` sets `enforce_admins`,
    # `allow_force_pushes: false` and required reviews on `main`, and the
    # Settings workflow re-checks them, so the push below is refused by GitHub
    # whether or not this file exists. What this buys is the refusal arriving
    # before the round trip, and a message that names the batch/* workflow
    # instead of a protection rule. The `--force` arm above is the opposite
    # case: nothing server-side protects a batch/* branch from it, so that one
    # really is the guard. Worth knowing before anybody trims this file.
    #
    # Which branch this push actually lands on, asked of git rather than
    # guessed from the presence of the word "main" in the command line.
    #
    # `rest` is everything after the `push` token. A refspec `src:dst` names its
    # destination; a lone branch name after the remote names itself; nothing at
    # all -- and `HEAD` -- mean the current branch, which is how `git push` on a
    # checked-out main used to walk straight past this arm.
    have git || deny_dependency git "reading which branch this push would land on"

    target=""
    for token in $rest; do
      case "$token" in
        # Flags first, so a flag carrying a colon is a flag rather than a
        # refspec. `case` takes the first arm that matches, which is what makes
        # one statement enough where there used to be two.
        -*) ;;
        *:*) target=${token##*:} ;;
        HEAD) target="" ;;
        *)
          # The first bare word is the remote; the second, if any, is the
          # refspec. A single bare word is the remote and nothing else.
          if [[ -n "${remote_seen:-}" ]]; then target=$token; else remote_seen=1; fi
          ;;
      esac
    done
    if [[ -z "$target" ]]; then
      target=$(git symbolic-ref --quiet --short HEAD 2>/dev/null) || target=""
    fi
    target=${target#refs/heads/}

    if [[ "$target" == main ]]; then
      deny "main takes nothing except a rebase merge of a green pull request, and
branch protection would refuse this anyway.

  this push lands on: $target

Publish to a batch/* branch and open a pull request instead. See
DEVELOPERS.md \"Pull requests\"."
    fi
    exit 0
    ;;

  merge-elsewhere)
    deny "This names another repository with --repo, and the landing gate only
knows this one.

Its receipts are keyed by pull request number within this repository, so a
recorded cycle for #7 here would be read as vouching for #7 over there. It
refuses rather than guess which you meant.

Run it from a checkout of that repository, where its own gate can answer."
    ;;

  ready)
    have gh || deny_dependency gh "reading whether the checks on this pull request are green"
    [[ -n "$pr" ]] || pr=$(pr_current) || deny_unavailable "which pull request this branch belongs to" gh
    failing=$(gh_json pr checks "$pr" --json name,state \
      --jq '[.[] | select(.state != "SUCCESS" and .state != "SKIPPED")] | length') || failing=""
    # No checks reported yet is not the same as checks that failed; a run that
    # has not started cannot be green, so this still refuses. Neither is a gh
    # that could not answer: that used to be read as green.
    if [[ -z "$failing" ]]; then
      deny_unavailable "whether the checks on pull request $pr are green" gh
    fi
    if [[ "$failing" != "0" ]]; then
      deny "Pull request $pr is not green, so it is not ready to be reviewed.

Marking it ready is what invites the review, and a review spent on what a
linter would have said is a review wasted. Wait for the checks, or fix them.

  gh pr checks $pr --watch"
    fi
    exit 0
    ;;

  merge)
    have gh || deny_dependency gh "reading the head this merge would land"
    have python3 || deny_dependency python3 "reading the recorded review cycle"
    [[ -n "$REPO_ROOT" ]] || deny_unavailable "where this repository is" "git and CLAUDE_PROJECT_DIR"
    # Two questions, one round trip. When the command names no pull request this
    # arm needs both its number and its head, and asking `gh pr view` twice
    # spends a second GH_DEADLINE window out of the hook's timeout for an answer
    # the first call could have carried -- the consolidation `record` already
    # makes, for the same reason. `current` stays empty when the command named
    # its pull request, and the comparison below fetches the head then.
    current=""
    if [[ -z "$pr" ]]; then
      both=$(gh_json pr view --json number,headRefOid --jq '"\(.number) \(.headRefOid)"') \
        || deny_unavailable "which pull request this branch belongs to" gh
      read -r pr current <<<"$both"
    fi

    # THE FINISHED QUESTION, asked here because this is the only moment it is
    # the right question. CI asks the structural half on every push and stays
    # quiet about a branch that is merely unfinished -- inventory-tng-ee2c is
    # why, and it is 48% of this repository's pull request runs. A batch that
    # is not collapsed, or holds an issue that has not landed, is not wrong: it
    # is not ready, and the difference only matters at a merge.
    #
    # No `[[ -f "$RECEIPTS" ]]` guard: the reader below catches the missing file
    # under its own `except`, prints nothing, and falls into the identical
    # refusal four lines further down. Two copies of one refusal is the thing
    # this file's own header rule is about.
    #
    # A receipt is its head AND its evidence, so this prints nothing for one
    # that has a head but no evidence behind it. That is not a hypothetical
    # shape: it is exactly what the gate wrote before this change, and leaving
    # those readable would mean the merges it was rewritten to stop are
    # permitted by whatever is already on disk.
    status=$(receipt_status "$pr") \
      || deny_unavailable "the recorded review cycle" "the receipts file"
    IFS="|" read -r recorded missing <<<"$status"
    # All or nothing here, unlike the stop hook: a receipt missing either stage
    # is not a receipt, and the head it names vouches for nothing.
    [[ -n "$missing" ]] && recorded=""

    [[ -n "$recorded" ]] || deny "No review cycle has been recorded for pull request $pr.

$CYCLE"

    # The comparison the old gate SKIPPED when gh could not answer, which let a
    # receipt from a superseded head permit the merge.
    [[ -n "$current" ]] || current=$(pr_head "$pr") || deny_unavailable \
      "the head of pull request $pr, to check it is what was reviewed" gh

    if [[ "$recorded" != "$current" ]]; then
      deny "Pull request $pr has moved since it was reviewed, so what is about to
merge is not what was reviewed.

  reviewed: $recorded
  current:  $current

$CYCLE"
    fi

    # ASKED OF THE BRANCH BEING MERGED, not the one that happens to be checked
    # out. `gh pr merge <n>` takes a number and will merge a pull request that
    # is not what you are standing on -- and a tidy local `main` has nothing
    # waiting to be folded in and no half-landed epic, so the checker would
    # pass and this would permit a merge it never looked at. That is the
    # direction this file may not fail in.
    checked_out=$(cd "$REPO_ROOT" && git rev-parse HEAD 2>/dev/null) \
      || deny_unavailable "which commit is checked out" git
    if [[ "$checked_out" != "$current" ]]; then
      deny "Pull request $pr is not what is checked out, so its readiness cannot be checked here.

  pull request $pr: $current
  checked out:      $checked_out

Check out the branch you are merging. This refuses rather than merging
something it has not looked at."
    fi

    # Its own exit status, not its output: a checker that died has checked
    # nothing, which is the direction this file refuses in everywhere else.
    checker=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/check-batch.sh
    if ! unfinished=$(cd "$REPO_ROOT" && "$checker" origin/main..HEAD 2>&1); then
      deny "Pull request $pr is not finished, so it is not ready to merge.

$unfinished

$CYCLE"
    fi
    exit 0
    ;;
esac

exit 0
