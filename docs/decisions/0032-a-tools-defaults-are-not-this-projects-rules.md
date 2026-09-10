# 0032 — A tool's defaults are not this project's rules

**Status:** accepted

## Context

This record was not written because somebody had an idea. It was written
because a count reached four.

`inventory-tng-ljj1` was opened on 2026-09-10 as a tally rather than a
proposal: occasions where a tool's defaults quietly disagreed with something
this repository had already decided, found only because somebody looked. It set
its own threshold — a fourth instance, or the first one that reached a commit
nobody reviewed — on the grounds that three is a pattern somebody can still
call coincidence.

It reached five inside two days, four of them in one.

1. **`bd dolt show` describes `.beads/metadata.json` as "(local,
   gitignored)"** while git was tracking it. Acting on what the tool said would
   have left it uncommitted and every clone's tracker pointing at a database
   name it had to guess.

2. **`bd init --proxied-server` committed directly to `main`**, wrote 56 lines
   into `AGENTS.md` — the file that *is* this repository's agent instructions —
   and deleted `sync.remote` and `export.auto` from `.beads/config.yaml`.
   `export.auto` is the one with teeth: it is what keeps
   `.beads/issues.jsonl` refreshed, and
   [`scripts/check-batch.sh`](../../scripts/check-batch.sh) reads that file to
   decide whether a batch may land.

3. **The same command's `--proxied-server` help names the wrong directory.** It
   says the database is rooted at `.beads/proxieddb`; it is at `.beads/dolt`. A
   `.gitignore` written from the documentation would have ignored a directory
   that is never created and published the one holding the database — tens of
   thousands of files, into a public repository.

4. **`.beads/metadata.json` reverted from `proxied-server` to `embedded` on its
   own**, with the database still in the proxied layout, and bd then refused the
   workspace: *"legacy Dolt workspace detected"*. No command was run against it.
   The tracker simply stopped opening.

5. **`bd` re-staged that same file into a commit that did not name it**, after
   this project had deliberately untracked it.

**What they have in common is not that bd is careless.** It is that a tool's
defaults are written for the median repository, and this one is not median: its
instruction files are load-bearing, its issue tracker is public
([0029](0029-the-issue-tracker-is-public.md)), and several of its checks read
files a tool considers its own. A default that is right almost everywhere is
still wrong here, and nothing in the tool can know that.

The failure mode they share is worse than being wrong: **they are quiet.** Four
of the five produced no error at the time, and two — the first, and the
`AGENTS.md` write inside the second (`inventory-tng-ltl8`) — printed output
saying the opposite of what they had just done.

## Decision

**A tool's defaults have no authority here, and where this repository has
already decided something, the decision wins — enforced by machinery wherever
machinery can hold it.**

1. **A tool may not be the author of this repository's instructions.**
   `AGENTS.md` and the files that symlink to it say what agents do here. A
   tool-managed block inside them is not permitted, whatever its markers claim
   about ownership. `inventory-tng-0dg` already decided that file is a thin
   router; this says the same thing to the tools that would grow it back.

2. **A tool may not commit on this repository's behalf.** Work reaches `main`
   the way [Pull requests](../../DEVELOPERS.md#pull-requests) describes, in
   commits that name an issue. A commit a tool made while doing something else
   satisfies none of that, and is to be unwound rather than kept — by whoever
   is entitled to rewrite the branch it landed on, which where that branch is
   `main` means asking a person first.

   **Read that as an instruction, not a description.** Nothing detects such a
   commit. The one this record is written about was found because somebody
   happened to be reading `git log` for another reason, and unwound by hand.
   Anybody relying on this point to self-correct is relying on that attention
   being paid again.

3. **Settings this project depends on are asserted, not remembered.** Where a
   tool rewrites a configuration file this repository relies on, the reliance
   gets a check. `export.auto` is the setting that named the rule and is its
   worked example: it keeps `.beads/issues.jsonl` current, and
   [`check-batch.sh`](../../scripts/check-batch.sh) reads that file to decide
   whether a batch may land. `bd init` deleted it once, and nothing but a
   person who happened to be looking noticed —
   [`check-beads-state.sh`](../../scripts/check-beads-state.sh) is what notices
   now.

4. **Where a tool's documentation and its behaviour disagree, the behaviour is
   what gets encoded** — and the check is written as an invariant rather than
   as a list copied from the documentation.
   [`scripts/check-beads-state.sh`](../../scripts/check-beads-state.sh) is the
   worked example: it asserts that every directory under `.beads/` is tracked
   on purpose or ignored on purpose, which is true whatever the next mode calls
   its storage.

5. **A file a tool owns may still be untracked, and untracking is the default
   for anything a tool can regenerate.** What is required is that the removal
   be real — deleted from the index, ignored going forward — rather than a
   convention. Measured rather than assumed: a fresh clone of `main` does not
   receive `.beads/metadata.json`, `git status --untracked-files=all` does not
   offer it, and `git add -A` does not pull it back.

## Consequences

- **Some of this cannot be enforced, and that is stated rather than hidden.**
  Nothing stops a tool rewriting a file between two of our commands. Points 3,
  4 and 5 have checkers or were measured, and each of those notices afterwards
  rather than preventing. Points 1 and 2 are rules somebody keeps: nothing
  refuses a tool that writes to `AGENTS.md` or commits on this repository's
  behalf, and nothing on the path an agent reads before running `bd init` sends
  it here. So the honest claim is "this repository finds out", and for two of
  the five not even that.

- **Upgrading a tool becomes a change to review**, not an errand. Instance 2
  arrived with a version bump, and three of the five would have been invisible
  to anybody who ran the upgrade and moved on.

- **A tally is a legitimate way to hold a question open.** `ljj1` is the
  mechanism this record came from: a bead, because it survives a session and is
  shared, with a threshold written down in advance so that reaching it is a fact
  rather than a judgement. That pattern is available again and is cheaper than
  arguing each instance on its own.

- **The cost is paid on the day a tool is right and we are not.** A project
  that overrides defaults inherits the reasons they existed. Where that
  happens, the override gets a comment saying what it is overriding.

## Alternatives considered

**Fix each instance and move on.** What was being done, and it is why the tally
existed: each fix was small, correct, and left nothing that would recognise the
sixth instance as a repeat.

**Pin the tool and stop upgrading.** Trades a known category of surprise for an
unknown one, and beads is under active development in ways this project wants —
proxied-server mode among them.

**Forbid tools that write to the repository.** Overbroad, and the example that
looks decisive is not one: the `commit-msg` hook that runs
[`check-commit.sh`](../../scripts/check-commit.sh) is a symlink git tracks and
[`bootstrap-dev.sh`](../../scripts/bootstrap-dev.sh) points at, so it arrives
with the clone whatever a tool may or may not write. What such a ban would
really cost is bd keeping its own four hooks current, and the tracker itself:
the database, the export and the metadata are all bd writing into this
directory.

**Write nothing and rely on review.** Review is what caught most of these, and
review is what missed the ones that arrived between reviews. It is a filter,
not a floor.

## References

- `inventory-tng-ljj1` — the tally, its five instances, and the threshold that
  produced this record.
- `inventory-tng-ol66` — bd may publish the tracker on a timer, which no
  command-text guard can see. Open, and the clearest instance of point 3's
  limit.
- `inventory-tng-ltl8` — bd reports skipping a symlinked agents file and then
  writes its target. Instance 2's proximate cause.
- [0029](0029-the-issue-tracker-is-public.md) — why publishing the tracker is
  the sharpest of these, rather than one of them.
- [0020](0020-who-merges.md) — the other record about what an agent may do
  without asking, and what enforces it.
