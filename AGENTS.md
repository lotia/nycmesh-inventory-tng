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
[Documentation rules](DEVELOPERS.md#documentation-rules).

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
[DEVELOPERS.md](DEVELOPERS.md#definition-of-done). It is the same standard human
contributors are held to. Nothing in this repository should be workable only by
an agent.

Three parts of it are enforced by tooling and will fail the build, so write the
code to satisfy them rather than discovering them in CI:

- **Every change that adds code adds tests.** Coverage thresholds are part of
  the ordinary test command — see
  [Testing and coverage](DEVELOPERS.md#testing-and-coverage). Do not add a
  coverage exclusion to make a build pass; excluding a file is a decision that
  needs justifying.
- **Style is enforced, not advisory.** See
  [Code style](DEVELOPERS.md#code-style) for the one command per language that
  fixes what can be fixed.
- **Every function is annotated.** `ruff`'s `ANN` rules fail the build on a
  missing argument or return type. `Any` is a permitted annotation in Python, so
  there is always a way to satisfy this — see
  [Typing](DEVELOPERS.md#typing) for the commands that tell you what is missing
  and what a type actually is. Do not reach for `# noqa` to silence it.

## Task tracking

Use `bd` (beads) for all task tracking — not TodoWrite, TaskCreate, or Markdown
checklists. Session hooks already inject the full beads command reference and
stored memories at session start, so that reference is deliberately not repeated
here; run `bd prime` if you need it and it is missing.

## Git

Work reaches `main` the way [Pull requests](DEVELOPERS.md#pull-requests)
describes. Read it before starting a batch; do not reconstruct it from here.

What you may do on your own, and what you must ask for first:

| | |
| --- | --- |
| On a `batch/*` branch, without asking | Commit, push, open and update the pull request, post findings to it, reply to and resolve its threads, `push --force-with-lease` when collapsing an issue's own commits |
| Merging a `batch/*` pull request, without asking | Once it meets [When a branch is ready to merge](DEVELOPERS.md#when-a-branch-is-ready-to-merge): `gh pr merge <pr> --rebase` |
| Ask first, every time | Anything touching `main` directly, a bare `push --force`, `bd dolt push`, and any change to repository or branch settings |

The line is what a mistake costs. A batch branch is proposed work: it can be
rewritten or thrown away and the repository is untouched, and every step of it
is visible in the pull request as it happens. `--force-with-lease` is on the
free side because the lease is the guard — it refuses if anything arrived since
you last fetched, so it cannot overwrite work you have not seen. A bare
`--force` has no such guard and so it asks.

Merging is on the free side for a different reason: none of the bar is yours to
judge, and most of it `main` will not let you waive. The part nothing enforces
is that the review cycle ran, so that one is on your honour —
[0020](docs/decisions/0020-who-merges.md) is why that trade is made and what it
costs.

So the rule is narrow on purpose. A pull request that is not mergeable is one
to finish, never one to ask an exception for; and anything that is not a
`batch/*` branch asks, whatever its state.

**Never `git commit --no-verify`.** It is the one way past the commit-msg hook,
and a guard that is stepped over the moment it refuses something is not a
guard. A refusal is the work; fix what it named.

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
| beads workflow detail | `.agents/skills/beads/SKILL.md` |
| Landing work — what one commit holds, and its message | `.agents/skills/commits/SKILL.md` |
| Running a batch through review, and merging it | `.agents/skills/pull-requests/SKILL.md` |
| Why something is built a certain way | [docs/decisions/](docs/decisions/) |

`CLAUDE.md`, `CODEX.md`, and `GEMINI.md` are symlinks to this file. Edit this
one.
