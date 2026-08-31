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
[When a branch is ready to merge](../../DEVELOPERS.md#when-a-branch-is-ready-to-merge)
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
[When a branch is ready to merge](../../DEVELOPERS.md#when-a-branch-is-ready-to-merge),
written once and identical for both.

A branch that does not meet the bar is one to finish. It is never one to ask
an exception for, which is the reading that would otherwise turn a standing
permission into a way around the gate.

Everything else an agent asks about is unchanged: `main` directly, a bare
`push --force`, `bd dolt push`, repository settings, and merging any branch
that is not a `batch/*` branch.

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
