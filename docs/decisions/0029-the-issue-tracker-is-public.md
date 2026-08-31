# 0029 — The issue tracker is public, on purpose

**Status:** accepted

## Context

Raised by the project owner on 2026-08-31, while a plan was being drawn up to
mirror beads into GitHub issues. The plan proposed a `private` label to keep
sensitive issues out of the mirror. The owner's objection ended it:

> *"isn't using tags to hide sensitive stuff just security by obscurity? Anyone
> can fork the repo and go through the beads"*

That is correct, and it was checked rather than conceded. `.beads/issues.jsonl`
is a tracked file in a public repository — a megabyte of it, browsable in the
GitHub interface, first committed on 2026-08-18 and touched by 195 commits
since. Every bead this project has ever filed is already readable by anybody,
and is in the history as well as at the tip. A label deciding what appears in a
GitHub *issue list* would have changed nothing about what is *readable*; it
would have been a filing convention wearing the costume of a control.

So the decision this record makes was already being made, silently, by the fact
of committing the export. What follows is that decision taken deliberately.

**What is actually in there, measured on 2026-08-31 rather than assumed.** Of
seven email-shaped strings in the whole tracker, five are `example.invalid`
placeholders; the other two are the repository owner's own commit address,
which is in every commit anyway, and a systemd unit name. No volunteer's name,
address or Slack ID appears. No credential appears. What is public is
*reasoning about* personal data — proportions, collision counts, the fact that
a name-to-address list exists and what it would be worth to somebody — and
candid engineering argument, including weaknesses that are known and not yet
fixed.

That is a policy question, not an incident, which is why this is a decision
record and not a remediation.

## Decision

**The issue tracker is public, and that is a choice this project makes rather
than an accident it tolerates.**

1. **`.beads/issues.jsonl` stays committed.** It is how the tracker reaches
   every clone and every agent; taking it out would cost the thing that makes
   the workflow work, to buy an obscurity that was never real.

2. **Nothing is hidden by labelling.** No bead is kept out of a mirror, a
   listing or an export on the grounds of sensitivity. If something genuinely
   cannot be public it does not go in a bead at all — see the rule below.

3. **Four things must never enter a bead**, because the tracker is public and
   the history cannot be recalled:

   - **Personal data about a real person.** Names, addresses, Slack IDs, phone
     numbers, locations. Aggregate shape — *"about 45% carry no address"* — is
     reasoning and is allowed; a row out of the database is not. The
     distinction is whether it identifies somebody.
   - **Credentials of any kind**, live or expired, including anything that
     looks like one closely enough to be tried.
   - **Exploitable detail for a weakness that has no mitigation and is
     reachable.** Naming a weakness is allowed and wanted; a recipe against a
     live deployment is not. Reachability is what decides it — see below.
   - **Anything given in confidence** by somebody who did not know they were
     writing for a public tracker.

4. **A known weakness may be described in the open while it is unreachable.**
   `inventory-tng-81f7.6` is the live example: it says that withholding a
   volunteer's identifiers does not stop them being guessed, and it is public.
   That is acceptable because `VOLUNTEER_ACCESS` defaults to `session`, so no
   deployment answers an anonymous caller unless an operator opts in, and no
   public deployment exists. **If that changes, the fix stops being optional** —
   an open posture over real volunteers requires the weakness closed first, not
   the description hidden.

## Consequences

- **Writing a bead is writing in public.** Every agent and contributor should
  compose one on that basis. This is not a burden worth resenting: it is the
  same standard as writing a commit message, and beads in this project are
  already written as arguments rather than as notes.

- **The reason for the openness is the project's own.** NYC Mesh is a volunteer
  community rather than a customer base. An engineering log somebody can read
  without asking, disagree with, and fork is consistent with that, and it is
  the same reasoning that made local development a priority
  ([0028](0028-a-certificate-a-phone-will-trust.md)) and that shaped
  [0012](0012-two-populations.md). Being able to see how a decision was reached
  is part of being able to dissent from it.

- **Mirroring beads into GitHub issues is now a workflow question and nothing
  more.** It changes reach — issues are indexed, searched and mailed to
  watchers, and a megabyte of JSONL is not — but it changes no one's *access*.
  That difference must never be argued as a security property, which is the
  mistake this record exists to have caught once and not again.

- **Something that must not be public has nowhere to go, and that is
  deliberate.** There is no private bucket in this tracker. If such a thing ever
  arises, it needs a decision of its own about where it lives, made before it is
  written down, rather than a label applied afterwards.

- **This record does not clean history and does not need to.** Nothing found in
  the audit meets the bar for rewriting 195 commits of a public repository. If
  something ever does, that is an incident with its own response, and the first
  step is not `git filter-repo` — it is establishing what was taken, which for
  a repository with forks is not answerable.
