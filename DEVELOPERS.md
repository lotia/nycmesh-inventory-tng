# Developer Guide

Everything you need to set up a development environment and work on
inventory-tng. If you are new to the project, read
[CONTRIBUTING.md](CONTRIBUTING.md) first — it explains how work gets picked up
and reviewed. For what the project *is*, see [README.md](README.md); for how it
is put together, see [docs/architecture.md](docs/architecture.md).

**This guide is expected to work.** If a command here fails on a clean machine,
that is a bug in the guide — please open an issue or fix it in your next pull
request. See [Definition of Done](#definition-of-done).

---

## Prerequisites

Two things, and nothing else installed globally:

| Tool | Why | Install |
| --- | --- | --- |
| [mise](https://mise.jdx.dev/getting-started.html) | Installs the pinned Python, Node, uv, and Helm versions. No system Python or Node needed. | `curl https://mise.run \| sh` |
| [Docker](https://docs.docker.com/get-started/get-docker/) | Runs PostgreSQL, and optionally the whole stack. | Platform installer |

[Podman](https://podman.io/) works too — substitute `podman compose` for
`docker compose` in the commands below. The images are fully qualified
(`docker.io/library/...`) so Podman resolves them without extra configuration.

Every version this project uses is pinned in [`mise.toml`](mise.toml). That file
is the single source of truth for the toolchain — CI installs from it too.

```bash
git clone git@github.com:lotia/nycmesh-inventory-tng.git
cd nycmesh-inventory-tng
mise trust      # allow mise to use this repo's mise.toml
mise install    # installs Python, Node, uv, Helm at the pinned versions
cp .env.sample .env
```

`.env` holds your local configuration. It is git-ignored, and
[`.env.sample`](.env.sample) documents every variable.

---

## Running it

There are two ways. Use Docker when you want the whole system up; use the native
setup when you are actively editing code and want fast reloads.

### Option A — everything in Docker

Best for a first run, or when you only care about one half of the stack.

```bash
docker compose up --build
```

Frontend on <http://localhost:8080>, API on <http://localhost:8000>. Migrations
run automatically on start.

```bash
docker compose exec backend python manage.py createsuperuser   # an administrator
docker compose logs -f backend                                 # tail logs
docker compose down -v                                         # stop, wipe database
```

Sign in at <http://localhost:8080/accounts/login/>. The first sign-in asks that
account to set up an authenticator app before it can reach anything, because
[decision 0013](docs/decisions/0013-administrator-sign-in.md) requires a second
factor of a local password. Which providers a deployment offers besides that
one is configuration; the variables are in
[deployment](docs/deployment.md#environment-variables).

> **If you had this stack running before August 2026**, run `docker compose down
> -v` once. The database volume now mounts `/var/lib/postgresql` rather than
> `/var/lib/postgresql/data`, because postgres 18 keeps its data in a
> major-version subdirectory. An older volume is not migrated: postgres would
> silently start an empty cluster beside it, and you would wonder where your
> local data went.

### Option B — native, with only PostgreSQL in Docker

Best for day-to-day development: both servers hot-reload.

```bash
docker compose up -d postgres     # database only
```

Then, in one terminal:

```bash
cd backend
uv sync                                    # create .venv from uv.lock
uv run python src/manage.py migrate
uv run python src/manage.py runserver      # http://localhost:8000
```

And in another:

```bash
cd frontend
npm install
npm run dev                                # http://localhost:5173
```

The Vite dev server proxies Django's paths
([which ones](docs/architecture.md#shape)), so the browser talks to a single
origin and you will not hit CORS locally.

### Option C — devcontainer

[`.devcontainer/devcontainer.json`](.devcontainer/devcontainer.json) gives you
the toolchain with nothing installed on your host. Open the repo in VS Code and
choose *Reopen in Container*, or run `devcontainer up --workspace-folder .`.

---

## Repository layout

```
backend/                Django REST Framework API
  pyproject.toml        Python dependencies and tool configuration
  Dockerfile            Backend image (gunicorn)
  src/
    manage.py
    inventory_tng/      Project package: settings, URLs, WSGI/ASGI
    inventory/          Domain app (models, views, management commands, tests)
frontend/               Vite + React + MUI single-page app
  package.json          Dependencies and scripts
  biome.json            Lint and format configuration
  vite.config.ts        Build, dev server, and test/coverage configuration
  playwright.config.ts  Integration test configuration (servers, browser)
  integration/          Integration tests and the scene they run against
  Dockerfile            Frontend image (nginx serving static files)
  nginx.conf.template   Runtime API proxy configuration
infra/helm/             Kubernetes deployment chart
docs/                   Architecture, deployment, and decision records
.agents/skills/         On-demand context for AI coding agents
compose.yaml            Local development stack
mise.toml               Pinned toolchain versions
```

The backend uses a `src/` layout mirroring
[MeshDB](https://github.com/nycmeshnet/meshdb) — see
[docs/architecture.md](docs/architecture.md) for why.

---

## Common tasks

All backend commands run from `backend/`, all frontend commands from `frontend/`.

### Backend

| Task | Command |
| --- | --- |
| Run the dev server | `uv run python src/manage.py runserver` |
| Run tests (with coverage) | `uv run pytest` |
| Lint and format | `uv run ruff check --fix . && uv run ruff format .` |
| Type check | `uv run ty check src` |
| Make migrations | `uv run python src/manage.py makemigrations` |
| Apply migrations | `uv run python src/manage.py migrate` |
| Open a Django shell | `uv run python src/manage.py shell` |
| Add a dependency | `uv add <package>` |
| Add a dev-only dependency | `uv add --group dev <package>` |

`uv add` updates both `pyproject.toml` and `uv.lock`. **Commit both.**

### Frontend

| Task | Command |
| --- | --- |
| Run the dev server | `npm run dev` |
| Build for production | `npm run build` |
| Lint and format check | `npm run lint` |
| Fix lint and formatting | `npm run lint:fix` |
| Type check | `npm run typecheck` |
| Run tests (with coverage) | `npm test` |
| Add a dependency | `npm install <package>` |

`npm install` updates `package-lock.json`. **Commit it.**

### Deployment chart

| Task | Command |
| --- | --- |
| Lint the chart | `helm lint infra/helm/inventory-tng` |
| Preview rendered manifests | `helm template test infra/helm/inventory-tng` |

---

## Database and migrations

PostgreSQL 18, reached through a single `DATABASE_URL`. Django settings read it
via `django-environ`; nothing else configures the database.

When you change a model:

```bash
uv run python src/manage.py makemigrations
uv run python src/manage.py migrate
```

Commit the generated migration file alongside the model change. In production,
migrations run as a separate Kubernetes Job before new pods start, never from a
running web pod — see [docs/deployment.md](docs/deployment.md).

---

## Code style

Style is not a matter of taste here — it is enforced, and CI fails on it. Both
languages use one fast tool that covers linting *and* formatting, so there is a
single configuration file per language and nothing to argue about in review.

| | Backend (Python) | Frontend (TypeScript) |
| --- | --- | --- |
| Lint + format | [ruff](https://docs.astral.sh/ruff/) | [Biome](https://biomejs.dev/) |
| Type check | [ty](https://github.com/astral-sh/ty) | `tsc --noEmit` |
| Configuration | `backend/pyproject.toml` | `frontend/biome.json`, `frontend/tsconfig.json` |
| Check everything | `uv run ruff check . && uv run ruff format --check . && uv run ty check src` | `npm run lint && npm run typecheck` |
| Fix what can be fixed | `uv run ruff check --fix . && uv run ruff format .` | `npm run lint:fix` |

Run the fixer before you open a pull request and the check will pass.

Both toolchains follow the same principle: one binary replacing what used to be
three or four. On the Python side ruff does the work of black, isort, and
flake8, and `ty` type checks; both come from [Astral](https://astral.sh/),
alongside `uv`, which manages the environment. On the TypeScript side Biome does
the work of ESLint and Prettier. Rationale is in
[docs/decisions/0004-python-tooling.md](docs/decisions/0004-python-tooling.md)
and
[docs/decisions/0006-frontend-tooling.md](docs/decisions/0006-frontend-tooling.md).

Notable rules that are deliberate rather than default: Python targets a
120-character line and enables the bugbear, pyupgrade, and Django rule sets;
TypeScript forbids `any` and non-null assertions, so a type error has to be
solved rather than silenced.

---

## The API schema

The API describes itself. `/api` lists its entry points, `/api/docs` renders
the full description for humans, and `/api/schema` serves that description as
an OpenAPI 3.1.1 document. The same document is committed at
[`backend/openapi.yaml`](backend/openapi.yaml) so it can be read, diffed and
consumed without running anything.

The two are not the same list, and the division is deliberate. The index says
where the collections are, so a client does not need the URL layout in
advance; the schema says what each one accepts, and covers what a list of
links cannot — the endpoints addressed per row, and the methods other than
`GET`. Start at `/api`, follow `schema`, and everything is reachable from
there.

**If you change an endpoint or a payload, regenerate it in the same change:**

```
cd backend && uv run python src/manage.py spectacular --file openapi.yaml
```

You do not have to remember this. `uv run pytest` generates the schema afresh
and fails if it differs from the committed file, so drift is caught locally and
in CI identically — the same approach as the coverage threshold. The failure
message contains the command above.

A second test requires every operation to document a response body, so the
schema cannot quietly decay into a list of paths with no payloads. Use
`@extend_schema` on any view whose response `drf-spectacular` cannot infer.

Why 3.1.1 rather than 3.0 or 3.2 is in
[decision 0010](docs/decisions/0010-openapi-version.md).

---

## Typing

**Every function you write is annotated.** Arguments and return types, in both
languages. This is not advisory: `ruff` enforces it on the Python side through
the `ANN` rule set, and CI fails on it, exactly as it does for tests and
formatting. TypeScript gets this from the compiler already.

### The bar is "a type is present", not "the best possible type"

*This latitude is Python-only.* TypeScript infers return types reliably and
`any` stays forbidden there, as [Code style](#code-style) says — on the frontend
`any` is an escape from the type system, whereas in Python `Any` is the on-ramp
onto it.

Types here exist to make the code readable and to let editors help you. They are
not a puzzle you have to solve before your contribution counts.

```python
from typing import Any

def summarise(rows: Any) -> Any:      # fine. passes. ship it.
    ...

def summarise(rows: list[dict[str, Any]]) -> dict[str, int]:   # better, later
    ...
```

`Any` is deliberately allowed — the `ANN401` rule that would forbid it is
switched off on purpose. If you cannot work out the right type, write `Any`,
open the pull request, and someone will suggest something tighter. That is a
review conversation, not a blocker. Reviewers: asking for a more specific type
is a suggestion, never a rejection.

The reasoning behind requiring annotations at all is in
[decision 0009](docs/decisions/0009-type-annotations-required.md).

### Getting help from the machinery

Three commands, in the order you will want them.

| I want to… | Run |
| --- | --- |
| See everything that is missing a type | `uv run ruff check --select ANN .` |
| Add the obvious ones automatically | `uv run ruff check --select ANN --fix --unsafe-fixes .` |
| Ask what type something actually is | put `reveal_type(x)` on a line, then `uv run ty check src` |

The second adds return annotations such as `-> None` where it can prove them.
It is "unsafe" only in ruff's sense that it edits annotations rather than
whitespace; the scoping to `--select ANN` keeps it from touching anything else.
Run `uv run ruff format .` afterwards.

The third is the one worth remembering. `reveal_type()` needs no import and is
understood by the type checker directly:

```python
def get(self, request: Request) -> Response:
    with connection.cursor() as cursor:
        reveal_type(cursor)        # ty prints: `CursorWrapper`
```

```
info[revealed-type]: Revealed type
 --> src/inventory/views.py:22:21
  |
  |         reveal_type(cursor)
  |                     ^^^^^^ `CursorWrapper`
```

Copy the answer into the annotation and delete the `reveal_type` line. This
works for any expression, and it is the fastest way to type a Django or DRF
object whose type you would otherwise have to go looking for.

### What the checker cannot see

Django generates some attributes at runtime. `django-stubs` describes them
through a mypy plugin, and `ty` does not run plugins yet, so it reports them as
missing even though the code is correct:

| Pattern | Use instead |
| --- | --- |
| `obj.get_kind_display()` | `obj.Kind(obj.kind).label` — explicit and typed |
| `item.identifiers`, `item.history` (reverse accessors, history managers) | Nothing better exists. Add `# ty: ignore[unresolved-attribute]` |

Suppress with `# ty: ignore[<rule>]` on the line, naming the rule rather than
silencing everything. Most are `unresolved-attribute`, from the table above;
the rest are places a third-party stub is narrower than the function it
describes, and each one carries a comment saying which stub and why.

A suppression is a statement that the checker is wrong, so if you are not sure
it is, it is a bug worth looking at instead — and the comment beside it is what
lets the next reader tell the two apart. This is the cost of `ty` being pre-1.0
and is expected to shrink — tracked as `inventory-tng-61b`.

### Editor setup

`ty` ships a language server, so your editor can show inferred types as you
type rather than at check time. `.vscode/settings.json` in this repository
configures it, and the devcontainer installs the extensions
(`astral-sh.ty`, `charliermarsh.ruff`). Outside VS Code, point your editor's
LSP client at `uv run ty server`.

Coding agents should use the same three commands above — they are deterministic
and their output is stable enough to act on directly.

### Learning Python typing

If annotations are new to you, these are the ones worth having open. The first
is the most useful by a distance:

- [mypy type-system cheat sheet](https://mypy.readthedocs.io/en/stable/cheat_sheet_py3.html)
  — one page, practical, and applies to `ty` just as well despite the name
- [Python typing guides](https://typing.python.org/en/latest/guides/index.html)
  — the official introduction, longer form
- [`typing` module reference](https://docs.python.org/3/library/typing.html)
  — what is available to import
- [django-stubs](https://github.com/typeddjango/django-stubs) — how Django's own
  types are described, when you need to know what a queryset or a request is

---

## Testing and coverage

**Every change that adds code adds tests for it, and CI fails if it does not.**

### How to run them

```bash
cd backend  && uv run pytest    # pytest + coverage
cd frontend && npm test         # vitest + coverage
```

Coverage is built into both commands rather than being a separate CI-only step.
A local run and a CI run enforce exactly the same rules, so the build cannot
fail on something you had no way to see.

### Integration tests

```bash
cd frontend && npm run test:integration    # Playwright, a real browser
```

Separate from the commands above on purpose, and not part of the coverage
threshold. They start a real Django, a real Vite dev server and a real browser,
and assert through a real browser's cookie jar and origin checks.

They are not a production rehearsal: they run the dev server rather than the
nginx image, with `DJANGO_DEBUG` on, so the secure-cookie and HSTS behaviour
that only appears with `DEBUG` off is still untested.

They exist because the unit tests cannot see a whole class of bug. Django's
test client exempts itself from CSRF and jsdom is not a browser, so both suites
were once green against an API no browser could write to — twice over, in fact:
nothing set a CSRF cookie, and the dev server's origin was not trusted. Only
this suite can fail on the second, and it is the only one that exercises either
through a real browser.

The scanner is here for the same reason. Self-hosting the decoder's `.wasm` is
a non-optional constraint of
[decision 0011](docs/decisions/0011-qr-batch-scanning.md), and whether it holds
is a question about a request a browser makes — jsdom has no camera to open and
the CDN's URL is still in the built JavaScript as a default nothing reaches, so
neither the unit suite nor a look at the bundle can answer it. The camera is
opened against Chromium's fake device, which needs no flag of yours: the spec
asks for it.

One of them decodes a symbol through a browser, which no other test can. That
the printed sticker is readable at all is settled in the backend suite, which
decodes what the label generator drew with a different implementation from the
one that drew it; what nothing could reach until now is the handoff between a
camera frame and the decoder, where a scanner can decode nothing while every
other suite stays green. `integration/decodes.spec.ts` closes that, and its
header says what it found when it first ran. The clip it films is generated
during the run rather than committed, and needs no ffmpeg and no container;
how, and why, is in `frontend/integration/qrVideo.ts`.

They need Docker (for PostgreSQL) and a one-off browser download:

```bash
cd frontend && npx playwright install chromium
```

Servers, migrations and the fixed test scene are all handled by the suite
itself, so there is no separate setup step, and a server you already have
running is reused without changing that. The scene comes from
`manage.py seed_integration_data`, which creates a login whose password *and*
whose TOTP secret are written down in this repository and so refuses to run
unless `DJANGO_DEBUG` is on *and* it is passed the flag that acknowledges that.
Running it by hand means typing that flag; the command says why.

The suite signs in through the local password path of
[decision 0013](docs/decisions/0013-administrator-sign-in.md) and completes the
real second factor, computing the code from that published secret with `pyotp`.
An OAuth round trip to Google or Slack cannot be completed from CI, so the
provider paths are covered in the backend suite instead, where a callback can
be finished without dialling anybody.

They write to your development database rather than a throwaway one, because
the point is to exercise the servers you actually run.

### What breaks the build

Both of these fail the command with a non-zero exit code, and therefore fail CI:

1. **Any failing test.**
2. **Coverage below the threshold** — currently **90%** on both sides, applied
   to lines, and additionally to branches, functions, and statements on the
   frontend.

### Why 90% and not 100%

Chasing 100% pushes people into writing tests for code that cannot
meaningfully break, which wastes effort and produces tests nobody maintains. The
number is not the point.

The real work is done by the **exclusion lists**, which decide what counts as
code worth covering. Everything not excluded is expected to be tested, and 90%
leaves only a little room for the genuinely awkward case.

| Excluded | Where | Why |
| --- | --- | --- |
| `manage.py`, `wsgi.py`, `asgi.py` | `backend/pyproject.toml` → `[tool.coverage.run] omit` | Entry points run by Django or the server, never by tests |
| `settings.py` | same | Declarative configuration. Every test imports it, so counting it would inflate the percentage without testing any behaviour |
| `migrations/` | same | Generated by `makemigrations` |
| `src/main.tsx` | `frontend/vite.config.ts` → `test.coverage.exclude` | Bootstrap that mounts React onto the DOM; no behaviour of its own |
| `src/theme.ts` | same | Declarative configuration, as with `settings.py` |

If you are tempted to add an exclusion, say why in the pull request. Excluding a
file is a decision about what does not need testing, and it deserves the same
scrutiny as the code itself. Lowering a threshold needs a reason too; raising
one needs nothing but a green build.

### Writing tests

| | Backend | Frontend |
| --- | --- | --- |
| Framework | [pytest](https://docs.pytest.org/) with `pytest-django` | [Vitest](https://vitest.dev/) |
| Location | `backend/src/inventory/tests/` | next to the code, as `*.test.tsx` |
| Component testing | — | [Testing Library](https://testing-library.com/docs/react-testing-library/intro/) |
| Database access | needs `@pytest.mark.django_db` | — |

Test behaviour through the public surface — an API endpoint's response, what a
user sees rendered — rather than asserting on internals. Tests written that way
survive refactoring, which is the only reason they are worth having.

---

## Documentation rules

Two rules govern documentation in this repository. They exist because NYC Mesh
is a volunteer community: stale or scattered setup docs are the single biggest
thing standing between a willing volunteer and a first contribution.

### 1. One topic, one place

Every piece of documentation lives in exactly **one** file. Everything else
links to it with a relative Markdown link.

If you find yourself explaining something that is already explained elsewhere,
delete your copy and link instead. Two copies of an instruction means one of
them is wrong within a month, and the reader has no way to tell which.

Where each topic lives:

| Topic | Canonical location |
| --- | --- |
| What the project is, quickstart | [README.md](README.md) |
| Development setup and workflow | This file |
| Code style and linting | [Code style](#code-style) |
| API schema and how it stays current | [The API schema](#the-api-schema) |
| Typing requirements | [Typing](#typing) |
| Testing and coverage requirements | [Testing and coverage](#testing-and-coverage) |
| What one commit contains, and its message | [Commits](#commits) |
| How work is reviewed and reaches `main` | [Pull requests](#pull-requests) |
| How to contribute | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Architecture and technology choices | [docs/architecture.md](docs/architecture.md) |
| Inventory data model | [docs/data-model.md](docs/data-model.md) |
| Deployment | [docs/deployment.md](docs/deployment.md) |
| Why a decision was made | [docs/decisions/](docs/decisions/) |
| The investigation behind a decision | [docs/briefs/](docs/briefs/) |
| Rules for AI coding agents | [AGENTS.md](AGENTS.md) |
| Configuration variables | [.env.sample](.env.sample) |
| Toolchain versions | [mise.toml](mise.toml) |

Two jobs in CI keep that arrangement from rotting, and both can be run by hand:

```bash
scripts/check-docs.sh          # the same passage in two files
scripts/check-docs.sh --words 8   # stricter, if you are hunting one down
```

A link checker catches a link whose target you renamed. `check-docs.sh` catches
the other half — an explanation pasted into a second file rather than linked to
— by comparing prose in runs of twelve words. Code blocks, tables, headings and
link text are not prose and are left out, so a repeated command or a repeated
citation is not reported.

It reads every Markdown file **and the comments of the scripts in `scripts/`**,
because a script's header is documentation of that script and is the easiest
place to re-derive something this guide already says. It does not read
application code: a docstring beside the invariant it enforces is the code
explaining itself, which is a different thing from a topic having two homes.

When it objects, the fix is almost always to delete one copy and link to the
other. `scripts/check-docs.allow` exists for the rare passage that is genuinely
meant to appear twice; its own header says how an entry is written and when one
is warranted. An allowance covers a named pair of files, so a third copy is
still reported, and one that stops matching anything is reported too — a
baseline cannot outlive the repetition it recorded.

### 2. Docs change with the code that invalidates them

Documentation is updated in the **same** change as the code, not afterwards.
This is part of [Definition of Done](#definition-of-done) below.

---

## Definition of Done

A change is not finished — and an issue must not be closed — until all of these
hold:

- [ ] Tests and coverage thresholds pass (`uv run pytest`, `npm test`) —
      see [Testing and coverage](#testing-and-coverage)
- [ ] Lint, format, and type checks pass — see [Code style](#code-style)
- [ ] Every function you added or changed is annotated — see [Typing](#typing)
- [ ] New behaviour has a test. If you added code that coverage counts, it is
      covered; if you excluded something, the exclusion is justified in the
      pull request
- [ ] **Documentation is consistent with the change.** If the change alters
      setup steps, commands, environment variables, architecture, the API
      surface, or the deployment procedure, the canonical document for that
      topic is updated in the same pull request.
- [ ] A decision that future readers would ask "why?" about has a record in
      [docs/decisions/](docs/decisions/)

The documentation item is not a formality and not a follow-up ticket. A change
that leaves the docs describing the old behaviour is incomplete, because the
next person to read them will be misled.

---

## Issue tracking

Work is tracked with [beads](https://github.com/steveyegge/beads), a CLI issue
tracker that stores issues in the repository:

```bash
bd ready                 # issues ready to work on, nothing blocking them
bd show <id>             # full detail
bd update <id> --claim   # claim it
bd close <id>            # done (see Definition of Done first)
```

You do not have to use beads to contribute. GitHub issues and pull requests work
fine — see [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Commits

**One issue per commit.** A commit contains work from exactly one issue and
nothing else, so that it can be read, reviewed, reverted and bisected as the
unit of work it claims to be. An issue may take more than one commit where that
genuinely reads better; no commit may ever take more than one issue.

That rule settles the awkward cases too:

- Documentation the change itself made wrong is part of the change — that is
  the [Definition of Done](#definition-of-done), not a separate concern.
- A fault you noticed on the way but did not cause is its own issue and its own
  commit, however small and however tempting. A one-line fix riding along is
  the commonest way a commit stops being one thing.
- A defect a review finds in the change is part of the change. A defect it
  finds in code the change did not touch is not.

### The message

```
abc: Summarise the change in the imperative

What changed, what was added, what was removed — in enough detail that
somebody reading the history a year from now knows what this did to the
repository, and no more.

Closes: inventory-tng-abc
```

- **The summary line names its issue, then says what changed in at most 50
  characters**, in the imperative mood ("Extract the decode loop", not
  "Extracted" or "Extracting"), with no full stop. The 50 is measured on the
  prose after the `abc: ` prefix, because it is a size check as much as a
  title: work that cannot be summarised in 50 characters is usually more than
  one issue, and the answer is to split the issue rather than to lengthen the
  line. Only the distinguishing part of a bead ID is used, for the reason
  [0017](docs/decisions/0017-review-through-pull-requests.md) gives.
- **The body says what changed**, wrapped at 72 columns. It is not a diary:
  how the work was done, what was tried first and what a review said are not
  what a reader of the history needs. A review's findings belong in the
  [pull request](#pull-requests). *Why* something is built the way it is
  belongs in [docs/decisions/](docs/decisions/), and is linked rather than
  retold.
- **A trailer naming that same issue in full** — `Closes: inventory-tng-abc` on
  the commit that completes it, `Refs: inventory-tng-abc` on one that only
  advances it. The colon is not decoration: git parses `Key: value` and nothing
  else, so without it `git log --format='%(trailers)'` finds nothing and only a
  bespoke script can answer "what did this issue do?". GitHub accepts the colon
  for its own closing keywords, so `Closes: #123` names a GitHub issue, because
  [beads is not required to contribute](#issue-tracking). Every trailer on a
  message names the *same* issue, and at most one closes it; that is what makes
  "one issue per commit" something a machine can check. Follow-up issues raised
  along the way may be created in the same commit — noticing work is honest
  work — but only one issue may be *closed* by it.

### Several issues at once

Work them one at a time on a batch branch and land each as it is finished. The
pull request is the unit of review; the commit stays the unit of work. See
[Pull requests](#pull-requests).

### Checking it

Write the message to a file and hand it over, so that what is checked is what
will land:

```bash
scripts/check-commit.sh <message-file>
scripts/check-commit.sh --amend <message-file>   # replacing the last one
```

It objects if more than one issue is closed by what is staged, if the message
and the tracker disagree about which, or if the summary line breaks the rules
above. A guardrail rather than a gate. To have it run every time, add it to the
hooks beads already installs — `core.hooksPath` is taken, so a second hooks
directory would turn beads' own git integration off:

```bash
ln -s ../../scripts/check-commit.sh .beads/hooks/commit-msg
```

History before this section predates it, and is not the example to follow:
several commits close five issues each.

---

## Pull requests

Nothing reaches `main` except through a pull request, and `main` is protected so
that there is no other way in.

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

### Finish, then publish, then review

Each issue is finished to the [Definition of Done](#definition-of-done) and
published before anything is reviewed. Nothing is reviewed that has not already
passed its own checks:

1. Land the issue as its own commit — see [Commits](#commits).
2. Push to the batch branch. The first push opens the pull request as a draft,
   so CI runs per issue rather than once at the end.
3. Repeat for the next issue in the batch.

Mark the pull request ready when the batch is complete and CI is green.

### One review pass, findings filed per issue

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

If a batch produces that third case twice, the issues were one issue. Merge them
in the tracker and rewrite the branch rather than fighting it.

Simplification runs afterwards, over the same pull request, under the same
rules. Its findings are posted to the pull request before they are applied —
"these three issues each grew the same helper" is the third row by construction.

### Merging

Squash merge and merge commits are disabled on this repository, so the merge
button cannot collapse a batch into a single commit. **Rebase merge** replays
each commit onto `main` individually. Why it is arranged that way rather than
left to discipline is
[0017](docs/decisions/0017-review-through-pull-requests.md).

That setting, and what `main` accepts, are GitHub's rather than the
repository's, so they are written down as `scripts/repo-settings.sh` rather than
left as something somebody once clicked. `--check` reports what has drifted;
running it without puts it back. Run it after adding a job to CI that ought to
be required.

Within a *single* issue, collapsing is fine and often better. Do it on the
branch before merging, and only once every review thread is resolved:

```bash
git commit --fixup=<that issue's commit>   # while fixing
git rebase -i --autosquash origin/main     # once, at the end
git push --force-with-lease
```

Nothing folds those in on the way to `main` — rebase merge replays them as they
stand — so the branch is not mergeable until you have. While the pull request
is a draft they are the expected state and the check says so; marking it ready
is what claims the branch is meant to merge.

That rebase also brings the branch up to date with `main`, which is required:
`main` accepts nothing that is behind it or that has a check outstanding, so the
suite runs again on what will actually land. The merge stays blocked until it is
green and every conversation is resolved.

---

## Troubleshooting

**`mise: command not found` after installing.** Add the shim directory to your
shell as printed by the installer, then restart your shell.

**`django.core.exceptions.ImproperlyConfigured: Set the DJANGO_SECRET_KEY
environment variable`.** You have no `.env`. Run `cp .env.sample .env`. The
settings module deliberately has no fallback secret, so a misconfigured
deployment fails at boot instead of running with a known-public key.

**`connection refused` on port 5432.** PostgreSQL is not running. Start it with
`docker compose up -d postgres`.

**Frontend loads but every API call 404s.** You are on the Vite dev server
(port 5173) with no backend running. Start Django, or use
<http://localhost:8080> from the Docker stack.

**Port already in use.** Something else holds 5432, 8000, 8080, or 5173. Stop it,
or change the published port in [`compose.yaml`](compose.yaml).
