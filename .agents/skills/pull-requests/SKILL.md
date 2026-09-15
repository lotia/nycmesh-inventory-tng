---
name: pull-requests
description: Use when running a batch through review - opening the pull request, taking a review or simplify pass against it, attributing findings to issues, and merging. Covers the part where findings from one review have to become commits that each belong to one issue.
---

# Running a batch through review

The procedure — publishing into a draft as each issue lands, the review pass,
sorting its findings into one issue each before fixing any, the simplify pass,
and the merge — is [Pull requests](../../../docs/pull-requests.md), written
for people, and an agent follows it as written. Read it when a batch is ready
to be reviewed rather than while building one; the skill for that is
[commits](../commits/SKILL.md).

Three things are an agent's alone, and are here because nothing else says
them.

**`/code-review` is a person's to type, and you cannot.** It is a built-in
carrying `disable-model-invocation`, so an agent invoking it is turned away by
the harness rather than by anything in this repository, and no setting here can
grant what the refusal withholds. Ask for it, and say only that you are asking
— name the pull request, name the one command, and stop:

> `<pr>` is green and every thread on it is answered. Run
> `/code-review <pr> --comment` and I will triage, fix and land what comes back.

Do not dress the request up as a failure: that invites a reply about whether
the batch is in trouble, when it is finished and waiting.

**`/simplify` is yours.** Run it; do not ask for it. It fans out several review
agents of its own, and its findings are posted to the pull request with the
`<!-- review-cycle: simplify -->` marker before any are applied, as the page
says.

**Never type a marker instead of running its pass.** Putting
`<!-- review-cycle: code-review -->` at the left margin having run no pass
manufactures the evidence `scripts/landing-gate.sh` exists to look for. It will
work, which is exactly the problem. The gate, what it refuses and why it fails
closed are [the landing gate](../../../docs/pull-requests.md#the-landing-gate)
and [0020](../../../docs/decisions/0020-who-merges.md); an agent meets its
`Stop` hook and a person does not, and the page says what that one asks.
