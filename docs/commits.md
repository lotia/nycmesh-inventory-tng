# Commits

What one commit holds, how its message is written, how to check it before it
lands, and how to keep to that when the work in your tree has already bled
across two issues.

**One issue per commit.** A commit contains work from exactly one issue and
nothing else, so that it can be read, reviewed, reverted and bisected as the
unit of work it claims to be. An issue may take more than one commit where that
genuinely reads better; no commit may ever take more than one issue.

That rule settles the awkward cases too:

- Documentation the change itself made wrong is part of the change — that is
  the [Definition of Done](../CONTRIBUTING.md#definition-of-done), not a separate concern.
- A fault you noticed on the way but did not cause is its own issue and its own
  commit, however small and however tempting. A one-line fix riding along is
  the commonest way a commit stops being one thing.
- A defect a review finds in the change is part of the change. A defect it
  finds in code the change did not touch is not.

## The message

```
abc: Summarise the change in the imperative

What changed, what was added, what was removed — in enough detail that
somebody reading the history a year from now knows what this did to the
repository, and no more.

Closes: inventory-tng-abc
```

- **The summary line names its issue, then says what changed in at most 50
  characters**, in the imperative mood ("Extract the decode loop", not
  "Extracted" or "Extracting"), with no full stop. The 50 is measured on the
  prose after the `abc: ` prefix, because it is a size check as much as a
  title: work that cannot be summarised in 50 characters is usually more than
  one issue, and the answer is to split the issue rather than to lengthen the
  line. Only the distinguishing part of a bead ID is used, for the reason
  [0017](decisions/0017-review-through-pull-requests.md) gives.
- **The body says what changed**, wrapped at 72 columns. It is not a diary:
  how the work was done, what was tried first and what a review said are not
  what a reader of the history needs. A review's findings belong in the
  [pull request](pull-requests.md). *Why* something is built the way it is
  belongs in [docs/decisions/](decisions/), and is linked rather than
  retold.
- **A trailer naming that same issue in full** — `Closes: inventory-tng-abc` on
  the commit that completes it, `Refs: inventory-tng-abc` on one that only
  advances it. The colon is not decoration: git parses `Key: value` and nothing
  else, so without it `git log --format='%(trailers)'` finds nothing and only a
  bespoke script can answer "what did this issue do?". GitHub accepts the colon
  for its own closing keywords, so `Closes: #123` names a GitHub issue, because
  [beads is not required to contribute](issue-tracking.md). Every trailer on a
  message names the *same* issue, and at most one closes it; that is what makes
  "one issue per commit" something a machine can check. Follow-up issues raised
  along the way may be created in the same commit — noticing work is honest
  work — but only one issue may be *closed* by it. An epic does not count: it
  groups a batch and does no work of its own, so it finishes when its children
  do and its closure rides with the last of them.

What it looks like kept, and not kept:

```
Extract the decode loop into its own module

The 5 Hz camera loop moves from CameraScanner.tsx to decodeLoop.ts, where
it takes an injected detector and frame source and owns its own stop. The
component keeps the wiring. Adds decodeLoop.test.ts; removes the
unreachable release() after the loop was started.

Closes: inventory-tng-w1e
```

```
The decode loop, out where it can be tested

CameraScanner.tsx sat at 68% lines while the rest of the frontend was
95-100%, on the judgement that covering it meant faking a camera. That is
right about the decode and wrong about the lifecycle...
```

The second is this repository's own history, and is why this page exists: 61
characters, not imperative, and it opens with the reasoning rather than the
change. The pull towards it is strong at exactly the wrong moment — everything
you just learned is fresh, and none of it is what a reader of the history
needs.

## Several issues at once

Work them one at a time on a batch branch and land each as it is finished. The
pull request is the unit of review; the commit stays the unit of work. See
[Pull requests](pull-requests.md).

## Before you stage

```bash
bd list --status=in_progress    # exactly one, and it is the one you are landing
git status                      # every path below belongs to that issue
```

If a second issue is in progress, you are about to write a commit that cannot
honestly close either. Finish and land one first.

Anything in `git status` that the issue did not cause is a `bd create`, not a
passenger. Do it now, while you still remember what you noticed.

## Staging

Stage by path, deliberately:

```bash
git add <the paths that issue touched>
```

`git add -A` is only safe when you have just read `git status` and every line of
it belongs to the issue in hand. It is how the unrelated fix gets in — and on
a checkout shared between sessions, where `git status` lists what anybody did,
the landing gate refuses it, and `git commit -a` with it. A worktree of your
own has only your edits in it, and there a sweep is free:

```bash
git worktree add .claude/worktrees/<name> <branch>   # the directory is ignored
```

## When work has already bled across two issues

This is the common case, because a review of one issue finds something in
another, and because a fix noticed on the way is a fix you already made. Do not
land it as one commit and apologise in the message. Split it:

```bash
git reset                                          # unstage everything
git add <paths for the first issue>                # git add -p for a file carrying both
git stash push --keep-index --include-untracked    # park the rest out of the way
```

The working tree is now that issue and nothing else. Work the
[Definition of Done](../CONTRIBUTING.md#definition-of-done) against it
*here*: that is the point of splitting, and a commit that only passes because of
the work you stashed is not a commit that stands alone. Then land it, restore
the rest with `git stash pop`, and repeat.

## Checking it

Write the message to a file and hand it over, so that what is checked is what
will land:

```bash
scripts/check-commit.sh <message-file>
scripts/check-commit.sh --amend <message-file>   # replacing the last one
```

It objects if more than one issue is closed by what is staged, if the message
and the tracker disagree about which, or if the summary line breaks the rules
above. A guardrail rather than a gate.

**Amending is recognised, within one limit.** The flag above is for running it
by hand; as a hook it is told nothing about how git was invoked, so it works the
shape out instead — `HEAD` already closed the issue the message names, *and* the
summary line is still `HEAD`'s. Both, because either alone would also describe a
fresh commit claiming a closure that the one before it made. So amending to
revise a body, or to fold in work you forgot to stage, passes; amending to
rewrite the summary is refused, and so is a reword during a rebase.

That pair narrows the shape; it does not pin it down. A brand new commit whose
subject repeats `HEAD`'s word for word, closing nothing itself, is read as an
amend and accepted — and nothing given to a hook run against one message could
decide otherwise. `scripts/check-batch.sh` is what covers it, by reading the
whole range instead: an issue closed twice is an objection there, and a branch
carrying one does not merge.

To change a summary, reach for `git commit --fixup=reword:<commit>`, which puts
the new one in an `amend!` for `git rebase --autosquash` to fold in. The
`reword:` is the part that matters — a plain `--fixup` throws its own message
away and the old summary survives the fold.

It also runs on every commit you make, as the `commit-msg` hook. Which hooks
run, what each can refuse and how they come to be installed is
[docs/git-hooks.md](git-hooks.md).

It can be skipped, and that page says how and why that is safe.

History before this page predates it, and is not the example to follow:
several commits close five issues each.

