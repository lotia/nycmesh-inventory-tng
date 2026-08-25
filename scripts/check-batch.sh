#!/usr/bin/env bash
# One issue per commit, read across a whole branch rather than one at a time.
#
# check-commit.sh sees a single message and what is staged beneath it, which is
# enough to refuse a commit holding two issues and not enough to notice that a
# branch closed the same issue twice, interleaved two issues' commits, or landed
# work nobody put in the batch. That is what this reads.
#
# The rules are in DEVELOPERS.md "Pull requests" and "Commits".
#
# Usage: check-batch.sh [<range>] [--epic <id>] [--draft] [--list] [--squashed]
#
# <range> defaults to origin/main..HEAD, or with --squashed to the last fifty
# commits -- see TRIPWIRE_DEPTH. --epic names the batch's epic in the
# tracker; without it the epic is inferred from the issues the range closes.
# Issues from two epics are refused, and so are several landing under none.
# --draft
# says the branch is still under review, where commits waiting to be folded in
# are expected rather than a fault.

set -uo pipefail

RANGE=""
# How far back --squashed looks with no range given. A tripwire, not an audit:
# history older than the conventions it reads would fail it forever.
TRIPWIRE_DEPTH=50
EPIC=""
DRAFT=0
LIST=0
SQUASHED=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --epic)
      EPIC=${2:?--epic needs an id}
      shift 2
      ;;
    # See DEVELOPERS.md#merging on why a branch under review is different.
    --draft)
      DRAFT=1
      shift
      ;;
    # A tripwire over landed history; see below.
    --squashed)
      SQUASHED=1
      shift
      ;;
    # The membership, for something that wants to say it rather than check it.
    --list)
      LIST=1
      shift
      ;;
    *)
      RANGE=$1
      shift
      ;;
  esac
done

if [[ "$SQUASHED" -eq 1 && ( "$LIST" -eq 1 || -n "$EPIC" || "$DRAFT" -eq 1 ) ]]; then
  echo "--squashed asks one question of landed history; the other flags do not apply." >&2
  exit 2
fi

if [[ "$LIST" -eq 1 && ( -n "$EPIC" || "$DRAFT" -eq 1 ) ]]; then
  echo "--list says what the range holds and checks nothing, so --epic and --draft do not apply." >&2
  exit 2
fi

if [[ -z "$RANGE" ]]; then
  if [[ "$SQUASHED" -eq 1 ]]; then
    # The last TRIPWIRE_DEPTH commits, or all of them where there are fewer --
    # a young repository is watched too. Resolved here rather than by a caller:
    # `git rev-parse` prints the literal argument when it cannot resolve one,
    # and a caller writing "$(git rev-parse HEAD~50)..HEAD" gets a range that
    # looks fine and reads nothing.
    if base=$(git rev-parse --verify "HEAD~${TRIPWIRE_DEPTH}" 2>/dev/null); then
      RANGE="${base}..HEAD"
    else
      RANGE="$(git rev-list --max-parents=0 HEAD | tail -1)..HEAD"
    fi
  else
    RANGE="origin/main..HEAD"
  fi
fi

REPO_ROOT=$(git rev-parse --show-toplevel) || exit 1
# Beside this script rather than under the repository being read: the range may
# belong to a checkout that has no scripts/ of its own, and the two are a pair.
HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
MEMBERSHIP="$HERE/batch-membership.py"

. "$HERE/report.sh"
. "$HERE/trailers.sh"
# The message rules themselves. check-commit.sh is the other caller, so the two
# enforce the same text by sharing this file rather than by one reading the
# other's output.
. "$HERE/message-rules.sh"

# A range git cannot resolve is not an empty range. Swallowing the difference
# would let a typo, a shallow checkout or a missing base commit report that
# there was nothing to check and pass as a required check having read nothing.
if ! listing=$(git rev-list --reverse --no-merges "$RANGE" 2>&1); then
  echo "Cannot read $RANGE:" >&2
  printf '  %s\n' "$listing" >&2
  exit 1
fi

mapfile -t commits <<<"$listing"
[[ ${#commits[@]} -eq 1 && -z "${commits[0]}" ]] && commits=()

if [[ ${#commits[@]} -eq 0 ]]; then
  # Nothing on stdout in --list mode: a caller reading rows would take the
  # sentence for one.
  [[ "$LIST" -eq 0 ]] && echo "No commits in $RANGE. Nothing to check."
  exit 0
fi

# --- reading the range -----------------------------------------------------
#
# The trailer rather than the summary prefix: the prefix is the short form, for
# a reader, and the trailer is the whole identifier. check-commit.sh is what
# insists they refer to the same thing.

declare -a order=()   # the issue each commit names, in order
declare -A closes=()  # issue -> how many commits in the range close it

rows=()

pending=0

# One git process for the range rather than four per commit: the subject and
# the body are both wanted, and both loops below would otherwise ask again.
# Record-separated so a body containing blank lines survives.
declare -A subject_of=() body_of=()
while IFS= read -r -d $'\x1e' record; do
  [[ -z "${record//[$'\n']/}" ]] && continue
  record=${record#$'\n'}
  sha_part=${record%%$'\x1f'*}
  rest=${record#*$'\x1f'}
  subject_of[$sha_part]=${rest%%$'\x1f'*}
  body_of[$sha_part]=${rest#*$'\x1f'}
done < <(git log --reverse --no-merges --format="%H%x1f%s%x1f%B%x1e" "$RANGE")

# One question of landed history: does any commit close more than one issue?
# That is what a squash merge composes, and it is readable whatever convention
# the message was written under -- which is why this asks nothing else.
if [[ "$SQUASHED" -eq 1 ]]; then
  for sha in "${commits[@]}"; do
    closes_here=0
    while IFS= read -r trailer; do
      [[ "$trailer" == Closes* ]] && closes_here=$((closes_here + 1))
    done < <(trailers_of "${body_of[$sha]:-}")
    if [[ "$closes_here" -gt 1 ]]; then
      fail "${sha:0:8} closes $closes_here issues: ${subject_of[$sha]:-}"
    fi
  done
  verdict "No commit here closes more than one issue." "one of them is split"
fi

# What `rebase --autosquash` would leave.
#
# A fixup, a squash and an amend all name the commit they belong to in the same
# place, so one predicate covers the three and amend! stops being a case this
# cannot read. What they name is not needed: a commit waiting to be folded in
# is not work, whichever commit absorbs it.
absorbed() {
  local subject=$1
  case "$subject" in
    fixup!\ * | squash!\ * | amend!\ *) ;;
    *) return 1 ;;
  esac
  # A prefix with nothing after it names nothing, and is a commit like any
  # other rather than one waiting to be folded in.
  [[ -n "${subject#* }" ]]
}

for sha in "${commits[@]}"; do
  subject=${subject_of[$sha]:-}

  if absorbed "$subject"; then
    pending=$((pending + 1))
    continue
  fi

  mapfile -t trailers < <(trailers_of "${body_of[$sha]:-}")

  # A commit naming no issue, or naming two, is message_rules' objection to
  # make -- it is applied below and would say it again. What is collected here
  # is only what a range needs and one message cannot give: which issue each
  # commit belongs to, in order, and how often each is closed.
  if [[ ${#trailers[@]} -eq 0 ]]; then
    order+=("")
    continue
  fi

  named=""
  for trailer in "${trailers[@]}"; do
    issue=$(issue_of "$trailer")
    [[ -z "$named" ]] && named=$issue
    [[ "$trailer" == Closes* ]] && closes[$issue]=$((${closes[$issue]:-0} + 1))
  done

  rows+=("$(printf '%s\t%s\t%s' "${sha:0:8}" "$named" "$subject")")
  order+=("$named")
done

# One branch, at the end: either the rows are the output or they are the
# preamble to everything below.
if [[ "$LIST" -eq 1 ]]; then
  [[ ${#rows[@]} -gt 0 ]] && printf '%s\n' "${rows[@]}"
  exit 0
fi

echo "Commits in $RANGE:"
for row in ${rows+"${rows[@]}"}; do
  note "${row//$'\t'/  }"
done

# --- an issue is closed once, and its commits sit together -----------------

for issue in "${!closes[@]}"; do
  if [[ ${closes[$issue]} -gt 1 ]]; then
    fail "$issue is closed by ${closes[$issue]} commits. It can only be finished once."
  fi
done

# Contiguity is not a correctness rule -- interleaved commits still each hold
# one issue -- but a branch that jumps between issues and back cannot be read,
# and a rebase that reorders it is one conflict away from mixing them.
declare -A seen=()
previous=""
for issue in "${order[@]}"; do
  [[ -z "$issue" ]] && continue
  if [[ "$issue" != "$previous" ]]; then
    if [[ -n "${seen[$issue]:-}" ]]; then
      fail "$issue is picked up again after $previous. Keep an issue's commits together."
    fi
    seen[$issue]=1
    previous=$issue
  fi
done

# --- the branch and the batch agree ----------------------------------------
#
# Only when there is a tracker to read. A contributor working from GitHub
# issues has none, and the rules above are the ones that matter anyway.

ISSUES="$REPO_ROOT/.beads/issues.jsonl"
if [[ -f "$ISSUES" ]]; then
  # Built by iterating rather than from "${!closes[@]}" directly: an empty
  # associative array still yields one empty word, and sorting it through
  # printf would put that word straight back. So the sort happens inside the
  # guard, where the array is known not to be empty.
  landed=()
  for issue in "${!closes[@]}"; do
    [[ -n "$issue" && "$issue" != \#* ]] && landed+=("$issue")
  done

  # See batch-membership.py for why the export is read rather than `bd`.
  if [[ ${#landed[@]} -gt 0 ]]; then
    mapfile -t landed < <(printf '%s\n' "${landed[@]}" | sort)

    # Its exit status, not just its output: a helper that died has checked
    # nothing, and saying nothing is how that looks from here.
    # How many commits did the closing, not how many issues were closed: one
    # commit closing four is already refused as a squash, and telling its
    # author to make an epic would be the wrong remedy for it.
    closing_commits=0
    for sha in "${commits[@]}"; do
      while IFS= read -r trailer; do
        [[ "$trailer" == Closes* ]] && { closing_commits=$((closing_commits + 1)); break; }
      done < <(trailers_of "${body_of[$sha]:-}")
    done
    if ! verdict=$(EPIC="$EPIC" LANDED_COMMITS="$closing_commits" \
      python3 "$MEMBERSHIP" "$ISSUES" "${landed[@]}" 2>&1); then
      fail "the batch could not be read from $ISSUES:"
      printf '      %s\n' "$verdict"
    else
      dispatch "$verdict"
    fi
  fi
fi

# --- and each message still stands on its own ------------------------------
#
# Shared rather than reimplemented: the summary and trailer rules have one
# home, message-rules.sh, and both checkers source it.
#
# The rules only, not the tracker cross-check: that reads a staged diff, and a
# commit that has already landed has none. message-rules.sh is exactly the half
# that applies, which is why it is a file rather than a flag on check-commit.sh.
#
# Called, not forked. Two scripts beside each other, already sharing a library,
# were agreeing on the rules only for as long as one kept parsing the other's
# prose. The objections come back through report.sh already, so REPORT_PREFIX
# attributes them to their commit rather than a scraper re-wrapping each line.

# The module has to be here: sourcing it silently and calling an undefined
# function would turn every rule below into a no-op while this script still
# exited 0, which is a required check that read nothing.
if ! declare -F message_rules >/dev/null; then
  echo "Cannot read the message rules: $HERE/message-rules.sh is missing." >&2
  exit 1
fi

for sha in "${commits[@]}"; do
  subject=${subject_of[$sha]:-}
  # An amend! read as a standalone commit is charged seven characters of
  # prefix against a fifty-column limit.
  absorbed "$subject" && continue
  # git writes a merge, revert or cherry-pick message itself, and none of them
  # is somebody's issue being landed. Forking check-commit.sh used to skip
  # them for free, because that script refuses them at its own front door.
  message_is_git_own "$subject" && continue
  REPORT_PREFIX=${sha:0:8}
  message_rules "${body_of[$sha]:-}"
  REPORT_PREFIX=""
done

if [[ "$pending" -gt 0 ]]; then
  if [[ "$DRAFT" -eq 1 ]]; then
    note "$pending commits are waiting to be folded in, which is fine while reviewing:"
  else
    fail "$pending commits are waiting to be folded in. Nothing does it on the way in:"
  fi
  # `core.editor`, and this said `sequence.editor` until a review ran it. The
  # sequence editor is the one shown the TODO LIST, which a rebase that was not
  # asked for `-i` never opens, so setting it changed nothing at all. What
  # `absorbed` counts includes squash!, and a squash! asks for the combined
  # MESSAGE -- core.editor's job -- so the rebase this printed stopped on
  # "there was a problem with the editor" or sat waiting for one. Measured on
  # git 2.55, both ways round, on inventory-tng-4md.
  #
  # Folding a squash! with no editor takes the message git assembled, which is
  # the target's followed by the squash's own. That can leave the trailers in
  # the middle of the message, and the rules above say so on the next run.
  note "  git -c core.editor=true rebase --autosquash origin/main"
fi

kept=$(( ${#commits[@]} - pending ))
if [[ "$kept" -eq 1 ]]; then
  verdict "One commit, one issue. Nothing to object to." merging
else
  verdict "$kept commits, one issue each. Nothing to object to." merging
fi
