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

The honest cost is that **the review cycle having happened is now attested
rather than checked.** `.claude/hooks/landing-gate.sh` refuses a merge until
the cycle is recorded, but it records whatever it is told, it is not in the
repository, and it runs for one editor on one machine —
`inventory-tng-3sp` is the work of deciding whether that becomes a real
guarantee or stays a reminder. Until it is answered, this decision rests on
the same thing every other repository rule here rests on: that the person or
agent doing the work follows it, and that the pull request records enough for
anyone to check afterwards.

That last part is what makes the trade acceptable rather than reckless. Under
0017 every review, every triage and every answered finding is already written
into the pull request as it happens. A merge nobody approved is not a merge
nobody can audit.

Not adopted: requiring a second GitHub review before merge. It would be real
oversight, and for a volunteer project with one active maintainer it would mean
work waiting on nobody — the failure this decision exists to remove, with more
ceremony.
