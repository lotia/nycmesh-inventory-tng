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
docker compose exec backend python manage.py createsuperuser   # admin login
docker compose logs -f backend                                 # tail logs
docker compose down -v                                         # stop, wipe database
```

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

The Vite dev server proxies `/api` to Django, so the browser talks to a single
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
    inventory/          Domain app (models, views, tests)
frontend/               Vite + React + MUI single-page app
  package.json          Dependencies and scripts
  biome.json            Lint and format configuration
  vite.config.ts        Build, dev server, and test/coverage configuration
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
| Testing and coverage requirements | [Testing and coverage](#testing-and-coverage) |
| How to contribute | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Architecture and technology choices | [docs/architecture.md](docs/architecture.md) |
| Deployment | [docs/deployment.md](docs/deployment.md) |
| Why a decision was made | [docs/decisions/](docs/decisions/) |
| Rules for AI coding agents | [AGENTS.md](AGENTS.md) |
| Configuration variables | [.env.sample](.env.sample) |
| Toolchain versions | [mise.toml](mise.toml) |

CI runs a link checker, so a link to a file you renamed will fail the build
rather than rot silently.

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
