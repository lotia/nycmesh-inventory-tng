# Instructions for AI coding agents

Applies to every agent working in this repository. It is deliberately short:
anything only some tasks need lives elsewhere and is loaded on demand.

The project is a Django REST API (`backend/`) and a React SPA (`frontend/`) for
NYC Mesh inventory. Read [README.md](README.md) for what it is and
[DEVELOPERS.md](DEVELOPERS.md) for how to build and run it. Do not restate
either here.

## Three rules that are not negotiable

**1. One topic, one place.** Every piece of documentation lives in exactly one
file; everywhere else links to it. Never paste an explanation into a second
location — link instead. Canonical locations are listed in
[Documentation rules](CONTRIBUTING.md#documentation-rules).

**2. Docs change with the code.** An issue must not be closed while
documentation contradicts the code. Before closing anything, check whether the
change altered setup steps, commands, environment variables, architecture, the
API surface, or deployment — and if so, update that topic's canonical document
in the same change. This is an acceptance criterion, never a follow-up ticket.
NYC Mesh is a volunteer community; stale setup docs are the biggest barrier to
new contributors.

**3. Never write cryptography.** Not a primitive, not a construction, not
"just" a key-derivation step, a nonce scheme, a padding mode, a signature
envelope, a token format, or a constant-time comparison. Three routes, in
order, and it is an order rather than a menu:

1. **Use an established library** — well known, actively maintained, widely
   deployed, independently audited. Here that means Django's own
   `django.core.signing`, `django.contrib.auth.hashers` and
   `django.utils.crypto.constant_time_compare` first, because they are already
   dependencies and already carry this project's threat model; then
   pyca/`cryptography`, `hmac`, `hashlib`, `secrets`. In a browser it is
   WebCrypto and nothing else. `inventory_tng.debugging` already signs tokens
   this way — copy it rather than inventing beside it.
2. **Otherwise, a thin wrapper over that library's public API.** Thin means it
   arranges calls, validates inputs, and names the operation in this project's
   vocabulary. It never implements the algorithm, and it never reaches for a
   private or underscore-prefixed name. Once a wrapper contains arithmetic on
   bytes it has stopped being a wrapper, and route 3 applies.
3. **Otherwise stop, and say so loudly.** Do not prototype it to see. Do not
   leave a TODO and carry on. Do not put it behind a flag. Say that the work
   has hit this rule, name what was needed and why neither route above reached
   it, and wait for a person. That decision is made deliberately, by a human,
   and its reasoning goes in [docs/decisions/](docs/decisions/).

This rule is absolute because the failure is silent. Cryptographic code that is
wrong produces output byte-shaped exactly like output that is right, so it
passes every test in this repository and no review here is qualified to catch
it. The safety net the rest of this file relies on — tests, coverage
thresholds, a reviewer — does not exist for this one category. And the cost of
being wrong is not borne by whoever wrote it; it is borne by the volunteers
whose names, addresses and whereabouts the code was protecting.

So reaching route 3 is not a setback to work around. It is this rule doing the
only job it has.

## Definition of Done

Use the checklist in
[CONTRIBUTING.md](CONTRIBUTING.md#definition-of-done). It is the same standard human
contributors are held to. Nothing in this repository should be workable only by
an agent.

Three parts of it are enforced by tooling and will fail the build, so write the
code to satisfy them rather than discovering them in CI:

- **Every change that adds code adds tests.** Coverage thresholds are part of
  the ordinary test command — see
  [Testing and coverage](docs/testing.md#testing-and-coverage). Do not add a
  coverage exclusion to make a build pass; excluding a file is a decision that
  needs justifying.
- **Style is enforced, not advisory.** See
  [Code style](docs/code-style.md#code-style) for the one command per language that
  fixes what can be fixed.
- **Every function is annotated.** `ruff`'s `ANN` rules fail the build on a
  missing argument or return type. `Any` is a permitted annotation in Python, so
  there is always a way to satisfy this — see
  [Typing](docs/code-style.md#typing) for the commands that tell you what is missing
  and what a type actually is. Do not reach for `# noqa` to silence it.

## Task tracking

Use `bd` (beads) for all task tracking — not TodoWrite, TaskCreate, or Markdown
checklists. Session hooks already inject the full beads command reference and
stored memories at session start, so that reference is deliberately not repeated
here; run `bd prime` if you need it and it is missing.

**Every bead you write is published.** `.beads/issues.jsonl` is committed and
this repository is public, so a bead is readable by anybody the moment it is
pushed, and it stays in the history. That is deliberate —
[0029](docs/decisions/0029-the-issue-tracker-is-public.md) — and it names the
four things that must never go in one. Read it before filing anything that
touches a person, a credential, or a weakness nothing has fixed yet.

## Git

Work reaches `main` the way [Pull requests](docs/pull-requests.md) describes,
and that page is written for people: read it before starting a batch, and do
not reconstruct it from here. What you may do on a `batch/*` branch without
asking, what [the landing gate](docs/pull-requests.md#the-landing-gate)
refuses so that nobody has to remember to ask, and why whoever finishes a
mergeable batch merges it — agent or not — are all there and in
[0020](docs/decisions/0020-who-merges.md).

Three things are said here because nothing else will say them to you.

**The main checkout is the person's, so work in a worktree of your own.**
Every session on this machine sees the same working tree, and `git status`
there lists what anybody did — so the gate refuses `git commit` there, and
`EnterWorktree` is the first thing a session does before landing anything.
Why, and the one case it cannot get you out of, is
[Staging](docs/commits.md#staging).

**A pull request whose body posts `<!-- do-not-merge -->` on a line of its own
is never merged, whatever its state, and the answer is never yours to
overturn.** An agent meeting a red check it has no rule for will set about
making it green, and nothing about such a pull request looks like an
exception: marking it ready and merging it is what following the documented
flow looks like. What the marker is and how it is read is
[When a branch is ready to merge](docs/pull-requests.md#when-a-branch-is-ready-to-merge).

**Never `git commit --no-verify`.** It is the one way past the commit-msg hook,
and a guard that is stepped over the moment it refuses something is not a
guard. A refusal is the work; fix what it named. A person may skip the hooks,
and [docs/git-hooks.md](docs/git-hooks.md) says how; that page is not for you.

When finishing, report changed files, what you validated, and the commands you
would run next.

## Shell

Use non-interactive flags so a prompt cannot hang the session: `cp -f`, `mv -f`,
`rm -f`, `rm -rf`, `apt-get -y`, `ssh`/`scp -o BatchMode=yes`.

## Load on demand

Read these only when the task needs them. Do not load them pre-emptively.

| Working on | Load |
| --- | --- |
| `backend/` — Django, DRF, models, migrations | `.agents/skills/django-backend/SKILL.md` |
| `frontend/` — React, MUI, Vite | `.agents/skills/react-frontend/SKILL.md` |
| Images, Helm chart, Kubernetes, CodeNOW | `.agents/skills/deploy/SKILL.md` |
| beads workflow detail | `.agents/skills/beads/SKILL.md`, then [docs/issue-tracking.md](docs/issue-tracking.md) |
| Landing work — what one commit holds, its message, and the split | [docs/commits.md](docs/commits.md); `.agents/skills/commits/SKILL.md` holds the one agent-only habit |
| Running a batch through review, and merging it | [docs/pull-requests.md](docs/pull-requests.md); `.agents/skills/pull-requests/SKILL.md` holds what is an agent's alone |
| Code style and typing, and the commands that fix what can be fixed | [docs/code-style.md](docs/code-style.md) |
| Running the suites, and what CI executes | [docs/testing.md](docs/testing.md), [docs/ci.md](docs/ci.md) |
| Why something is built a certain way | [docs/decisions/](docs/decisions/) |
| A tool writing into this repository — its hooks, its managed blocks, the config it rewrites | [0032](docs/decisions/0032-a-tools-defaults-are-not-this-projects-rules.md) |

`CLAUDE.md`, `CODEX.md`, and `GEMINI.md` are symlinks to this file. Edit this
one.
