# Instructions for AI coding agents

Applies to every agent working in this repository. It is deliberately short:
anything only some tasks need lives elsewhere and is loaded on demand.

The project is a Django REST API (`backend/`) and a React SPA (`frontend/`) for
NYC Mesh inventory. Read [README.md](README.md) for what it is and
[DEVELOPERS.md](DEVELOPERS.md) for how to build and run it. Do not restate
either here.

## Two rules that are not negotiable

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
| Ask first, every time | Merging, anything touching `main` directly, a bare `push --force`, `bd dolt push`, and any change to repository or branch settings |

The line is what a mistake costs. A batch branch is proposed work: it can be
rewritten or thrown away and the repository is untouched, and every step of it
is visible in the pull request as it happens. `--force-with-lease` is on the
free side because the lease is the guard — it refuses if anything arrived since
you last fetched, so it cannot overwrite work you have not seen. A bare
`--force` has no such guard and so it asks.

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
