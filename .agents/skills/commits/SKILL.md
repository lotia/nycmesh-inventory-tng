---
name: commits
description: Use when landing work in this repository - staging, writing or rewording a commit message, splitting a change that spans two issues, or preparing a branch to push. Covers what one commit may hold and how its message is written.
---

# Committing

Read [Commits](../../../DEVELOPERS.md#commits) first: it is where the rules are,
and they are short. This file is the procedure for keeping them, which is the
part that actually goes wrong. Load it when you are about to land something, not
while you are still working.

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
it belongs to the issue in hand. It is how the unrelated fix gets in.

## Checking

Write the message to a file first and pass it, so the check sees what will land
rather than the last message you wrote:

```bash
scripts/check-commit.sh <message-file>
```

Pass `--amend` when you are replacing the last commit rather than adding one;
`check-commit.sh` says at the top of itself what that changes.

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
[Definition of Done](../../../DEVELOPERS.md#definition-of-done) against it
*here*: that is the point of splitting, and a commit that only passes because of
the work you stashed is not a commit that stands alone. Then land it, restore
the rest with `git stash pop`, and repeat.

## The message

The rule is in [Commits](../../../DEVELOPERS.md#commits). What it looks like
kept, and not kept:

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

The second is this repository's own history, and is why this file exists: 61
characters, not imperative, and it opens with the reasoning rather than the
change. The pull towards it is strong at exactly the wrong moment — everything
you just learned is fresh, and none of it is what a reader of the history
needs.
