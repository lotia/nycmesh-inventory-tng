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

### The window is wider than point 2 said, and a comment is why

Amended 2026-09-02, for `inventory-tng-cwpa.15`. Point 2 above says what stays
behind — design, acceptance criteria, notes, every dependency — and calls that
asymmetry "accepted rather than regretted". The acceptance stands; the size of
it does not, and this is what changed.

**What the acceptance was actually against.** Point 2 gives one reason:
inventing a dependency graph "out of labels and checklists would produce a
second half-tracker to keep in step". That is an argument against a
*representation GitHub could be edited through* — a checklist somebody ticks, a
label somebody removes — because two writable copies of one fact is the thing
nothing can keep true. It is not an argument against a reader being able to see
what a piece of work depends on, which is a different question and had been
answered by accident.

**What it cost.** 367 of the 435 beads with an issue carry something the body
does not say. A contributor reading on GitHub — which is everybody who has
never run `bd`, and the whole reason
[the tracker is public](0029-the-issue-tracker-is-public.md) — cannot see what
would make a piece of work done, and an epic reads as a description with
nothing under it.

**The body was not available.** The obvious fix is to write more into the
description before pushing it, and that fix is unsafe here for the reason point
6 gives: reconciliation runs `--prefer-newer`, so a body newer than its bead
replaces that bead's description. Anything written into a body comes back on
the next pull and *becomes* the bead. That is not a window growing; it is the
copy overwriting the original.

**And not GitHub's own relations, which is the other thing to rule out.**
Sub-issues and issue dependencies are the features built for the symptom this
opens with, and they are the natural reach. Two reasons they are not it. They
are **editable in the UI and never read back**, which makes them precisely the
writable second copy point 2 refused — unless a pass reconciled them wholesale,
which means silently deleting a link a contributor added by hand. And they carry
two of the four relation types and **none** of `design`, `acceptance_criteria`
or `notes` — so adopting them buys a nicer rendering of part of the content at
the price of running two mechanisms. Worth revisiting only if the second ever
stops being true.

**So it is a comment, and the comment is generated.** `bd` reads and writes no
issue comments in either direction — checked, not assumed — so a comment is the
one place text can sit beside a body without travelling back.
`scripts/unsaid.py` renders it from the committed export and
`scripts/say-bead.sh` puts it there, rewriting the one it left rather than
adding another, and taking it down when the bead stops having anything to say.

**This does not create the thing point 2 refused.** Nothing ever reads the
comment back. It is overwritten wholesale from the export on every pass, it is
marked as generated and says where the record is, and no decision is ever made
from it. A projection that is rewritten from its source is not a second copy to
keep in step — it is the same argument that makes `external_ref` in a committed
file safe. **An issue is still a summary, and the bead is still the thing.**

**It runs as step 5 of the reconciliation**, in the same pass that pushes the
bodies, so a comment and the body it sits under are never one run apart. It is
deliberately not part of the standing signal in point 7: asking whether every
comment is current costs one request per issue, every morning, to notice
something the next ordinary run repairs.

**But mirroring and keeping up are not the same act, and only one of them is
automatic.** Writing a comment onto every issue that has never had one is the
same kind of thing point 4 makes `export-issues.sh` ask for `--confirm` before
doing, and being a step of a script does not make it smaller. So the ordinary
case — the few that moved — runs unasked, and a pass that would create more
than a handful refuses and says what to type. `--no-say` reconciles without the
step at all.

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

**"Every clone has a complete map" means as of that clone's last pull, and both
halves enforce that rather than asking for it.** Two machines that both file
while one is behind the other would file the same bead twice — the stale side
cannot see a reference that exists only in a commit it has not fetched. The
guard against the mirror-image failure is no help, and looking at why is what
shaped the fix: a stale checkout is equally unaware of the issue on the *other*
side, so the unlinked-issue precondition finds nothing waiting and is content.
It would go further and offer that issue as unpulled, and honestly acting on
that makes a second bead — one stale checkout, duplication in both directions.

So a run that writes — `export-issues.sh --confirm`, or `pull-new-issues.sh`
bringing anything in — fetches and refuses while the checkout is behind its
upstream, or while it tracks nothing at all. A run that only asks is not
refused: it changes nothing, so being behind costs a stale count rather than a
duplicate, and `--check` runs in CI where the head is a pull request's and has
no upstream to be behind. A fetch that fails refuses too, and that trade turns
out not to be one — a run that cannot reach the git remote was never going to
reach the GitHub API a moment later.

The export half was built first, because the two costs are not equal: an
ordinary token cannot delete a GitHub issue, so a duplicate there is permanent,
while a duplicate bead can be deleted. That asymmetry justified doing one
first. It does not justify leaving the other undone, and the guard lives in
`scripts/repository.sh` so that both halves call it rather than copy it.

**The standing signal asks three questions, and only ever reports.** CI is red
while GitHub holds an issue no bead points at, while the tracker holds a bead
GitHub has never heard of, and while the two describe work they *both* know
differently. The second went unasked until `unexported.py` made it answerable
from the committed export alone; the third until `drifted.py` did, after it had
been found by hand three batches running.

One job and one `run:` line, with `scripts/sync-issues.sh --check` asking all
three — because a step that fails stops the job, so as three workflow steps the
later questions would go unasked for as long as an earlier one was red. None of
the three brings anything in or sends anything out.

**The third will not be automated, and that is a decision rather than an
omission.** Closing an issue to match a closed bead would be right almost every
time, and the exception is the whole reason not to: an issue may be open because
somebody *reopened* it, saying the work is not done after all. A schedule that
overruled them would be doing exactly what point 5 forbids an export from doing,
and what `--prefer-newer` exists to leave to a person looking at a diff. So the
check names what disagrees, and settling it stays a person running
`sync-issues.sh`.

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
