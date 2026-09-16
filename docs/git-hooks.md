# The git hooks

What runs when you commit, check out, pull or push in this repository; what
each of it can refuse; and how to get past it when you need to.

The short version: in a shell, only `commit-msg` refuses anything, and the
rules it applies to the message are checked again on every pull request by CI.
The rest of the git hooks are beads' own plumbing. An agent session meets two
more, which are not git hooks and are [at the end](#the-claude-code-hooks).

## What runs

The table below is not typed: `scripts/hooks-doc.sh` writes it, from the hooks
directory and from what each checker says about itself, and CI checks the page
against a fresh render. The script's header says why.

<!-- hooks-doc: begin -->
<!-- Rendered by scripts/hooks-doc.sh from .beads/hooks. Do not edit between the markers; run the script. -->
| Hook | What runs | What it does, and what it refuses |
| --- | --- | --- |
| `pre-commit` | beads shim v1.0.5: `bd hooks run pre-commit` | Never refuses on its own: hands off to beads, which runs any hooks it was told to chain before a commit, and passes the exit status of `bd` through. |
| `prepare-commit-msg` | beads shim v1.0.5: `bd hooks run prepare-commit-msg` | Never refuses on its own: hands off to beads, which adds an agent-identity trailer to the message for forensics, and passes the exit status of `bd` through. |
| `commit-msg` | [`scripts/check-commit.sh`](../scripts/check-commit.sh) | Refuses a commit whose message breaks the rules in docs/commits.md -- a summary over 50 characters after its issue prefix, a body line over 72 columns, or trailers that do not name exactly one issue -- or whose staged tracker closes more than one issue, or a different one from the message's. |
| `pre-push` | beads shim v1.0.5: `bd hooks run pre-push` | Never refuses on its own: hands off to beads, which runs any hooks it was told to chain before a push, and passes the exit status of `bd` through. |
| `post-checkout` | beads shim v1.0.5: `bd hooks run post-checkout` | Never refuses on its own: hands off to beads, which runs any hooks it was told to chain after a checkout, and passes the exit status of `bd` through. |
| `post-merge` | beads shim v1.0.5: `bd hooks run post-merge` | Never refuses on its own: hands off to beads, which runs any hooks it was told to chain after a pull or merge, and passes the exit status of `bd` through. |
<!-- hooks-doc: end -->

`commit-msg` is the one to know. How it reads the staged tracker, how it
recognises an amend and what it cannot tell from one message, and the way to
reword a summary it refuses, are
[Checking it](commits.md#checking-it); the rules it applies are
[Commits](commits.md).

`.beads/hooks` is where they live, and `core.hooksPath` is what points git at
it. That directory is beads' own and holds its shims; a second directory is not
an option, because `core.hooksPath` is one path and beads' git integration goes
quiet the moment it names anywhere else. So one pointer arms all of them: the
commit checker this repository wrote, and beads' own.

Beads' shims stay armed, and that is a decision rather than an accident
(`inventory-tng-hn6w`): beads is a core part of how this project is worked,
and its hooks are not something to undo. What they cost is measured there and
judged worth it. What is bounded is how long one may *hang*: `mise.toml` sets
`BEADS_HOOK_TIMEOUT` for any shell that has activated mise here, and its
comment says why that value.

Anything you keep in git's default `.git/hooks` stops running once that pointer
is set — `pre-commit`, husky, a hook of your own — so move what you want kept
into `.beads/hooks`. Bootstrap says so when it finds any, rather than switching
them off quietly.

## How they get there

The hook is committed and the pointer is not:
[bootstrap](../DEVELOPERS.md#clone-and-bootstrap) writes `core.hooksPath`, and
`scripts/check-setup.sh` reports a clone where either half is missing — its
header is where the two halves are explained, and why CI can only ask about
one of them.

A hook inherits whatever `PATH` invoked it rather than an activated shell's, and
the commit checker reads the tracker through `python3`. So a perfectly wired
clone can still refuse every commit that stages the tracker; the refusal names
the program rather than blaming the commit.

## Skipping them

You can. The rules `commit-msg` applies to the message are enforced again on
every pull request — CI's `One issue per commit` job runs
`scripts/check-batch.sh` over the whole range — so a hook skipped locally
moves that refusal to the pull request; it never gets past it. The local hook
is there to tell you sooner, not to be the gate. Skip it when it is in your
way, and expect the same answer from CI if the message was wrong.

The other half of what it checks is the hook's alone: that the staged tracker
closes the issue the message names, and no other. That reads a staged diff,
which a commit that has already landed no longer has, so CI does not repeat
it; what CI reads instead is whether every issue the branch closes belongs to
the batch. So a commit that stages `.beads/issues.jsonl` is the one kind to
skip the hook on with some care — a closure the message does not name is what
it would have caught, and nothing later reads the two side by side.

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
as unwired in the meantime.

**If you are an agent, the rule is different**, and [AGENTS.md](../AGENTS.md#git)
says so and says why: never `--no-verify`. An agent that learns the switch will
reach for it in place of the fix. A person reading a refusal is trusted to know
which is which.

## The Claude Code hooks

These are not git hooks. `.claude/settings.json` registers them with Claude
Code, and they run inside an agent session: once before every shell command the
agent is about to type, and once when it tries to end its turn. A person in a
shell never meets them, and if you are one, the table above is the whole of
what runs for you.

They exist to hold an agent to the same bar as a person without asking anybody:
the argument, and what the gate does and does not claim to cover, is
[When a branch is ready to merge](pull-requests.md#when-a-branch-is-ready-to-merge).
What follows is rendered the same way as the table above, from the settings
file and from the script's own account of itself.

<!-- claude-hooks: begin -->
<!-- Rendered by scripts/hooks-doc.sh from .claude/settings.json and the scripts it names. Do not edit between the markers; run the script. -->
| Event | Only for | Command | Timeout |
| --- | --- | --- | --- |
| `PreToolUse` | Bash | `"$CLAUDE_PROJECT_DIR"/scripts/landing-gate.sh check` | 30s |
| `Stop` | every one | `"$CLAUDE_PROJECT_DIR"/scripts/landing-gate.sh stop` | 30s |

What [`scripts/landing-gate.sh`](../scripts/landing-gate.sh) refuses, in its own words:

| On | What it refuses, or asks |
| --- | --- |
| ending a turn | Blocks the turn once, when the current branch's pull request is ready and green and its review cycle has not been recorded against this head -- a batch that looks finished and is not. |
| bd dolt push, bd sync, bd federation sync | Refused: it publishes the issue tracker, and this repository is public (decision 0029). A person reads what is about to become public and runs it. |
| scripts/repo-settings.sh without --check; gh api writing branch protection or repository settings | Refused: it writes the protections every other refusal here relies on. --check compares and is free; writing is a person's to authorise. |
| git commit, in the shared checkout | Refused: that checkout is the person's, git status there lists what anybody did, and even a path-named add stages a colleague's hunk in that file. Work in a worktree of your own -- the EnterWorktree tool -- where a commit is free. |
| git push --force, -f | Refused: use --force-with-lease, which refuses if the remote moved since you fetched. Breaking a lease is a person's call. |
| git push to main | Refused before GitHub gets to, so the refusal names the batch/* workflow rather than a protection rule. |
| gh pr ready | Refused while any check other than Review cycle and Repository settings is not green, and refused differently for a pull request whose body posts the do-not-merge marker. |
| gh pr merge or gh pr ready naming the pull request by URL or branch | Refused: the gate keys receipts and heads by number, and read as naming none such a command was judged against the checked-out branch's pull request. Name it by number. |
| gh pr merge --repo pointing at another repository | Refused: the receipts are keyed by pull request number within this repository, so a cycle recorded for #7 here cannot vouch for #7 anywhere else. Run it from a checkout of that repository. |
| gh pr merge, and the API spellings of it | Refused unless the pull request does not post the do-not-merge marker, its review cycle is recorded against the exact head being merged, its branch is checked out somewhere in this repository at that head, and check-batch.sh is clean over the range there. |
<!-- claude-hooks: end -->
