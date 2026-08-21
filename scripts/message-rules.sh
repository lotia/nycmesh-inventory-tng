#!/usr/bin/env bash
# What a commit message must be, as functions rather than as a program.
#
# Sourced, never run. check-commit.sh is the command-line interface over this
# file and check-batch.sh calls the same functions once per commit in the range,
# so the two enforce identical text by sharing the code rather than by one
# reading the other's output. Before this existed, check-batch.sh forked
# check-commit.sh per commit and grepped the failure glyph out of its stdout,
# so the two agreed only for as long as one kept parsing the other's prose.
#
# Source it after report.sh and trailers.sh, both of which this file calls, and
# resolve the path the way check-commit.sh does -- through readlink -f, because
# DEVELOPERS.md installs that script as a symlink and "beside me" is otherwise
# the hooks directory. Then:
#
#   message_rules "$(cat message.txt)"
#
# Every objection goes through report.sh's fail and note, so a caller that
# wants them attributed sets REPORT_PREFIX rather than reformatting text.
#
# Reads nothing but the message. Whether the staged tracker agrees with the
# trailer is check-commit.sh's business, because only it has an index to look
# at; MESSAGE_TRAILER_ISSUE, MESSAGE_CLOSES_COUNT and MESSAGE_TRAILER_COUNT are
# set for it, so it asks what this file already worked out rather than parsing
# the message a second time.

SUMMARY_LIMIT=50
BODY_LIMIT=72

# Set by message_rules for a caller that needs them afterwards.
MESSAGE_TRAILER_ISSUE=""
MESSAGE_CLOSES_COUNT=0
MESSAGE_TRAILER_COUNT=0

# message_is_git_own <summary> — a merge, revert or cherry-pick, whose message
# git writes itself and which is nobody's issue being landed.
message_is_git_own() {
  [[ "$1" =~ ^(Merge|Revert)\  ]]
}

# message_rules <message text> — everything that can be judged from the message
# alone. Sets MESSAGE_TRAILER_ISSUE, MESSAGE_CLOSES_COUNT and
# MESSAGE_TRAILER_COUNT for the caller.
message_rules() {
  local text=$1
  # Comments are git's own and never reach the stored message. Dropped here
  # rather than by each caller: check-commit.sh reads a file that still has
  # them and check-batch.sh reads %B that never did, so a rule applied before
  # this line would give the two opposite verdicts on the same commit -- which
  # is the divergence this file exists to remove.
  text=$(printf '%s\n' "$text" | grep -v '^#')
  # Trailing blank lines are how the message was stored, not something the
  # author wrote: git's %B ends in a newline and a file ends in one too. Left
  # on, they make the trailers stop being the last paragraph.
  while [[ "$text" == *$'\n' ]]; do text=${text%$'\n'}; done
  local -a lines trailers closing wide
  mapfile -t lines <<<"$text"
  local summary=${lines[0]:-}

  if [[ -z "$summary" ]]; then
    fail "the summary line is empty"
  fi

  # The summary names its issue and then describes the change. The limit is on
  # the description: the identifier is addressing, not prose, and charging the
  # line for it would shorten every summary in the repository to pay for
  # something the reader gains nothing from reading.
  #
  # Only the distinguishing part appears here -- "c6j.6", not
  # "inventory-tng-c6j.6" -- because the repository prefix is the same on every
  # bead and would spend 14 of the 50 characters saying so. The trailer carries
  # the full identifier, which is what a machine reads.
  #
  # What makes it an identifier is that the trailer agrees: a prefix is only
  # waved through if the issue this commit belongs to actually ends with it.
  # Anything else -- a "Fix:" habit, a colon that happens to fall early -- is
  # prose and is charged for. Guessing from the shape of the token instead does
  # not work, because a bead identifier is arbitrary and need not contain a
  # digit: this repository has swr and jro as well as c6j and 2dg.
  mapfile -t trailers < <(trailers_of "$text")
  closing=()
  local uncolonned="" line trailer
  for line in ${trailers+"${trailers[@]}"}; do
    [[ "$line" == Closes* ]] && closing+=("$line")
    parses_as_trailer "$line" || [[ -n "$uncolonned" ]] || uncolonned=$line
  done

  local trailer_issue=""
  [[ ${#trailers[@]} -gt 0 ]] && trailer_issue=$(issue_of "${trailers[0]}")

  local prose=$summary
  if [[ "$summary" =~ ^([^[:space:]]+):[[:space:]](.*)$ ]]; then
    # *"$marker" covers the exact match too: [[ abc == *abc ]] is true.
    [[ -n "$trailer_issue" && "$trailer_issue" == *"${BASH_REMATCH[1]}" ]] &&
      prose=${BASH_REMATCH[2]}
  fi

  if [[ ${#prose} -gt $SUMMARY_LIMIT ]]; then
    fail "the summary is ${#prose} characters, over $SUMMARY_LIMIT"
    note "  usually the issue was too big rather than the line too short"
  fi

  if [[ "$summary" == *. ]]; then
    fail "the summary line ends in a full stop"
  fi

  # Imperative mood, as far as a machine can tell: the past tense and the gerund
  # are what gets written instead. The exceptions are imperatives that simply
  # end that way; extend the list when a real commit trips it.
  local first=${prose%% *}
  # Restored rather than cleared: this is a library, and a caller that had
  # nocasematch set would silently lose it from here on.
  local _nocase; _nocase=$(shopt -p nocasematch)
  shopt -s nocasematch
  if [[ "$first" =~ (ed|ing)$ ]] &&
    [[ ! "$first" =~ ^(Bring|Embed|Exceed|Feed|Proceed|Read|Seed|Shed|Speed|Spread|Succeed)$ ]]; then
    fail "\"$first\" is not the imperative: write \"Extract\", not \"Extracted\" or \"Extracting\""
  fi
  eval "$_nocase"

  if [[ ${#lines[@]} -gt 1 && -n "${lines[1]}" ]]; then
    fail "the line after the summary must be blank"
  fi

  # Everything after the summary, rather than everything after the blank line:
  # a message that forgot the blank line still has a body, and it is still too
  # wide.
  # Every one of them, not the first. Stopping at one meant a message with three
  # long lines took three runs to fix, and the second and third were written
  # against a report that named neither -- which is how a line over the limit
  # reached this repository's history more than once.
  wide=()
  for line in "${lines[@]:1}"; do
    # A line with nowhere to break -- a long URL, pasted output -- is left alone.
    if [[ ${#line} -gt $BODY_LIMIT && "$line" == *" "* ]]; then
      wide+=("${#line}: ${line:0:44}...")
    fi
  done

  if [[ ${#wide[@]} -gt 0 ]]; then
    if [[ ${#wide[@]} -eq 1 ]]; then
      fail "one body line is over $BODY_LIMIT characters:"
    else
      fail "${#wide[@]} body lines are over $BODY_LIMIT characters:"
    fi
    for line in "${wide[@]}"; do
      note "  $line"
    done
  fi

  # Every trailer names an issue, and they all name the same one. That is what
  # "one issue per commit" reduces to in a message: an issue may take more than
  # one commit, so `Refs` exists for the ones that advance it without finishing
  # it, but no commit may name two issues whatever the verb.
  if [[ ${#trailers[@]} -gt 0 ]] && ! trailers_are_last "$text"; then
    fail "the trailers are not the last paragraph, so git reads them as prose"
    note "  put them alone at the end, after a blank line"
  fi

  if [[ -n "$uncolonned" ]]; then
    fail "\"$uncolonned\" is not a trailer git can read: write \"${uncolonned%% *}: ${uncolonned#* }\""
    note "  git parses Key: value, so without the colon %(trailers) finds nothing"
  fi

  if [[ ${#trailers[@]} -eq 0 ]]; then
    fail "expected a 'Closes: <issue>' or 'Refs: <issue>' trailer, found none"
  elif [[ ${#closing[@]} -gt 1 ]]; then
    fail "${#closing[@]} 'Closes' trailers. One issue, one commit."
  else
    for trailer in "${trailers[@]:1}"; do
      local other
      other=$(issue_of "$trailer")
      if [[ "$other" != "$trailer_issue" ]]; then
        fail "the trailers name $trailer_issue and $other. A commit belongs to one issue."
        break
      fi
    done
  fi

  MESSAGE_TRAILER_ISSUE=$trailer_issue
  MESSAGE_CLOSES_COUNT=${#closing[@]}
  MESSAGE_TRAILER_COUNT=${#trailers[@]}
}
