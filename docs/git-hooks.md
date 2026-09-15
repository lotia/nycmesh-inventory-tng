# The git hooks

What runs when you commit, check out, pull or push in this repository; what
each of it can refuse; and how to get past it when you need to.

The short version: **one hook refuses anything**, `commit-msg`, and the rule it
enforces is checked again on every pull request by CI. Everything else is
beads' own plumbing and refuses nothing of its own.

## What runs

The table below is not typed. `scripts/hooks-doc.sh` renders it from the hooks
directory and from each checker's own account of itself, and CI fails when the
page differs from that render — so a hook added, renamed or changed reddens the
build until somebody runs the script again. It cannot describe hooks that are
no longer there, or numbers a checker no longer enforces.

<!-- hooks-doc: begin -->
<!-- Rendered by scripts/hooks-doc.sh from .beads/hooks. Do not edit between the markers; run the script. -->
| Hook | What runs | What it does, and what it refuses |
| --- | --- | --- |
| `pre-commit` | beads shim v1.0.5: `bd hooks run pre-commit` | Never refuses on its own: hands off to beads, which runs any hooks it was told to chain before a commit, and passes the exit status of `bd` through. |
| `prepare-commit-msg` | beads shim v1.0.5: `bd hooks run prepare-commit-msg` | Never refuses on its own: hands off to beads, which adds an agent-identity trailer to the message for forensics, and passes the exit status of `bd` through. |
| `commit-msg` | [`scripts/check-commit.sh`](../scripts/check-commit.sh) | Refuses a commit whose message breaks the rules in DEVELOPERS.md "Commits" -- a summary over 50 characters after its issue prefix, a body line over 72 columns, or trailers that do not name exactly one issue -- or whose staged tracker closes more than one issue, or a different one from the message's. |
| `pre-push` | beads shim v1.0.5: `bd hooks run pre-push` | Never refuses on its own: hands off to beads, which runs any hooks it was told to chain before a push, and passes the exit status of `bd` through. |
| `post-checkout` | beads shim v1.0.5: `bd hooks run post-checkout` | Never refuses on its own: hands off to beads, which runs any hooks it was told to chain after a checkout, and passes the exit status of `bd` through. |
| `post-merge` | beads shim v1.0.5: `bd hooks run post-merge` | Never refuses on its own: hands off to beads, which runs any hooks it was told to chain after a pull or merge, and passes the exit status of `bd` through. |
<!-- hooks-doc: end -->

`.beads/hooks` is where they live, and `core.hooksPath` is what points git at
it. That directory is beads' own and holds its five shims; a second directory
is not an option, because `core.hooksPath` is one path and beads' git
integration goes quiet the moment it names anywhere else. So one pointer arms
all six: the commit checker this repository wrote, and beads' five, which cost
a commit, a checkout and a pull each a fraction of a second more than they did
— `inventory-tng-hn6w` is where what that is worth gets decided.

Anything you keep in git's default `.git/hooks` stops running once that pointer
is set — `pre-commit`, husky, a hook of your own — so move what you want kept
into `.beads/hooks`. Bootstrap says so when it finds any, rather than switching
them off quietly.

## How they get there

`.beads/hooks/commit-msg` is a symlink committed to the repository, so every
clone has it and it follows `scripts/check-commit.sh` wherever that goes.
`core.hooksPath` is written into `.git/config` by
[bootstrap](../DEVELOPERS.md#clone-and-bootstrap), and no clone copies that
file — which is why bootstrap is a step and not a default.

Two ways a hook can be absent, both of which say so rather than passing
silently. A clone that never ran bootstrap has the hook and no
`core.hooksPath`, and `scripts/check-setup.sh` tells you which of the two is
missing. A checkout that lost the link fails CI, where the same script runs as
`--shipped-only` — its header says why the halves are split and which one a
runner can be asked.

A hook inherits whatever `PATH` invoked it rather than an activated shell's, and
the commit checker reads the tracker through `python3`. So a perfectly wired
clone can still refuse every commit that stages the tracker; the refusal names
the program rather than blaming the commit.

## Skipping them

You can. The rules `commit-msg` enforces are enforced again on every pull
request — CI's `One issue per commit` job runs `scripts/check-batch.sh` over
the whole range — so a hook skipped locally moves a refusal to the pull
request; it never gets past it. The local hook is there to tell you sooner,
not to be the gate. Skip it when it is in your way, and expect the same
answer from CI if the message was wrong.

git's own switches are the ones to use; there is nothing of this repository's
to learn:

```bash
git commit --no-verify                    # this commit: no pre-commit, no commit-msg
git push --no-verify                      # this push: no pre-push
git -c core.hooksPath=/dev/null <command> # this command: no hooks at all
git config --unset core.hooksPath         # this clone: no hooks, until bootstrap runs again
```

The last one is undone by `scripts/bootstrap-dev.sh`, which sets the pointer
whenever it finds it unset, and `scripts/check-setup.sh` will report the clone
as unwired in the meantime. `BEADS_HOOK_TIMEOUT` (seconds, default 300) bounds
how long any of beads' shims may take before git carries on without it.

**If you are an agent, the rule is different**, and [AGENTS.md](../AGENTS.md#git)
says so and says why: never `--no-verify`. An agent that learns the switch will
reach for it in place of the fix. A person reading a refusal is trusted to know
which is which.

## What the Claude Code hooks are not

`scripts/landing-gate.sh` is registered in `.claude/settings.json` as a Claude
Code hook, not a git one. It runs when an agent session is about to type a
guarded command, and never when a person does. If you are in a shell, the table
above is the whole of what runs.
