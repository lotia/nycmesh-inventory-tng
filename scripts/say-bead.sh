#!/usr/bin/env bash
# Put what a bead holds, and its issue body cannot, on the issue as a comment.
#
# `unsaid.py` composes it and says why it is a comment rather than a body. This
# is the half that talks to GitHub: one comment per issue, rewritten in place
# rather than added to, and taken down again when the bead stops having anything
# to say. `inventory-tng-cwpa.15`.
#
# Usage: say-bead.sh [--dry-run | --confirm] [--only <bead>] [<repository>]
#
# --dry-run says what it would write and writes nothing.
# --only does one bead, which is how a person checks the wording before a pass
# over the whole tracker.
# --confirm allows a pass that would CREATE more comments than $NEW_WITHOUT_ASKING.
#
# === KEEPING A FEW CURRENT AND MIRRORING THE TRACKER ARE DIFFERENT ACTS ===
#
# 0031's amendment argues that; what this file adds is the operational half.
#
# So a pass that would create more than a few refuses and says what to type.
#
# ONLY CREATIONS ARE COUNTED, and the reason is what the family's line actually
# is. A new comment NOTIFIES EVERY WATCHER of that issue, and deleting it does
# not unsend the notification -- so a creation is irreversible in the only way
# that matters here, whatever the comment itself can be made to say afterwards.
# An edit notifies nobody. That is also why this sits beside step 4 rather than
# contradicting it: `bd`'s push PATCHes bodies of issues that already exist, and
# `export-issues.sh` asks for `--confirm` because it CREATES. The line is
# creation against update, and both halves of this are on the right side of it.
#
# WHY THERE IS NO `--check`, unlike every other script in this family, and it is
# not about cost: `--dry-run` already computes the whole answer for about five
# requests, so a check would be that plus an exit status.
#
# IT IS THAT NONE OF THE STANDING QUESTIONS IS ABOUT CONTENT. The three
# `sync-issues.sh --check` asks are all about work one side has never heard of,
# or disagrees about being finished. Nothing asks whether an issue's BODY still
# matches its bead's description either -- that drift is repaired by the next
# ordinary run, and a comment's is repaired the same way, by the same run. A
# check for this one and not for the body it sits under would be the special
# case, not the missing piece.
#
# NO CURRENT-CHECKOUT GUARD EITHER, and that is a decision rather than an
# omission. `export-issues.sh` and `pull-new-issues.sh` refuse from a stale
# checkout because both CREATE, and a duplicate issue cannot be deleted with an
# ordinary token -- 0031, and inventory-tng-cwpa.10. Nothing here creates: every
# write overwrites this script's own marked comment or removes it, so a run from
# a stale checkout writes text that the next run corrects, and posting an old
# rendering is not the kind of mistake that accumulates. The one path where it
# would matter is the reconciliation, and step 1 there has already refused a
# stale checkout before this is reached.

set -uo pipefail

HERE=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")
. "$HERE/report.sh"
. "$HERE/repository.sh"

REPO_ROOT=$(git -C "$HERE" rev-parse --show-toplevel) || exit 1
EXPORT="$REPO_ROOT/.beads/issues.jsonl"

#: Between writes, because GitHub's secondary limits are about the RATE of
#: content creation rather than the hourly budget, and a full pass is several
#: hundred writes in a row. GitHub's own guidance is to wait at least a second
#: between them. A pass costs about as long in seconds as it writes comments,
#: which is why it is not on a timer.
BETWEEN_WRITES=1

#: How many comments a run may CREATE before it stops and asks. Twenty-five is
#: not a measurement; it is "more than somebody could watch go past", which is
#: the honest bar for the difference the header describes. A pass that is
#: keeping up with ordinary work never comes near it.
NEW_WITHOUT_ASKING=25

dry_run=false
confirm=false
only=""
repository="${GITHUB_REPOSITORY:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) dry_run=true ;;
    --confirm) confirm=true ;;
    --only)
      [[ $# -ge 2 ]] || refuse "--only needs a bead"
      only=$2
      shift
      ;;
    -*) refuse "unknown flag $1" ;;
    *) repository="$1" ;;
  esac
  shift
done

# `jq` for reading the comment listing back, which `gh --jq` cannot do across
# pages. say-batch.sh reaches for it the same way and for the same call.
need_tools gh python3 jq

resolve_repository "$repository"
repository="$REPOSITORY"

[[ -f "$EXPORT" ]] || refuse "$EXPORT does not exist, so there is nothing to say."

# THE MARKER IS unsaid.py's, ASKED FOR RATHER THAN TYPED. It is what decides
# which comment this script owns, so a second spelling here would make it
# capable of writing one comment and finding another -- which reads as a run
# that posted nothing and leaves two.
MARKER=$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import unsaid; print(unsaid.MARKER)' \
  "$HERE") || refuse "could not read the marker unsaid.py uses."

# === STDERR IS NEVER PART OF AN ANSWER, and the two files are made here so that
# ONE trap covers them. A second `trap ... EXIT` replaces the first, which is
# the arrangement sync-issues.sh already spells out thirty lines from its own
# pair.
#
# WHY IT MATTERS ON THE SUCCESS PATH, which is the half that is easy to miss. A
# warning python printed while still exiting 0 -- a deprecation, a locale
# grumble -- becomes a row of the listing here, and BECOMES THE FIRST LINE OF A
# COMMENT below: the marker is displaced, so nothing this script writes can be
# found again, the digest never matches, and every run rewrites every comment
# it made. sync-issues.sh keeps `gh`'s stderr out of its listing for the same
# reason and says so; this is the same rule at the same kind of call.
gaps=$(mktemp) || exit 2
already=$(mktemp) || exit 2
trap 'rm -f "$gaps" "$already"' EXIT

if ! listing=$(python3 "$HERE/unsaid.py" "$EXPORT" "$repository" 2>"$gaps"); then
  refuse "could not work out what the tracker holds:" "$(tail -2 "$gaps")"
fi

if [[ -n "$only" ]]; then
  # THE WHOLE ID, NOT A PREFIX OF ONE. A substring match would let --only
  # tng-aaa act on inventory-tng-aaa, which is a different bead every time two
  # ids share a tail.
  listing=$(printf '%s\n' "$listing" | awk -F'\t' -v id="$only" '$1 == id') ||
    refuse "could not read the listing for $only."
  [[ -n "$listing" ]] ||
    refuse "$only has no issue of $repository, so there is nowhere to say anything."
fi

if [[ -z "$listing" ]]; then
  note "No bead has an issue on GitHub yet, so there is nothing to say."
  verdict "Nothing to say." speaking
fi

# === WHAT IS ALREADY THERE, ASKED ONCE FOR THE WHOLE REPOSITORY ===
#
# `/issues/comments` lists every comment on every issue, a hundred to a page, so
# this is about five requests. Asked per issue it is one request each -- 435
# round trips, well over a minute of waiting, on a script somebody runs and
# watches. Both stay inside the hourly budget; only one of them is worth
# sitting through.
#
# ONLY THE FIRST LINE IS KEPT, which is what makes the listing enough. That line
# is unsaid.py's marker and it carries a digest of the rest, so "does this
# comment already say what this run would say" is a comparison between two short
# tokens rather than between two bodies -- and the bodies never have to come
# back through the shell at all. `stamped` in unsaid.py says why that digest is
# not a security claim.
#
# `set -o pipefail` is what makes the `!` here read `gh`'s failure and not
# `jq`'s success over an empty stream, which is the direction that matters: an
# unreachable GitHub would otherwise look like a repository with no comments on
# it, and every issue would be written to again.
if ! gh api "repos/$repository/issues/comments" --paginate --slurp 2>"$gaps" |
  jq -r "[.[][] | select(.body | startswith(\"$MARKER\"))][]
         | \"\(.issue_url | split(\"/\") | last)\t\(.id)\t\(.body | split(\"\\n\") | first)\"" \
  >"$already" 2>>"$gaps"; then
  refuse "could not read the comments already on the issues:" "$(tail -2 "$gaps")"
fi

declare -A comment_id=() comment_line=()
while IFS=$'\t' read -r number found line; do
  [[ -z "$number" ]] && continue
  comment_id[$number]=$found
  comment_line[$number]=$line
done <"$already"

note "${#comment_id[@]} issue(s) already carry one of these."

# === AND HOW MANY OF THEM WOULD BE NEW, ASKED BEFORE ANY OF IT IS WRITTEN ===
#
# Counted from the comment map rather than by rendering, so this costs nothing:
# a bead that says something and whose issue carries no comment of ours is a
# creation, whatever the wording turns out to be.
creations=0
while IFS=$'\t' read -r _ number marker; do
  [[ -n "$marker" && -z "${comment_id[$number]:-}" ]] && creations=$((creations + 1))
done <<<"$listing"

if [[ "$dry_run" == false && "$confirm" == false && "$creations" -gt "$NEW_WITHOUT_ASKING" ]]; then
  fail "$creations issue(s) have no comment yet, which is a mirror rather than an update."
  note "Read one first, then let it write them:"
  note "  scripts/say-bead.sh --dry-run"
  note "  scripts/say-bead.sh --only <bead>"
  note "  scripts/say-bead.sh --confirm"
  note "Nothing was written. This says the same thing until somebody runs it."
  stop speaking
fi

written=0
removed=0
unchanged=0
while IFS=$'\t' read -r identifier number marker; do
  [[ -z "$identifier" ]] && continue
  existing=${comment_id[$number]:-}

  # AN EMPTY MARKER IS A BEAD WITH NOTHING TO SAY, which is unsaid.py's answer
  # rather than something inferred here from an empty rendering.
  if [[ -z "$marker" ]]; then
    [[ -n "$existing" ]] || continue
    if [[ "$dry_run" == true ]]; then
      note "would remove the comment on #$number ($identifier), which now says nothing"
      removed=$((removed + 1))
      continue
    fi
    if gh api -X DELETE "repos/$repository/issues/comments/$existing" >/dev/null 2>&1; then
      removed=$((removed + 1))
    else
      fail "the comment on #$number ($identifier) would not come down"
    fi
    sleep "$BETWEEN_WRITES"
    continue
  fi

  # NOTHING IS WRITTEN THAT WOULD NOT CHANGE ANYTHING, and it is the difference
  # between a pass that costs four hundred writes and one that costs the few
  # that moved. It also keeps the issue's timeline honest: an edit GitHub
  # records is one somebody may go and look at.
  #
  # ASKED BEFORE THE BODY IS COMPOSED, which is what keeps the ordinary pass
  # cheap: both sides of this comparison are lines the two listings already
  # handed over, so a bead that has not moved costs no interpreter at all.
  if [[ -n "$existing" && "${comment_line[$number]:-}" == "$marker" ]]; then
    unchanged=$((unchanged + 1))
    continue
  fi

  if [[ -n "$existing" ]]; then
    doing="rewrite the comment on"
    endpoint=(-X PATCH "repos/$repository/issues/comments/$existing")
  else
    doing="write a comment on"
    endpoint=(-X POST "repos/$repository/issues/$number/comments")
  fi

  if [[ "$dry_run" == true ]]; then
    note "would $doing #$number ($identifier)"
    written=$((written + 1))
    continue
  fi

  if ! body=$(python3 "$HERE/unsaid.py" "$EXPORT" "$repository" "$identifier" 2>"$gaps"); then
    fail "$identifier could not be rendered: $(tail -1 "$gaps")"
    continue
  fi

  if posted=$(gh api "${endpoint[@]}" -f body="$body" 2>&1); then
    written=$((written + 1))
  else
    fail "#$number ($identifier) would not take the comment: $(printf '%s' "$posted" | tail -1)"
  fi
  sleep "$BETWEEN_WRITES"
done <<<"$listing"

note "$written written, $removed removed, $unchanged already saying it."

if [[ "$dry_run" == true ]]; then
  verdict "Nothing written: this was a dry run." speaking
fi

verdict "Every issue says what its bead holds." speaking
