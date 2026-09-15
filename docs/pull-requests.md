# Pull requests

Nothing reaches `main` except through a pull request, and `main` is protected
so that there is no other way in: one batch on one branch under one pull
request, reviewed once, its findings filed per issue, and merged by whoever
finished it once it clears a bar `main` mostly enforces itself. Why it is
arranged this way is [0017](decisions/0017-review-through-pull-requests.md)
and [0020](decisions/0020-who-merges.md); this page is the procedure.

This is the first point at which the anonymous clone in
[Prerequisites](../DEVELOPERS.md#prerequisites) is not enough: pushing needs a credential. Add
an SSH key to your GitHub account and point the remote at it —

```bash
git remote set-url origin git@github.com:lotia/nycmesh-inventory-tng.git
```

— or keep the HTTPS remote and let `gh auth login` install a credential helper
for it. Contributors without write access push to a fork instead;
[CONTRIBUTING.md](../CONTRIBUTING.md) is that path.

**One batch, one branch, one pull request.** A batch is the set of issues you
mean to ship together. Branch from `main` as `batch/<name>`; if the batch is
more than one issue, group them under an epic in the tracker so that what
belongs to it is recorded rather than remembered:

```bash
bd create --type=epic --title="Batch: <name>"   # only when batching
bd update <issue> --parent=<epic>
```

A single issue shipping on its own needs no epic. The epic carries one fact —
which issues are in this pull request — and `bd epic close-eligible` disposes of
it once they land. `--parent` means batch membership and nothing else; what a
piece of work is *about* is a label.

## Finish, then publish, then review

Each issue is finished to the [Definition of Done](../CONTRIBUTING.md#definition-of-done) and
published before anything is reviewed. Nothing is reviewed that has not already
passed its own checks:

1. Land the issue as its own commit — see [Commits](commits.md#commits).
2. Push to the batch branch. The first push opens the pull request as a draft,
   so CI runs per issue rather than once at the end.
3. Repeat for the next issue in the batch.

Mark the pull request ready when the batch is complete and CI is green. What
the batch holds is posted there as a comment, read off the commits rather than
typed, so the list is the one that was checked.

## A batch is done when it is merged

Not when the commits are pushed, not when the checks go green, and not when the
pull request is marked ready. Those are the middle of it.
[0020](decisions/0020-who-merges.md) settles that merging a mergeable
`batch/*` pull request needs nobody's permission and that whoever finished it
merges it; this is the consequence.

So there are two honest ways to leave a batch, and a ready-but-unreviewed pull
request is neither:

- merged, or
- stopped at a named stage, saying why, with what
  `scripts/landing-gate.sh status` reports

Stopping anywhere else is a deviation. Say so plainly rather than describing it
as finished — a summary that reports a stalled batch as done is how one gets
left behind, and the reader has no way to tell from the outside. The gate
refuses to end a turn in exactly that state, which is
[what it refuses](#when-a-branch-is-ready-to-merge), but the refusal asks once
and is not a substitute for saying it.

## One review pass, findings filed per issue

The batch is reviewed **once**, against the pull request, and the commentary
stays there. A commit message says what changed; what a review said is not what
a reader of the history needs, and putting it in both places would leave two
records to disagree.

Every finding is attributed to exactly one issue before any of it is fixed,
because a fix spanning two issues would produce a commit that does:

| The finding | Where the fix belongs |
| --- | --- |
| Lands inside the lines one issue introduced | That issue |
| Touches another issue's code, but one commit is to blame — it was correct until this one arrived | The **later** issue |
| Exists only as the composition of two or more, and cannot be fixed within either | A **new issue** in the same batch |

The third row is the honest case rather than a workaround: integration work is
work, and giving it its own issue keeps it revertible on its own. Fixes are then
applied one issue at a time, each checked and published before the next is
started, and each recorded against the finding it answers by replying to the
review comment and resolving it.

**A finding that is not any of those three is either filed or fixed, and which
one is not a matter of taste.** A finding orthogonal to what the epic is for
gets an issue of its own and is left for later — `inventory-tng-nb8.15` through
`nb8.18` came out of PR #20 that way, and `o1uj.8` out of PR #41, all
correctly. But a finding that shows the work so far is **insufficient to close
the epic** may not be deferred. It becomes an issue on the **batch epic**, and
the epic is not complete until it has landed.

The emphasis on *batch* epic is the whole of what makes that binding rather
than hopeful. `scripts/batch-membership.py` reads parentage as batch
membership, and `check-batch.sh` refuses a pull request whose landed issues are
not exactly one epic's children — so an issue parented onto the batch epic and
not landed cannot be merged past. Parent the same issue onto any other epic and
nothing checks it at all.

If a batch produces that third case twice, the issues were one issue. Merge them
in the tracker and rewrite the branch rather than fighting it.

Simplification runs afterwards, over the same pull request, under the same
rules. Its findings are posted to the pull request before they are applied —
"these three issues each grew the same helper" is the third row by construction.

**Each findings comment carries a marker**, on a line of its own anywhere in the
body — `<!-- review-cycle: code-review -->` for the first pass and
`<!-- review-cycle: simplify -->` for the second.

**Quoting one is not posting one.** A marker indented four columns — four
spaces, or a single tab — or inside a fenced block, however deeply that block
is nested inside a longer one, is being *shown* rather than applied and counts
for nothing. That is not pedantry about whitespace: the gate used to look for
the marker anywhere in the body, so a comment that merely mentioned it became
evidence that the pass had run — and a comment reporting that a receipt was
*missing* quoted the marker while explaining the omission and thereby created
it. Write about a marker as freely as you like; put it at the left margin only
when you mean it.

They are what [the landing gate](#when-a-branch-is-ready-to-merge) reads as
evidence that a pass happened, and they are the reason it can record what it
found rather than what it was told. A review that **said something** counts for
the first on its own, so in practice the marker only has to be typed on the
simplify comment. Said something means one of two things: a review submitted
with prose in its body, or an inline comment that opens a thread — which is what
`/code-review --comment` leaves behind.

**Answering a finding is not making one.** Replying to a review thread creates
an entry indistinguishable from a finding in everything the pull request reports
about reviews — same state, same empty body, same author — and replying is what
[the procedure](#fix-one-issue-at-a-time) tells you to do with
findings. So the stage was satisfiable by working through a review that had
never happened. What separates them is whether the comment opens a thread or
answers one, and that is what is read.

It is the same device as the `<!-- batch-contents -->` marker CI posts, for the
same reason: a marker survives rewording and prose does not.

**The two stages do not have the same operator, and an agent has to know which
is which.** Simplification is an agent's own work and it runs without being
told to. Reviewing is not: `/code-review` is a built-in carrying
`disable-model-invocation`, so an agent asking for it is refused, and no
setting in this repository can grant what the refusal withholds. A batch
therefore reaches a point where it needs a person and cannot go further alone.
That pause is the process working.

## Triage before you fix anything

Before asking for review:

```bash
gh pr checks --watch          # green, on the head that will be reviewed
bd list --parent=<epic>       # every issue in the batch is closed
git log --oneline main..HEAD  # one commit per issue, none of them mixed
gh pr ready
```

Then the review pass — `/code-review <pr> --comment`, which a person types
— and one pass over the whole batch. Now resist the urge to start fixing: the
findings arrive in the order the reviewer noticed them, and applying them in
that order is how a commit ends up holding two issues.

Sort every finding first, by the table
[above](#one-review-pass-findings-filed-per-issue). Write the buckets down —
the comment IDs under each issue — before touching the tree. The question to
ask of each finding is not "what is this about?" but **"which single commit
would I revert to make this go away?"**

The third row of that table is the one people get wrong. A finding that needs
code from two issues changed is not a reason to widen a commit; it is a new
issue:

```bash
bd create --parent=<epic> --type=bug --title="..."
```

Creating one is not an admission of failure. The composition genuinely is work
that neither issue did alone, and
[0017](decisions/0017-review-through-pull-requests.md) says why it gets an
issue rather than a wider commit.

### Fix one issue at a time

For each bucket, in the order the issues were landed:

```bash
# only that issue's findings in the tree
<gates for what you touched>
git commit --fixup=<that issue's commit>
git push
```

Then answer the findings where they were made, so the record stays in one
place — reply to the thread and resolve it, or `gh pr comment <pr> --body`.

Do not start the next bucket with the previous one uncommitted. The whole point
of triage was to keep them apart, and a shared working tree undoes it.

### Then simplify, the same way

`/simplify` fans out several review agents of its own and that is what it
costs. No batch is too small to be worth it: the pass that found the most on
PR #80 ran over three rows of tracker prose.

It has no pull request target and posts nothing itself, so post what it found
before applying any of it — otherwise the findings exist only in one session
and the pull request records fixes nobody can trace:

```bash
gh pr comment <pr> --body "$(cat findings.md)"
```

The body carries `<!-- review-cycle: simplify -->` on a line of its own. That
marker is what the gate reads as evidence the pass happened, and without it the
record is refused. Triage identically. Expect most of it to be the third row:
"these three issues each grew the same helper" is a finding no one issue owns,
and the extraction is its own piece of work.

### Then merge

Only once every thread is resolved — [Merging](#merging) says why the order
matters:

```bash
git -c core.editor=true rebase --autosquash origin/main
scripts/check-batch.sh origin/main..HEAD
git push --force-with-lease
gh pr checks --watch
scripts/landing-gate.sh record <pr>
gh pr merge <pr> --rebase
```

The merge does not ask. The bar it has to clear is
[When a branch is ready to merge](#when-a-branch-is-ready-to-merge), and none
of it is yours to weigh.

## Merging

Squash merge and merge commits are disabled on this repository, so the merge
button cannot collapse a batch into a single commit. **Rebase merge** replays
each commit onto `main` individually. Why it is arranged that way rather than
left to discipline is
[0017](decisions/0017-review-through-pull-requests.md).

That setting and what `main` accepts are GitHub's rather than the repository's,
so they are written down as `scripts/repo-settings.sh` rather than left as
something somebody once clicked. `--check` reports what has drifted;
running it without puts it back, which is what to do after adding or renaming a
job in CI that ought to be required. A weekly job runs `--check` and reports,
so drift is found rather than remembered — and it runs on any pull request that
touches CI's job names, because those decide what `main` requires.

So a pull request that adds or renames a job is red on `Repository settings`
until it has merged, and `scripts/landing-gate.sh` reads past that one check
when asking whether a branch is green — `scripts/review_cycle.py` says why,
beside the name. The step it is asking for comes **after** the merge: run
`scripts/repo-settings.sh` then. Every other red check still means what it
says.

One setting is not checked and cannot be: GitHub answers with the merge methods
only for a token holding `contents:write`, which is not a thing to hand a
scheduled job in order to detect a settings change. Merge commits are covered
anyway, because linear history is required and *that* is readable. Squash merge
is watched for by its effect instead — the same job asks whether any recent
commit on `main` closes more than one issue, which is what a squashed batch
looks like once it has landed.

Within a *single* issue, collapsing is fine and often better. Do it on the
branch before merging, and only once every review thread is resolved:

```bash
git commit --fixup=<that issue's commit>   # while fixing
git -c core.editor=true rebase --autosquash origin/main   # at the end
git push --force-with-lease
```

`core.editor` there, not `sequence.editor`: what a fold can stop to ask for is a
*message*, and the todo list a `sequence.editor` would answer for is something a
rebase run without `-i` never writes.

Nothing folds those in on the way to `main` — rebase merge replays them as they
stand — so the branch is not mergeable until you have. While the pull request
is a draft they are the expected state and the check says so; marking it ready
is what claims the branch is meant to merge.

That rebase also brings the branch up to date with `main`, which is required:
the suite has to run again on what will actually land rather than on what was
reviewed beside it.

## When a branch is ready to merge

Five of these `main` enforces itself, and there is no way to merge without
them:

- every required check green **on the head being merged**
- the branch not behind `main`
- every review conversation resolved
- a linear history, which is why the merge is `--rebase`
- no `<!-- do-not-merge -->` posted on a line of its own in the pull request
  body

That last one is how a spike — a pull request that exists to be read at a
meeting and must never land — says so in a way something enforces; why prose
could not hold it is [0020](decisions/0020-who-merges.md#the-marker-a-spike-posts).
It is read by the same rule as the
[review-cycle markers](#one-review-pass-findings-filed-per-issue) and the same
reader, so a body may explain at length why it is blocked, quote the marker in
a sentence, or display it in a fence, without disarming itself. Posting it or
removing it is done by editing the body, and that is enough:
`.github/workflows/look-again.yml` re-runs the check whenever a body changes,
and a push re-runs it too.

Two it cannot see, and they are the ones a person has to hold to:

- `scripts/check-batch.sh origin/main..HEAD` is clean, so every commit belongs
  to exactly one issue — note it accepts a `Refs:`-only commit, so "belongs to"
  is not the same as "closes". The gate runs this itself before a merge, so it
  is checked rather than remembered; what CI asks on every push is the half of
  it that is about structure rather than about being finished, and
  [One review pass](#one-review-pass-findings-filed-per-issue) says why
- the review pass above has actually happened, and its findings have been
  triaged and answered

Nobody is asked to weigh those against anything. A branch that does not meet
them is one to finish, and whoever finished it merges it — an agent working a
batch does not stop to ask, for the same reason a contributor with write access
does not.

**The review cycle is held by a required check**, the `Review cycle` job in
`ci.yml`, which sees every route to a merge rather than only the ones a local
hook meets. Evidence posted after the last push is picked up by
`review-cycle.yml`, which asks CI again rather than making somebody push a
commit to satisfy a checker. What that closed, what it did not, and why the
local gate stays are all in [0020](decisions/0020-who-merges.md).

### The landing gate

`scripts/landing-gate.sh` holds a Claude Code session to the same pair locally.
It ships with the repository and is registered in the tracked
`.claude/settings.json`, so a fresh clone, a fork and every worktree get it.
What it refuses:

| Command | Refused when |
| --- | --- |
| `gh pr merge` | no review cycle is recorded against the exact head being merged; or the branch is not finished — commits still waiting to be folded in, or an issue in the batch epic that has not landed. A pull request named by URL or by branch rather than by number is refused outright: the gate keys everything by number, and read as naming none such a command was judged against the checked-out branch's pull request instead |
| `gh pr ready` | the checks are not green |
| `git push` | it would land on `main` — asked of git, so `git push origin HEAD` from a checked-out `main` is refused and a branch called `batch/main-fix` is not. GitHub refuses it behind the gate too, with `enforce_admins`, required reviews and the required contexts; the gate goes first so the message names the `batch/*` workflow rather than a protection rule |
| `git push --force`, `-f` | always, and `allow_force_pushes: false` stands behind it. `--force-with-lease` is free because the lease is the guard: it refuses if anything arrived since you last fetched, so it cannot overwrite work you have not seen |
| `bd dolt push` | always: it publishes the tracker, and [0029](decisions/0029-the-issue-tracker-is-public.md) makes that public the moment it runs |
| `scripts/repo-settings.sh` writing | always; `--check` compares and reports, and is free. Writing is refused because that script sets the protections the rows above rest on |
| ending a turn | the branch is `batch/*`, its pull request is ready and green, and no cycle is recorded — a nudge rather than a lock: it asks once per head, drafts and red checks are exempt, and it fails open where everything else fails closed |

Each of those used to be a thing an agent was asked to stop and ask about.
What a rule bought was an agent stopping to ask; what a refusal buys is one
that cannot proceed and is told why, including on the day nobody read the
rule. The one thing still asked rather than refused is merging a pull request
whose branch is not a `batch/*` branch: the gate reads the pull request
number, the receipt and the marker, never the branch name, so nothing stands
behind that one but [0020](decisions/0020-who-merges.md).

On a `batch/*` branch, then, nothing needs asking: commit, push, open and
update the pull request, post findings to it, reply to and resolve its
threads, `push --force-with-lease` when collapsing an issue's own commits, and
merge once it clears the bar above. A batch branch is proposed work: it can be
rewritten or thrown away and the repository is untouched, and every step of it
is visible in the pull request as it happens.

Why each of those is drawn where it is — the questions CI stopped asking on
every push, the turn that may not end on a summary, and the one place the gate
stands down — is [0020](decisions/0020-who-merges.md#what-the-gate-refuses-and-why).

```bash
scripts/landing-gate.sh record <pr>     # as its own command; see below
scripts/landing-gate.sh status          # what is recorded, and on what evidence
scripts/landing-gate.sh clear [<pr>]
```

`record` stores what it found on the pull request — the review submissions and
the marker comments from [One review pass](#one-review-pass-findings-filed-per-issue),
by id, author and timestamp. **A receipt is not a permission**: one missing a
stage refuses the merge exactly as no receipt would. Anything pushed afterwards
moves the head, so the merge is refused until you record again.

**`record` must be its own command.** Piping it — `record 12 | tail -2 && gh pr
merge 12` — takes the pipeline's exit status, so the record does not take
effect and the merge is then refused with "No review cycle has been recorded",
which reads like the record failed rather than like the shell ate it.

**It fails closed.** A missing `python3` or `gh`, or a `gh` that is
unauthenticated or rate-limited, makes it *refuse* the command and name the
program it could not find — see the guide's
[Prerequisites](../DEVELOPERS.md#prerequisites) for the three an agent session
needs. Only the commands it guards are affected. A `gh` that is merely slow is
given a shorter deadline than the hook's own, so it refuses in time to say so;
set `GH_DEADLINE` if a slow network makes it refuse honest commands, and raise
the timeout registered in `.claude/settings.json` with it, or the harness
kills the gate before it can speak. `clear <pr>` refuses a receipts file it
cannot parse rather than rewriting it; `clear` with no argument removes the
file whole.

If a rebase stops on a conflict *in `scripts/landing-gate.sh` itself*, the gate
stands down, saying so on stderr, and only then. Markers in its shell half are
a hook error rather than a refusal, and the answer is to resolve them with an
editor; `inventory-tng-ghqk` records why a wrapper was weighed and not taken.

It is a guardrail against forgetting and not a security boundary;
[0020](decisions/0020-who-merges.md) is the decision about what may rest on
one.
