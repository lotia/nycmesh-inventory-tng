# Issue tracking

Work is tracked with [beads](https://github.com/steveyegge/beads), a CLI issue
tracker that stores issues in the repository:

```bash
bd ready                 # issues ready to work on, nothing blocking them
bd show <id>             # full detail
bd update <id> --claim   # claim it
bd close <id>            # done (see Definition of Done first)
```

**Where the tracker keeps its database, and why a checker watches it.** beads
stores issues in a Dolt database under `.beads/`, and which directory depends
on the backend mode it is running — `.beads/dolt` under the proxied server this
project uses, `.beads/embeddeddolt` under the in-process engine. None of it is
committed: it is tens of thousands of files, and this repository is public.

`scripts/check-beads-state.sh` is what keeps that true. It asserts the
invariant rather than a list of names — every directory under `.beads/` is
either tracked on purpose, like the `hooks/` symlinks, or ignored on purpose —
so a directory a future bd version invents is caught by a rule that never heard
of it. It also checks that the mode named in `.beads/metadata.json` has storage
to go with it, because a mismatch does not make bd fail: it opens an empty
database and says so only as a warning, which reads as an empty tracker rather
than a broken one. CI runs it, and you can run it yourself.

`.beads/metadata.json` is deliberately not committed. It is bd's local pointer
at that database, and `bd dolt show` describes it as local; it must not be
deleted, only left untracked.

You do not have to use beads to contribute. GitHub issues and pull requests work
fine — see [CONTRIBUTING.md](../CONTRIBUTING.md). An issue you open there is meant
to reach the tracker rather than sit beside it:

```bash
scripts/sync-issues.sh --check     # is anything out of step? (refuses if so)
scripts/sync-issues.sh --dry-run   # what would move, in either direction
scripts/sync-issues.sh             # reconcile the two
scripts/sync-issues.sh --no-say    # that, without the last step
scripts/pull-new-issues.sh         # only the half that brings issues in
```

The reverse trip — every bead that has never had an issue filed for it — is
`scripts/export-issues.sh`, which mirrored the tracker into GitHub once and
has had nothing to do since:

```bash
scripts/export-issues.sh             # what it would file, and files nothing
scripts/export-issues.sh --confirm   # actually file it
scripts/export-issues.sh --check     # only: is anything waiting? (refuses if so)
```

It only ever *creates*. A bead that already points at an issue is not in its
list, so it cannot reach the call that would rewrite that issue's body. It
refuses to start at all in two cases, both of which would otherwise duplicate
something: while GitHub is holding an issue no bead points at, and while your
checkout is behind its upstream — somebody else may have filed from a commit
you have not pulled. `pull-new-issues.sh` refuses on that second one too, for
the mirror of the same reason. Stopping half way through is safe: re-running
picks up exactly what is left, and nothing is filed twice.

`sync-issues.sh` runs five steps and the middle one is the one to read: it
brings issues in, **prints what arrived**, and only then sends anything out.
Look at that diff. It is where an edit made on GitHub becomes visible before it
can travel any further, and `git checkout .beads/issues.jsonl` is how you
refuse it.

The last step is `scripts/say-bead.sh`, which puts what an issue body cannot
hold — a bead's design, its acceptance criteria, its notes and every dependency
it declares — on the issue as a comment it rewrites in place. Why it is a
comment, and why it runs there rather than beside this, are in
[0031](decisions/0031-the-issue-list-is-a-window-on-the-tracker.md).

```bash
scripts/say-bead.sh --dry-run        # what would change, and change nothing
scripts/say-bead.sh --only <bead>    # one of them, to read the wording first
scripts/say-bead.sh --confirm        # allow a pass that creates hundreds
```

It keeps up with ordinary work unasked, and **refuses a pass that would create
more comments than somebody could watch go past** — which the first one, on a
tracker that has never had them, always would. Until that has been run
deliberately, the reconciliation says so at its last step and reconciles
everything else.

Both need `gh` authenticated and a `GITHUB_TOKEN`; without them they say so and
stop rather than reporting that there was nothing to do. Being unable to run
them does not break anything — the tracker is not made wrong by an unsynced
issue, only incomplete.

Nothing does any of this for you. No git hook fires it, and CI does not run it;
what CI does is ask every morning whether the two lists are out of step, which
is three questions — is GitHub holding an issue no bead points at, is the
tracker holding a bead GitHub has never heard of, and do the two disagree about
work they *both* know. It stays red until somebody has acted, and clears itself
with nothing to close.

That third one it will only ever *report*, never fix — reconciling it is
`sync-issues.sh` and a person, for the reason
[0031](decisions/0031-the-issue-list-is-a-window-on-the-tracker.md) gives. Why it is arranged that
way, why the correspondence between the two lists reaches every clone for free,
and what a GitHub reader is and is not looking at, are
[0031](decisions/0031-the-issue-list-is-a-window-on-the-tracker.md).

An issue pulled in this way becomes a bead named for the moment it arrived —
`inventory-tng-1788200756998-1-26030a28` — typed `task` at priority 2, with no
parent. That is a sensible default and a poor resting place: the id cannot be
read in a commit message, the type is a guess, and nothing joins it to the work
it belongs with. So it wants a person:

```bash
scripts/untriaged.py .beads/issues.jsonl   # what arrived and was never looked at
```

For each one it names the issue it came from, offers a few `bd search` terms
drawn from its title so you can tell whether it duplicates something already
tracked, and spells out the two commands that finish it — `bd rename`, then
`bd update` for the type, the priority and the parent.

Deciding what the thing actually *is* — whether it duplicates something, what
kind of work it is, how urgent, whose epic, and how much of the body you may
honestly write — is [Triaging an issue somebody else
filed](triage.md). The commands are the easy half.

**The name is the marker.** A bead still carrying the one it arrived with has
not been through this; renaming takes it off the list. Nothing has to be set or
cleared, so there is no second record to fall out of step with the first.
Renaming keeps the GitHub link: `bd rename` preserves the reference and
repoints anything that mentioned the old id.

**Every bead you write is published.** See
[0029](decisions/0029-the-issue-tracker-is-public.md) for what that means
and the four things that must never go in one.

