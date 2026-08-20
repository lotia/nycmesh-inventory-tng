# 0017 — Review happens in the pull request, not in the history

**Status:** accepted

## Context

Work here arrives in batches: several issues are finished, then reviewed
together, then simplified, then landed. Until now the batch was landed as a
single commit. The history says so:

```
8d881a7 Five beads, one review
9e2e86a Administrative powers in the app, and invariants below the API
d3d8d84 Catalogue write API, /api/me, and the index's real contract
```

Each of those is five issues. That costs three things at once. `git bisect`
cannot land between them, so a regression is attributed to five issues rather
than one. Reverting one means reverting all five. And the summary line is not a
summary — 61 characters listing two unrelated subjects — because
[Commits](../../DEVELOPERS.md#commits) asks for a 50-character description of
one thing and it was being handed five.

The review was the reason. A batch is reviewed once, so the fixes it produced
were spread across whatever the review found, and separating them afterwards
was harder than collapsing the lot. The findings themselves then had nowhere to
go but the commit message, which is the wrong place: a commit message is read by
someone tracing a change years later, and what a reviewer said in the week it
was written is noise to them.

## Decision

**Move the review onto a GitHub pull request. The batch becomes the branch, the
issue stays the commit, and the review commentary stays in the pull request.**

The full procedure is [Pull requests](../../DEVELOPERS.md#pull-requests); this
records why it is shaped the way it is.

### Finished and published before reviewed

An issue passes its own checks, is committed, and is pushed before anything is
reviewed. Reviewing work that does not yet build wastes the review on things a
linter would have said, and it means the reviewed content and the landed content
are not provably the same.

### One review pass, findings attributed one issue at a time

The batch is reviewed in one pass, because a reviewer holding the whole change
in their head is the point of batching. But the fixes are applied one issue at a
time, so no commit ends up holding two.

The awkward case is a finding that only exists because two issues met. Widening
a commit to hold it would break the rule this decision exists to enforce, so it
becomes a **new issue in the same batch** instead. Integration work is work; a
commit of its own keeps it revertible on its own, and names it honestly rather
than hiding it inside whichever issue happened to be edited last.

### Rebase merge only

Squash merge and merge commits are disabled on the repository, not merely
discouraged. The rule is that no commit may hold two issues, and a discouraged
button is one misclick from breaking it — GitHub's squash merge collapses an
entire pull request, which is precisely the forbidden operation offered as a
default. Rebase merge replays each commit separately, which is both what is
wanted and what matches the existing linear history.

Collapsing several commits belonging to **one** issue is still allowed, because
it does not violate anything; it happens on the branch with `--autosquash`
before merging, and after every review thread is resolved so the review stays
anchored to commits that still exist.

### The identifier moves into the summary line

A summary that names its issue lets `git log --oneline` answer "what did this
issue do?" without reading trailers. The full bead identifier costs 19
characters of a 50-character line, and this repository's own summaries run to 43
— so only the distinguishing part is used, and the 50 is measured on the prose
after it. The size check the limit exists for is unchanged; the 14-character
repository prefix it would otherwise have spent is information the reader
already has.

## Consequences

- `main` is protected and accepts nothing except a rebase merge of a green,
  fully-resolved pull request. That binds administrators too; there is no
  bypass, deliberately.
- A batch of one issue needs no epic and no ceremony. The epic exists only to
  record which issues belong to a pull request, which is a fact worth checking
  and not worth inventing when there is only one.
- History gains commits. That is the intended trade: five commits that can each
  be reverted are worth more than one that reads tidily.
- The batch that introduced this landed through it. Only the automated checks
  bootstrap awkwardly: `check-batch.sh` could not verify the batch that wrote
  it, so that one was read by hand. Nothing about the procedure itself needed
  the checkers to exist first, which is the useful thing this proved.
