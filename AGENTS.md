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

Two parts of it are enforced by tooling and will fail the build, so write the
code to satisfy them rather than discovering them in CI:

- **Every change that adds code adds tests.** Coverage thresholds are part of
  the ordinary test command — see
  [Testing and coverage](DEVELOPERS.md#testing-and-coverage). Do not add a
  coverage exclusion to make a build pass; excluding a file is a decision that
  needs justifying.
- **Style is enforced, not advisory.** See
  [Code style](DEVELOPERS.md#code-style) for the one command per language that
  fixes what can be fixed.

## Task tracking

Use `bd` (beads) for all task tracking — not TodoWrite, TaskCreate, or Markdown
checklists. Session hooks already inject the full beads command reference and
stored memories at session start, so that reference is deliberately not repeated
here; run `bd prime` if you need it and it is missing.

## Git

Do not commit, push, or run `bd dolt push` unless explicitly asked. When
finishing, report changed files, what you validated, and the commands you would
run next.

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
| Why something is built a certain way | [docs/decisions/](docs/decisions/) |

`CLAUDE.md`, `CODEX.md`, and `GEMINI.md` are symlinks to this file. Edit this
one.
