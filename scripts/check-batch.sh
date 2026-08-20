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
# Usage: check-batch.sh [<range>] [--epic <id>]
#
# <range> defaults to origin/main..HEAD. --epic names the batch's epic in the
# tracker; without it the epic is inferred from the issues the range closes,
# and if they do not agree on one, membership is simply not checked.

set -uo pipefail

RANGE="origin/main..HEAD"
EPIC=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --epic)
      EPIC=${2:?--epic needs an id}
      shift 2
      ;;
    *)
      RANGE=$1
      shift
      ;;
  esac
done

REPO_ROOT=$(git rev-parse --show-toplevel) || exit 1
# Beside this script rather than under the repository being read: the range may
# belong to a checkout that has no scripts/ of its own, and the two are a pair.
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CHECK="$HERE/check-commit.sh"
MEMBERSHIP="$HERE/batch-membership.py"

problems=0
fail() {
  printf '  ✗ %s\n' "$1"
  problems=$((problems + 1))
}
note() { printf '  · %s\n' "$1"; }

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
  echo "No commits in $RANGE. Nothing to check."
  exit 0
fi

# --- what each commit says it belongs to -----------------------------------
#
# The trailer rather than the summary prefix: the prefix is the short form, for
# a reader, and the trailer is the whole identifier. check-commit.sh is what
# insists they refer to the same thing.

declare -a order=()   # the issue each commit names, in order
declare -A closes=()  # issue -> how many commits in the range close it

echo "Commits in $RANGE:"

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

for sha in "${commits[@]}"; do
  subject=${subject_of[$sha]:-}

  # A branch under review is full of these, and they carry no trailer because
  # they are not commits yet -- they are corrections waiting to be folded into
  # one. They are not read as work belonging to nothing, but they are still
  # refused: nothing autosquashes them on the way in. GitHub's rebase merge
  # replays them exactly as they are, and squash merge, which would at least
  # have absorbed them, is disabled. So a branch is not mergeable until they
  # are gone, and this is what says so.
  #
  # amend! is not in this list. It carries the original message, trailers and
  # all, so skipping it would lose a commit's issue and counting it would find
  # that issue closed twice; it needs handling of its own before it can be
  # used here.
  if [[ "$subject" == fixup!* || "$subject" == squash!* ]]; then
    pending=$((pending + 1))
    continue
  fi

  if [[ "$subject" == amend!* ]]; then
    fail "${sha:0:8} is an amend! commit, which this cannot read: $subject"
    note "  use --fixup, or collapse it before asking"
    order+=("")
    continue
  fi

  mapfile -t trailers < <(printf '%s\n' "${body_of[$sha]:-}" | grep -E '^(Closes|Refs) ')

  # A commit naming no issue, or naming two, is check-commit.sh's objection to
  # make -- it is delegated below and would say it again. What is collected
  # here is only what a range needs and one message cannot give: which issue
  # each commit belongs to, in order, and how often each is closed.
  if [[ ${#trailers[@]} -eq 0 ]]; then
    order+=("")
    continue
  fi

  named=""
  for trailer in "${trailers[@]}"; do
    issue=${trailer#* }
    issue=${issue%%[[:space:]]*}
    [[ -z "$named" ]] && named=$issue
    [[ "$trailer" == Closes* ]] && closes[$issue]=$((${closes[$issue]:-0} + 1))
  done

  note "${sha:0:8}  $named  $subject"
  order+=("$named")
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
    if ! verdict=$(EPIC="$EPIC" python3 "$MEMBERSHIP" "$ISSUES" "${landed[@]}" 2>&1); then
      fail "the batch could not be read from $ISSUES:"
      printf '      %s\n' "$verdict"
    else
      while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        case "$line" in
          fail\ *) fail "${line#fail }" ;;
          note\ *) note "${line#note }" ;;
        esac
      done <<<"$verdict"
    fi
  fi
fi

# --- and each message still stands on its own ------------------------------
#
# Delegated rather than reimplemented: the summary and trailer rules have one
# home, and it is the script a contributor runs before committing.
#
# --message-only because the tracker cross-check reads a staged diff, and a
# commit that has already landed has none. Asking for the rules that apply is
# the whole of it; reading everything and then discarding objections whose
# wording looked like staging would discard real ones too.

if [[ -x "$CHECK" ]]; then
  for sha in "${commits[@]}"; do
    subject=${subject_of[$sha]:-}
    [[ "$subject" == fixup!* || "$subject" == squash!* ]] && continue
    if ! output=$("$CHECK" --message-only <(printf '%s\n' "${body_of[$sha]:-}") 2>&1); then
      while IFS= read -r line; do
        [[ "$line" == *"✗"* ]] || continue
        fail "${sha:0:8} ${line#*✗ }"
      done <<<"$output"
    fi
  done
fi

echo
if [[ "$pending" -gt 0 ]]; then
  fail "$pending fixup commits have not been folded in. Nothing does it on the way in:"
  note "  git rebase -i --autosquash origin/main"
fi

if [[ "$problems" -eq 0 ]]; then
  kept=$(( ${#commits[@]} - pending ))
  if [[ "$kept" -eq 1 ]]; then
    echo "One commit, one issue. Nothing to object to."
  else
    echo "$kept commits, one issue each. Nothing to object to."
  fi
  exit 0
fi

if [[ "$problems" -eq 1 ]]; then
  echo "One thing to fix before merging."
else
  echo "$problems things to fix before merging."
fi
exit 1
