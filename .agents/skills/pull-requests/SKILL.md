---
name: pull-requests
description: Use when running a batch through review - opening the pull request, taking a review or simplify pass against it, attributing findings to issues, and merging. Covers the part where findings from one review have to become commits that each belong to one issue.
---

# Running a batch through review

Read [Pull requests](../../../DEVELOPERS.md#pull-requests) first: it is where
the rules are. This file is the procedure for keeping them, and the part that
actually goes wrong is triage — one review pass produces findings that do not
respect the boundaries between issues, and they have to be sorted before
anything is fixed.

Load it when a batch is ready to be reviewed, not while you are still building
it. While you are building it, the skill you want is
[commits](../commits/SKILL.md).

## Before you ask for review

```bash
gh pr checks --watch          # green, on the head that will be reviewed
bd list --parent=<epic>       # every issue in the batch is closed
git log --oneline main..HEAD  # one commit per issue, none of them mixed
```

Publish into the draft as each issue lands, and mark ready only when the batch
is complete and green.

```bash
gh pr ready
```

## Triage before you fix anything

```bash
/code-review <pr> --comment
```

One pass over the whole batch. Now resist the urge to start fixing: the findings
arrive in the order the reviewer noticed them, and applying them in that order
is how a commit ends up holding two issues.

Sort every finding first, by the table in
[One review pass](../../../DEVELOPERS.md#one-review-pass-findings-filed-per-issue).
Write the buckets down — the comment IDs under each issue — before touching the
tree. The question to ask of each finding is not "what is this about?" but
**"which single commit would I revert to make this go away?"**

The third row of that table is the one people get wrong. A finding that needs
code from two issues changed is not a reason to widen a commit; it is a new
issue:

```bash
bd create --parent=<epic> --type=bug --title="..."
```

Creating one is not an admission of failure. The composition genuinely is work
that neither issue did alone, and
[0017](../../../docs/decisions/0017-review-through-pull-requests.md) says why it
gets an issue rather than a wider commit.

## Fix one issue at a time

For each bucket, in the order the issues were landed:

```bash
# only that issue's findings in the tree
<gates for what you touched>
git commit --fixup=<that issue's commit>
git push
```

Then answer the findings where they were made, so the record stays in one place:

```bash
gh pr comment <pr> --body "..."        # or reply to the thread and resolve it
```

Do not start the next bucket with the previous one uncommitted. The whole point
of triage was to keep them apart, and a shared working tree undoes it.

## Then simplify, the same way

```bash
/simplify
```

`/simplify` has no pull request target and posts nothing itself, so post what it
found before applying any of it — otherwise the findings exist only in this
session and the pull request records fixes nobody can trace:

```bash
gh pr comment <pr> --body "$(cat findings.md)"
```

Triage identically. Expect most of it to be the third row: "these three issues
each grew the same helper" is a finding no one issue owns, and the extraction is
its own piece of work.

## Merging

Only once every thread is resolved — see
[Merging](../../../DEVELOPERS.md#merging) for why the order matters:

```bash
git rebase -i --autosquash origin/main
scripts/check-batch.sh origin/main..HEAD
git push --force-with-lease
gh pr checks --watch
.claude/hooks/landing-gate.sh record <pr>    # if the local gate is installed
gh pr merge <pr> --rebase
```

The merge does not ask. The bar it has to clear is
[When a branch is ready to merge](../../../DEVELOPERS.md#when-a-branch-is-ready-to-merge),
and none of it is yours to weigh.

Two things about that `record` line, because both are easy to get wrong. It
records the head it saw and nothing else — it does not check that either pass
ran, so it is a reminder and you are still the one answerable for the cycle.
And anything pushed afterwards moves the head, so the merge is refused until
you record again: when a late fix means another `fixup!`, the way back in is
the whole block above, from the rebase down.
