# 0020 — Whoever finished the branch merges it

**Status:** accepted

## Context

[0017](0017-review-through-pull-requests.md) settled how work is reviewed: one
pull request per batch, findings answered where they were made, and each commit
landing individually by rebase merge. It did not say who presses the button, and
for agents the answer had been "ask a human, every time".

That was the right default when nothing mechanical checked anything. It stopped
being right once `main` grew branch protection. Most of the conditions on a
merge are now enforced by GitHub and cannot be waived from a terminal;
[When a branch is ready to merge](../pull-requests.md#when-a-branch-is-ready-to-merge)
is the list, and which of them nobody enforces.

So the approval step had become a question with one available answer. It cost a
round trip at the moment work was finished and verified, and a question whose
answer is never "no" teaches everyone to stop reading it.

There is a second reason, and it is the one that decided the shape. Asking a
human to approve a merge *looks* like oversight without being any. The person
approving cannot see whether the review pass happened; they see a green tick
they did not compute. Naming that honestly is worth more than the ritual.

## Decision

**Whoever finished a `batch/*` branch merges it, agent or not, once it is
mergeable.** The bar is
[When a branch is ready to merge](../pull-requests.md#when-a-branch-is-ready-to-merge),
written once and identical for both.

A branch that does not meet the bar is one to finish. It is never one to ask
an exception for, which is the reading that would otherwise turn a standing
permission into a way around the gate.

Everything else is unchanged in what it permits, and has since changed in what
does the permitting. `main` directly, a bare `push --force`, publishing the
tracker and writing repository settings were all things an agent was asked to
stop and ask about; they are now refused by
[`scripts/landing-gate.sh`](../../scripts/landing-gate.sh) and, for the first
two, by branch protection behind it. Merging a branch that is not a `batch/*`
branch is still nobody's to do on their own. What this record settles is
untouched by that: the change is who remembers, not what is allowed.

## Consequences

The honest cost is that **the review cycle having happened is attested rather
than proven.** `inventory-tng-3sp` asked whether the gate this rests on should
become a real guarantee or stay a reminder, and answered it by making it as
real as a client-side guard can be: `scripts/landing-gate.sh` now ships with
the repository, is registered in tracked settings so every clone and worktree
has it, refuses rather than permits whenever a dependency is missing, and
records the artifacts it found on the pull request rather than a literal it was
handed. Three of the four things this paragraph originally conceded are no
longer true.

The fourth stands, and half of it has since moved. A marker comment can be
posted by somebody who reviewed nothing: a gate can make forgetting hard and
cannot make lying hard, and no amount of machinery changes that. But *"a
command line can be spelt in ways no reader catches"* was a property of asking
the question on the machine that types the merge, and `inventory-tng-x0jp`
asked whether it had to be. It did not.

**The review cycle is now held by a required check as well.** The `Review
cycle` job in `ci.yml` reads the pull request's own comments and reviews and
refuses when a stage has nothing behind it, and it is required by existing and
being named — `scripts/repo-settings.sh` derives the contexts from that file's
job names. What that closes is not the lying: it is every route that never met
the local gate at all — a different spelling, a different machine, a checkout
where the hook was never installed, and the GitHub web UI, which the hook
structurally cannot see.

**It is not un-waivable, and calling it that would be the same overstatement
this record was written to avoid.** The job checks the branch out and runs
`scripts/review_cycle.py` *from the pull request under review*, so a change to
that file in the same pull request decides its own verdict. Removing the check
from the required list is a deliberate branch-protection change; editing what
it does is an ordinary diff. What stops that is the same thing that stops a
forged marker — it is written down where anyone can read it afterwards — and
not the machinery.

Two things follow that are worth stating plainly rather than discovering.

**The local gate is still there, and is not now redundant.** It refuses before
a round trip rather than after one, and it holds the bare `git push --force`
rule from `inventory-tng-614`, which branch protection does not cover because
protection is on `main` and that rule is about `batch/*`. Deleting it is a
separate decision, and one nobody should take before the check has been watched
working.

**The check is deliberately weaker in one respect.** The local receipt ties
evidence to a head, so any push invalidates it. The check does not: the
evidence is the pull request, which cannot be moved to another one, and
`inventory-tng-8nqo` — what makes a review current enough to count — has
already decided that a re-record after a fix is satisfied by the review already
there. Requiring the review to name the current commit would refuse the case
this project has said it wants. If 8nqo settles the other way, this is the
paragraph that changes.

So the decision still rests on the same thing every other repository rule here
rests on: that the person or agent doing the work follows it, and that the pull
request records enough for anyone to check afterwards.

That last part is what makes the trade acceptable rather than reckless. Under
0017 every review, every triage and every answered finding is already written
into the pull request as it happens. A merge nobody approved is not a merge
nobody can audit.

Not adopted: requiring a second GitHub review before merge. It would be real
oversight, and for a volunteer project with one active maintainer it would mean
work waiting on nobody — the failure this decision exists to remove, with more
ceremony.

## Amendment (2026-09-15) — what the gate refuses, and why

The procedure — the five conditions `main` enforces, the two a person holds
to, and the gate's commands — is
[When a branch is ready to merge](../pull-requests.md#when-a-branch-is-ready-to-merge).
The reasoning behind each refusal used to sit beside it in the guide, and is
here now because this is the record it belongs to.

### The marker a spike posts

A spike built to be shown at a meeting can be green, rebased, reviewed and on
a `batch/*` branch — every condition for merging — and still be work that must
not land. Prose cannot hold that, because merging it is what following the
documented flow looks like. So the body carries `<!-- do-not-merge -->`, and
something reads it.

Posting the marker is the whole of it; writing *about* it is free. Only the
left margin, outside a fence, counts — the rule the review-cycle markers
already keep, and
[One review pass](../pull-requests.md#one-review-pass-findings-filed-per-issue)
says what it cost to learn.

`.github/workflows/look-again.yml` re-runs the check whenever a body changes,
because a check that has already answered would otherwise stand on what it
read last — green, if the marker was added after the fact. Its header says why
that lives in its own file.

### What the gate refuses, and why

**A merge of a branch that is not finished** — commits still waiting to be
folded in, or an issue in the batch epic that has not landed. Those two
questions used to be asked by CI on every push, which meant they were answered
"no" through the whole of building and reviewing a batch: the job failed on
48% of this repository's pull request runs, every one of them the documented
flow doing what it is told. They are questions about whether a branch is
*ready*, so they are asked once, at the merge, where that is the thing being
decided. CI still asks everything structural, and still holds a fork's pull
request to it where no local hook runs.

**Ending a turn** on a `batch/*` branch whose pull request is ready, green,
and has no recorded cycle. That one is registered as a `Stop` hook rather than
on a command, because the failure it answers is not a command at all: it is a
session deciding the work is finished and writing a summary instead of running
the cycle. Ending the turn is what gets refused, so the summary cannot be
written in place of the work. Three things about it are deliberate and are not
how the rest of the gate behaves:

- **It fails open.** Every other refusal fails closed, because letting an
  unreviewed merge through is worse than being unable to merge. Ending a turn
  is the opposite: a session that cannot stop also cannot fix whatever is
  stopping it, because fixing it ends in stopping too. So a missing `python3`,
  an unauthenticated `gh`, a rate limit or no network all let the turn end and
  say on stderr that nothing was checked.
- **It asks once per head.** A refusal that repeated every time you meant it
  would be a session nobody could end, so it is remembered against the commit
  it was about. Push anything and it asks again, which is right — what was
  reviewed is no longer what is there.
- **Drafts and red checks are exempt.** A batch under construction is a draft
  by the flow above, and stopping in the middle of one is ordinary.

That makes it a nudge rather than a lock, on purpose. The lock is the merge
refusal, which does not forget and cannot be spent.

**Every other refusal fails closed.** When `python3` or `gh` is absent, or
`gh` answers unauthenticated or rate-limited, the gate refuses the command and
names the program it could not use. It used to fail open in exactly those
three ways, which is worse than having no gate at all: the rule goes on being
believed while nothing is checking it, and nothing announces that the guard
has stopped working. A `gh` that is merely *slow* is the same case, and needs
its own deadline rather than the hook's: a hook killed for exceeding its
timeout prints no verdict, and no verdict is read as permission. So the gate
gives `gh` a shorter deadline than the timeout registered in
`.claude/settings.json` and refuses in time to say so. The one place the same
reasoning points the other way is `clear <pr>`: rather than treat a receipts
file it cannot parse as empty and rewrite it, it refuses and leaves the file
alone.

**With one exception, and it is the gate's own source.** If a rebase stops on
a conflict *in `scripts/landing-gate.sh` itself*, the half-written file cannot
judge anything, and every command it guards would then be refused over
something that has nothing to do with the command. The gate stands down there,
saying so on stderr, and it takes three things at once: the matcher has
already failed, git reports an operation actually in flight, and the file
carries conflict markers. Any two of the three leave it guarding as usual.

Deliberate, and worth less than it first looks. Whether the guarded verbs
appear at all is settled before the matcher is consulted, so anything without
`gh` or `push` in it never needed this file to be readable: continuing or
abandoning the rebase was never prevented. What the stand-down releases is the
guarded pair themselves, the force-with-lease that ends a collapse and a merge
that nothing is checking. The break that really does take a session down is
markers in the *shell* half, where bash exits with the status the harness
reads as *blocked* before any of this runs — out of reach from inside the
file, and `inventory-tng-ghqk` records both the measurement and what it would
take to cover.

**What `record` buys.** It stores what it found on the pull request whether or
not that is everything, and a partial receipt refuses a merge as firmly as
none. What it buys is a truthful nudge, since the stop hook reads the receipt
rather than the pull request and would otherwise go on naming a pass that had
already run. Finding nothing at all still writes nothing.

None of it is a security boundary. It reads a command line, and a command line
has more spellings than any reader has patterns; the enforcement that matters
is the rules `main` holds itself, and this record is the decision about what
may rest on a guardrail against forgetting.
