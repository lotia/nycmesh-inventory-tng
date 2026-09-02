# 0031 — The issue list is a window on the tracker, and the mapping travels in the repository

**Status:** accepted

## Context

This project keeps its work in [beads](https://github.com/steveyegge/beads) and
its front door on GitHub. Neither is going away. Beads holds dependencies,
parents, acceptance criteria, design notes and a history that survives being
compacted; GitHub holds the button a stranger presses when something is wrong,
and [CONTRIBUTING.md](../../CONTRIBUTING.md) promises they need know nothing
about the other. So there are two lists of work, and somebody has to say how
they relate.

The project owner asked for that on 2026-09-01, in two parts: mirror what the
tracker already holds into GitHub, once; and write down the arrangement that
keeps them together afterwards, **in a form that serves anybody who clones the
repository** rather than only the machine that first ran it.

That second clause is the whole difficulty. A correspondence between two
identifier spaces has to be stored somewhere, and most of the obvious homes —
a database on one contributor's laptop, a file nobody commits, a service — fail
the moment a second person shows up. Everything below follows from finding a
home that does not.

What `bd` supplies, measured on 2026-08-31 and re-measured against version
1.1.2's source on 2026-09-01: it can create a GitHub issue for a bead, update
one it created, and fetch one by number. It cannot ask GitHub what exists. That
last gap is not a defect to route around — it is the reason this arrangement
has scripts in it at all.

## Decision

### 1. The correspondence lives in the repository, and that is why a clone is enough

When `bd` files an issue for a bead it records the issue's URL on the bead, as
`external_ref`. That field is written into `.beads/issues.jsonl`, and **that
file is committed**.

So the answer to "which issue is this bead?" is in the checkout. It is in every
checkout. A contributor who has never authenticated against GitHub, never run a
sync and never heard of `external_ref` still holds a complete and current map,
because it arrived with `git clone` along with everything else. Nothing has to
be distributed, no server is asked, and there is no state on one person's
machine that the rest of the project is missing.

This was the open question when the work began and it is the load-bearing
answer. Every other decision here is affordable only because of it — in
particular, tooling can be honest about what has and has not been pushed
without talking to anybody, which is what makes both the export below and the
scheduled check safe to run anywhere.

### 2. The tracker is the record; the issue list is a window on it

They are not two copies of one thing and must not be read as though they were.
What reaches GitHub is the title, the body, and labels for type and priority.
What stays behind is everything else a bead carries: its design section, its
acceptance criteria, its notes, its parent, and every dependency it declares or
satisfies.

That asymmetry is accepted rather than regretted. GitHub has nowhere to put a
dependency graph, and inventing one out of labels and checklists would produce
a second half-tracker to keep in step. The consequence has to be said plainly
because a reader will otherwise assume the opposite: **an issue is a summary,
and the bead is the thing.** Judgements about what to work on are made from
`bd`, not from the issue list.

### 3. Pull before push, in that order, every time

`bd` never enumerates, so an issue opened by somebody who has never run it is
invisible until the number is handed over by name. Push first and that issue is
still invisible — and if the same work is already a bead, GitHub gets a second
issue saying it.

So bringing things in always precedes sending things out.
`scripts/unsynced.py` supplies the missing enumeration by asking GitHub what it
holds and subtracting the URLs already recorded in the committed export; it
matches whole URLs rather than parsing issue numbers, because numbers are only
unique within one repository and an earlier version that pattern-matched them
accepted another repository's issue as ours.

### 4. The bulk export only ever creates, and that is checkable rather than careful

`external_ref` decides which of two very different things happens to a bead:
one without it gets `POST /issues`, and one with it gets `PATCH /issues/{n}`
carrying title, body, labels and state. The second **replaces the issue body
outright**. Text somebody typed on GitHub that never made it back into the bead
does not survive it.

The one-time export therefore names only beads that carry no `external_ref`.
Not "avoids updating where it can", not "warns first" — it cannot reach the
update path at all, because the only beads it mentions are ones for which no
issue exists. The property is a consequence of the input, so it can be verified
by looking at the list rather than trusted by reading the code.

Two things follow, and both are easy to get wrong:

- **The refusal is part of the guarantee.** An issue on GitHub that no bead
  points at is invisible to a create-only export, so exporting across it files
  a duplicate. Losing an issue in a pile of near-identical ones is the same
  harm as overwriting it. The export therefore refuses to begin while anything
  on GitHub is still unlinked; point 3 is a precondition, not a suggestion.
- **Comments were never at risk.** `bd` has no code that reads or writes an
  issue comment, in either direction. The whole exposed surface is the body,
  title and labels of an issue already linked to a bead. Discussion is safe,
  and is also never imported — a decision reached in a comment thread has to be
  written into the bead by a person.

### 5. A closed bead is closed on GitHub by a second, narrower step

Creation sends a title, a body and labels. It does not send a state, and
GitHub's default is open. So a mirror of this tracker built by creation alone
publishes several hundred open issues for work that finished months ago,
indistinguishable in the list from the ones that are real.

The export closes them itself, by state and nothing else. It is deliberately
not a second push: a push would PATCH the body too, which is the path point 4
exists to stay off. Leaving them open would also poison every later
reconciliation, because a routine sync compares the state it wants against the
state GitHub has, finds hundreds that disagree, and proposes exactly the
wholesale body rewrite that this record is arranged to prevent.

### 6. On a conflict the newer side wins, and the safety net is git rather than the flag

Reconciliation runs with `--prefer-newer`, which means an edit made on GitHub
can replace a bead's description. That is the project owner's choice while it
is still unknown whether contributors will adopt beads at all, and it is
tolerable for a reason that has nothing to do with the flag being careful: the
export is a committed file, so anything arriving from GitHub shows up as a diff
to read before it goes anywhere, and one `git checkout` puts it back. The
tracker has version history and a working tree. That is the protection.

Which is why the reconciliation script pulls, then **shows what arrived**, then
pushes. The middle step is not progress reporting; it is where the safety net
is actually deployed.

### 7. Nobody's machine does this on a timer, and CI does not do it at all

Not a git hook. Pushing writes `external_ref` onto every bead it files, which
rewrites the committed export, so a hook on `git push` would hand the tracker
back dirty after every push — which is precisely how tracker rows get stranded
and lost. It would also make a GitHub token a requirement for pushing a branch.

Not CI either, and this one was built before it was abandoned. A tracker
assembled inside a runner is discarded when the job ends, so the only durable
output was a commit — and a commit pushed with `GITHUB_TOKEN` starts no
workflow run, so the pull request carrying it sat for ever waiting for checks
that would never be reported, while the job that opened it reported success.

What CI does instead is **notice**. A scheduled job asks whether GitHub holds
an issue the tracker has never heard of, and is red for exactly as long as the
answer is yes. That is a standing signal rather than a notification: it asks
again tomorrow, it needs nothing closed or cleaned up, and it goes green by
itself when somebody commits the rows. A pull request would have announced the
same fact once and then rotted.

## Consequences

**Anybody can answer "is this in step?" offline.** The comparison is between
what GitHub returns and what the committed export records, so a fresh clone, a
CI runner with no database, and a laptop that has never synced all give the
same answer. Nothing needs a token to ask the question — only to act on it.

**A second machine needs no setup beyond the one this repository already
asks for.** Beads themselves travel between machines as `.beads/issues.jsonl`
in git, and `bd`'s own five hooks — which arrive in the clone, in
`.beads/hooks/` — import on merge and checkout so a `git pull` brings another
machine's work into the local database. Those hooks are armed by
`core.hooksPath`, which git does not clone, and pointing it at that directory
is something [bootstrap](../../DEVELOPERS.md#clone-and-bootstrap) already does
for its own reasons. Nothing further is installed for the GitHub half: the link
is a field in a file that is already being pulled. What a second machine needs
is a `gh` login and a token, and only at the moment it wants to *act*.

**"Every clone has a complete map" means as of that clone's last pull, and
there is a sharp edge on it.** Two machines that both file issues while one is
behind the other will file the same bead twice — the stale side cannot see a
reference that exists only in a commit it has not fetched. Nothing currently
refuses that, and the guard against the mirror-image failure does not help,
because a stale checkout is equally unaware of the issue on the other side.
`inventory-tng-cwpa.10` is the mechanism that would make it impossible rather
than merely discouraged; until it lands, pull before exporting.

**The standing signal runs one way only.** CI is red while GitHub holds an
issue no bead points at, and silent while the tracker holds a bead GitHub has
never heard of — so work created on a second machine can sit unfiled with
nothing saying so. That was a limit of what could be asked cheaply, and
`unexported.py` removed it: the question is now answerable from the committed
export alone, on the same terms as the other direction. `inventory-tng-cwpa.9`.

**Two credentials are needed to act, and they are configured separately.** `gh`
works out which repository it is in by reading the checkout; `bd` does not, and
wants `GITHUB_REPOSITORY` or its own config. Discovering that by running it is
how the scripts came to resolve one answer and export it to both. A contributor
without either is not blocked from anything except syncing; an unsynced issue
makes the tracker incomplete, never wrong.

**An issue that arrives from GitHub is not yet a bead anybody can work with.**
It lands with a synthetic id built from a timestamp, typed `task` at priority 2,
with no parent — which cannot be written in a commit message and joins nothing.
Triage is a person's job and has its own guide,
[docs/triage.md](../triage.md); the marker for whether it has happened is the
id itself, so nothing separate can fall out of step.

**Everything mirrored is public, and was already.** Beads are published the
moment they are pushed —
[0029](0029-the-issue-tracker-is-public.md) — so the export discloses nothing
new. What it changes is reach: a closed bead in a JSONL file is technically
readable, and a closed GitHub issue is *findable*, by search engines and by
people. The four things 0029 forbids in a bead are forbidden for this reason
and not a weaker one.

**The bulk export is one-time by intent and idempotent by construction.**
Re-running it names nothing, because every bead it filed an issue for now
carries an `external_ref`. There is no state to reset and no marker to clear;
it stops having anything to do because its input is empty. Beads created
afterwards are ordinary work for the reconciliation in point 3.

**This depends on `bd` continuing to store the link as a URL.** If that
representation changes, every issue reads as unpulled and a sync files a second
bead for each — silently, and doubling. `scripts/unsynced.py` refuses outright
on an `external_ref` it does not recognise rather than treating it as absent,
which turns that failure from data corruption into a stop.
