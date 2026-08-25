# Developer Guide

Everything you need to set up a development environment and work on
inventory-tng. If you are new to the project, read
[CONTRIBUTING.md](CONTRIBUTING.md) first — it explains how work gets picked up
and reviewed. For what the project *is*, see [README.md](README.md); for how it
is put together, see [docs/architecture.md](docs/architecture.md).

**This guide is expected to work.** If a command here fails on a clean machine,
that is a bug in the guide — please open an issue or fix it in your next pull
request. See [Definition of Done](#definition-of-done). CI runs the setup below
on a clean machine every push, so that expectation is checked rather than
hoped for: [What CI proves](#what-ci-proves).

---

## Prerequisites

Two things, and nothing else installed globally:

| Tool | Why | Install |
| --- | --- | --- |
| [mise](https://mise.jdx.dev/getting-started.html) | Installs the pinned Python, Node, uv, and Helm versions. No system Python or Node needed. | `curl https://mise.run \| sh` |
| [Docker](https://docs.docker.com/get-started/get-docker/) | Runs PostgreSQL, and optionally the whole stack. | Platform installer |

If you intend to run a coding agent in this repository — Claude Code, Codex,
Gemini or another — there are three more, and they are listed separately
because they are **not** what running the application needs:

| Tool | Why an agent session needs it |
| --- | --- |
| `git` | Every guardrail in `scripts/` reads the repository through it. |
| `python3` | The commit checker and [the landing gate](#when-a-branch-is-ready-to-merge) both read through it. |
| [`gh`](https://cli.github.com/), authenticated | The landing gate asks GitHub what a pull request's head is and what has been posted to it. `gh auth login` once. |

**A shim is not enough for these three, and that is the whole reason they are
called out.** Hooks and git hooks inherit whatever `PATH` the invoking process
had, which is not always an activated shell: a GUI git client, a launcher, or a
terminal where mise was never activated are all real cases. `mise.toml` pins a
Python for the project rather than installing one globally, so `python3` can be
absent exactly there. The checkers refuse rather than pass when one of these is
missing — that is deliberate, and
[the landing gate](#when-a-branch-is-ready-to-merge) explains why — so the
symptom is a command that will not run and a message naming the program, not a
guard that quietly stopped guarding.

```bash
git --version && python3 --version && gh auth status
```

[Podman](https://podman.io/) works too — substitute `podman compose` for
`docker compose` in the commands below. The images are fully qualified
(`docker.io/library/...`) so Podman resolves them without extra configuration.

Every version this project uses is pinned in [`mise.toml`](mise.toml). That file
is the single source of truth for the toolchain — CI installs from it too.

### Activate mise, then open a new shell

**Do this before typing anything else.** The installer above only puts a binary
in `~/.local/bin`; it changes no shell of yours. Its last lines print the one
line to add to your shell's configuration, of the form:

```bash
eval "$(~/.local/bin/mise activate bash)"      # or zsh, or fish
```

Add the line it printed — to `~/.bashrc`, `~/.zshrc`, or
`~/.config/fish/config.fish` — and then **open a new shell**, because a running
one will not pick it up. Everything after this point in this guide, and in the
two documents it links to, assumes an activated shell.

Until it is activated, `mise` may not be found at all, and `uv`, `python`,
`node`, `npm` and `helm` certainly are not: this project installs none of them
globally, so a shell that cannot see mise's shims answers `command not found`
to every command below. The new shell should answer both of these:

```bash
mise --version
mise doctor       # "activated: yes" is the line that matters
```

Three of the names mise brings recur throughout, so they are worth having
straight now: **uv** manages the backend's Python environment, and `uv run
<command>` is how every backend command is run inside it; **ty** is the Python
type checker; **helm** renders the deployment chart.

### Clone and bootstrap

```bash
git clone https://github.com/lotia/nycmesh-inventory-tng.git
cd nycmesh-inventory-tng
scripts/bootstrap-dev.sh
```

That URL is public and read-only, and needs no GitHub account and no SSH key.
Where a remote you can push to matters is [Pull requests](#pull-requests).

`mise run setup` is the same script under the name mise lists it by, so
`mise tasks` in a fresh clone answers "what am I supposed to run?" without your
having found this page first.

[`scripts/bootstrap-dev.sh`](scripts/bootstrap-dev.sh) is setup: it trusts and
installs the toolchain, writes `.env` from [`.env.sample`](.env.sample) if you
have none, points git at the hooks that check a commit as you make one, starts
PostgreSQL, applies the migrations, and puts an invented catalogue in the
database so that no screen you open is blank. It composes the
commands the rest of this guide describes and invents nothing of its own, so
nothing here is out of reach if you would rather type them. Run it as often as
you like; it writes no file it has written already, and it will not touch a
`.env` you have edited.

If it stops on a port that is already taken, or on anything else,
[Troubleshooting](#troubleshooting) is two sections down and names the fix for
each of them.

It stops short of two things, and says both as it finishes rather than leaving
you to find them here. `createsuperuser` asks for a password at a terminal, so
it cannot run unattended. And the first sign-in of the account it makes needs
[a second factor](#signing-in), which means having an authenticator app to
hand.

The last thing it prints is what to do with it: the two label codes the seed
made, and the order to start the servers in. Read the end of its output rather
than scrolling past it — those codes are the stickers a scanner resolves, and
nothing else prints them.

`.env` holds your local configuration. It is git-ignored, and
[`.env.sample`](.env.sample) documents every variable. Setting the toolchain
and that file up by hand, if you would rather, is three commands:

```bash
mise trust      # allow mise to use this repo's mise.toml
mise install    # installs Python, Node, uv, Helm at the pinned versions
cp .env.sample .env
```

`mise trust` is a one-off confirmation that you meant to run the versions this
repository asks for: mise refuses to read a `mise.toml` it has not been told
about, so that a checkout cannot pick your toolchain for you unnoticed.

---

## Running it

Three ways, and the bootstrap script above prepares the second of them. Use
Docker when you want the whole system up; use the native setup when you are
actively editing code and want fast reloads; use the devcontainer when you want
nothing at all on your host.

**Run one at a time.** A and B both put Django on port 8000 — one in a
container, one on your machine — so whichever starts second fails to bind.
`docker compose down` before starting the native servers, or stop those before
bringing the stack up. PostgreSQL is not a clash: both use the same compose
service, and starting it twice starts it once.

### Option A — everything in Docker

Best for a first run, or when you only care about one half of the stack. It is
the quickstart, so the commands live in
[README](README.md#quickstart) rather than a second time here. Frontend on
<http://localhost:8080>, API on <http://localhost:8000>, and migrations run
automatically on start.

Two more worth knowing once it is up:

```bash
docker compose logs -f backend | scripts/pretty-logs           # tail logs
docker compose down -v                                         # stop, wipe database
```

Every service in [`compose.yaml`](compose.yaml) names the non-root uid it runs
as, drops all capabilities, refuses to gain privileges, and runs with a
read-only root filesystem — with a `tmpfs` for each directory that service
genuinely writes to, and no others. Those are stated in the file rather than
claimed in a comment, and none of them needs anything added under rootless
Podman.

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

### Signing in

Everybody signs in, for now. These answer without a session — the index
`/api`, the two probes `/api/healthz` and `/api/livez`, `/api/me`, the two
credential-free reports `/api/client-failures` and `/api/debug-trace`, and the
API's own description at `/api/schema` and `/api/docs` — and every other one
needs one, the two a volunteer writes to included. The list is deliberately
not a count: it was written as one and was wrong twice running. It is also a
convenience rather than the authority — that is an audit in
`backend/src/inventory/tests/test_capabilities.py`, which keeps the same set
with the argument for each entry beside it, and whether an endpoint may join
them at all is
[decision 0012](docs/decisions/0012-two-populations.md). So make an
account before you expect either half of the app to answer, and sign in at
`/accounts/login/` on whichever address you are using — that page and the rest
of `/accounts` answer anybody, because a sign-in form has to. `/api/schema`
and `/api/docs` are
readable by anyone who can reach the port, which is why a deployment puts a
network boundary in front of
them: [which paths are restricted](docs/deployment.md#which-paths-are-restricted).

That is a gap rather than the design.
[Decision 0012](docs/decisions/0012-two-populations.md) settles that the two
populations are told apart by what they may do and not by a credential, and
[what is not built yet](docs/architecture.md#not-yet-built) is where the
distance between that and today is recorded.

The first sign-in of a new account stops and asks it to set up an authenticator
app before it can reach anything, because
[decision 0013](docs/decisions/0013-administrator-sign-in.md) requires a second
factor of a local password. There is no way past that and no setting that turns
it off, so have a phone or any other TOTP application ready before you start.
Which providers a deployment offers besides that one is configuration; the
variables are in [deployment](docs/deployment.md#environment-variables).

### If you had this stack running before August 2026

Run `docker compose down -v` once. The database volume now mounts
`/var/lib/postgresql` rather than `/var/lib/postgresql/data`, because postgres
18 keeps its data in a major-version subdirectory. An older volume is not
migrated: postgres would silently start an empty cluster beside it, and you
would wonder where your local data went.

---

## Troubleshooting

**`mise: command not found`, or `uv`/`npm`/`helm: command not found`.** You have
installed mise but not activated it, which is by far the commonest way a first
run stops. [Activate mise, then open a new shell](#activate-mise-then-open-a-new-shell)
is the fix, and it is one line plus a new terminal.

**`django.core.exceptions.ImproperlyConfigured: Set the DJANGO_SECRET_KEY
environment variable`.** You have no `.env`. Run `cp .env.sample .env`. The
settings module deliberately has no fallback secret, so a misconfigured
deployment fails at boot instead of running with a known-public key.

**`connection refused` on the database port.** PostgreSQL is not running.
Start it with `docker compose up -d postgres`.

**Frontend loads but every API call 404s.** You are on the Vite dev server
(port 5173) with no backend running. Start Django, or use
<http://localhost:8080> from the Docker stack.

**`Bind for 0.0.0.0:5432 failed: port is already allocated`**, or
`address already in use`. A PostgreSQL of somebody else's — a system service, or
another project's stack — already holds the port this one wants to publish, and
that is the ordinary state of a laptop that has done any database work before.
Either stop theirs, or move ours, which takes **two** settings changed
together:

```bash
POSTGRES_PORT=5433
DATABASE_URL=postgres://inventory:inventory@localhost:5433/inventory_tng
```

Put both in `.env`. `POSTGRES_PORT` is what
[`compose.yaml`](compose.yaml) publishes the container on and `DATABASE_URL` is
where Django looks, so changing one alone points your Django at the stranger's
cluster — it will connect, fail to authenticate or migrate a database that is
not yours, and none of the errors will say why.
[`.env.sample`](.env.sample) carries the same pairing.

**Port 8000, 8080 or 5173 already in use.** Two of the three ways to
[run it](#running-it) are up at once; bring one down. Failing that, something
else on the machine holds the port and has to be stopped — those three are not
configurable here, because both dev servers and the guides' addresses assume
them.

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
  capture/              The screenshot run behind the guides, and its scene
  Dockerfile            Frontend image (nginx serving static files)
  nginx.conf.template   Runtime API proxy configuration
infra/helm/             Kubernetes deployment chart
scripts/                The development bootstrap, and the guardrail checkers
guides/                 The two user guides, and the pictures in them
docs/                   Architecture, deployment, and decision records
.agents/skills/         On-demand context for AI coding agents
compose.yaml            Local development stack
mise.toml               Pinned toolchain versions, and the setup task
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
| Put demo rows in an empty database | `uv run python src/manage.py seed_demo_data` (refuses unless `DJANGO_DEBUG` is on) |
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
via `django-environ`, and one other variable bounds how long a connect to it
may take — [`.env.sample`](.env.sample) says why that bound exists and
[deployment](docs/deployment.md#health-checks) says what it must stay under.
Nothing else configures the database.

When you change a model:

```bash
uv run python src/manage.py makemigrations
uv run python src/manage.py migrate
```

Commit the generated migration file alongside the model change. In production,
migrations run as a separate Kubernetes Job before new pods start, never from a
running web pod — see [docs/deployment.md](docs/deployment.md).

### Importing the old spreadsheet

No part of setting up, and deliberately not in the path above: it needs an
exported workbook, which is not in this repository and is not ours to publish.
What `manage.py import_sheet` does, the four steps it composes, and when you
would run one of them on its own are in
[the data model](docs/data-model.md#migrating-the-existing-sheet).

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

## Reading the logs while you work

The backend writes one kind of record and draws it two ways: in columns for a
person, or as JSON for something that parses. Same fields, same names, same
values — only the drawing differs, which is what makes debugging against your
own terminal worth anything. [Decision 0021](docs/decisions/0021-telemetry-over-otlp.md)
is why it is arranged that way; this is how to use it.

### Running natively

Nothing to do. `runserver` draws columns, because a checkout has no collector
and you are the only reader:

```bash
cd backend && uv run python src/manage.py runserver
```

### Running under compose

That stack writes JSON, deliberately — it is meant to look like a deployment
rather than like a friendlier version of one. Pipe it through the reader:

```bash
docker compose logs -f backend | scripts/pretty-logs
```

Any JSON stream works, from anywhere, including one saved to a file weeks ago.
The reader is the same code that would have drawn the columns in the first
place, so nothing is approximated.

It is the one thing here that needs `uv` even if you are running everything in
Docker — it is a Python program in this repository, not something in the image.
[Prerequisites](#prerequisites) installs it.

### Sending it somewhere instead of reading it

A terminal is one reader; a collector is the other, and this repository ships
one for development behind a compose profile — one command to start, and
somewhere for traces and metrics to go as well as logs.
[docs/observability.md](docs/observability.md#somewhere-to-send-it-on-a-laptop)
is that command and what to open.

### Turning one subsystem up

Setting everything to `DEBUG` is almost never what you want: Django logs every
SQL statement at that level, so the line you were reading becomes one in a
thousand. Name the logger instead.

```bash
# every query the importer makes, and nothing else raised
DJANGO_LOG_LEVELS=inventory.sheet=DEBUG,django.db.backends=DEBUG \
  uv run python src/manage.py runserver
```

The SQL half of that only works with `DJANGO_DEBUG` on, which is the case here
and never in a deployment: Django decides whether to record a query from that
setting rather than from the logger's level, so no level at all makes its SQL
logger speak with `DEBUG` off. Nothing tells you that at the time, which is why
it is here.

Those statements arrive with their parameters already interpolated, so they are
one of the few things a local log holds that a collector's never does —
[observability.md](docs/observability.md#what-telemetry-may-carry) is why.

Both halves are comma-separated `logger=LEVEL` pairs laid over `DJANGO_LOG_LEVEL`.
A level Python does not know stops the process rather than becoming `INFO`
quietly — which is the general rule here, and the reason you will never be
looking at output you did not ask for.

`inventory=DEBUG` is also the switch for the level below this: every function
a request called, recorded as spans rather than as lines.
[observability.md](docs/observability.md#every-function-a-request-called) is
which modules that covers, which two it deliberately leaves out, and what it
costs when nobody has asked.

### Which columns you get

Measured from your terminal once, at startup, and said out loud whenever the
answer costs you something — one line naming what it found, what it chose and
what that choice leaves out. `full` drops nothing, so it says nothing; a JSON
stream has no layout, so it says nothing either. Anything else announces
itself, and you should never be comparing consoles with somebody else and
wondering.

| Layout | Needs | Timestamp | Logger | Drops |
| --- | --- | --- | --- | --- |
| `full` | 140 columns | `2026-08-23T14:32:07.412-04:00` | 34 columns, `inventory.sheet.batches` | nothing |
| `compact` | 100 columns | `14:32:07.412` | 12 columns, `batches` | the date, the offset, the module path |
| `minimal` | anything less | `14:32:07.412` | — | the above, and the logger column |

A logger name longer than its column is cut from the *left*, so `…batches` — the
tail is the half worth keeping. 34 columns fits every logger this project and
Django actually use, including `django.security.DisallowedHost`.

`DJANGO_LOG_LAYOUT=full` overrules the measurement, in either direction: it is
how to keep the whole timestamp on a narrow window, and how to buy back the
seventeen columns it costs on a wide one. It works on the reader too, which is
the useful case — the process piping into it had no terminal to measure.

A key bound for the life of a request, rather than passed on one call, is on
every line — so the columns leave it out and `DJANGO_LOG_CONTEXT=shown` puts it
back, which is what you want when following one request rather than reading a
sequence of them. Only what the writer *inherited* is hidden: a key you passed
yourself always appears, even one named `status` or `path`. What that covers
today is `trace_id` and `span_id` — empty until a collector is configured, and
the way to find a log line's trace once one is. Nothing binds a request id yet;
that is `inventory-tng-nb8.9`.

Colour appears only when the output is a terminal, and never when `NO_COLOR` is
set, so piping to `grep` or a file gives text you can read.

Every one of these is in [`.env.sample`](.env.sample) with its default. Where a
*deployment* sends all this instead is
[docs/deployment.md](docs/deployment.md#reading-the-logs).

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

One of them decodes a symbol through a browser, which no other test can. What
nothing could reach until now is the handoff between a camera frame and the
decoder, where a scanner can decode nothing while every other suite stays
green. `integration/decodes.spec.ts` closes that; its header says what the
other suites already settle, and what it found when it first ran. The clip it
films is generated during the run rather than committed, and needs no ffmpeg
and no container; how, and why, is in `frontend/integration/qrVideo.ts`.

The offline queue is here on a variant of the first argument.
`integration/offline-batch.spec.ts` throws away a batch's *answer* instead of
its request, so the browser retries something the ledger already holds — and
whether that writes a second row is a question only a real ledger can be asked.
Both sides of it are covered without a browser and neither can see the join.

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

### The guides' screenshots

```bash
cd frontend && npm run capture:guides
```

Every picture in [guides/volunteer.md](guides/volunteer.md) and
[guides/administrator.md](guides/administrator.md) comes from that command
rather than from somebody's phone, which is what makes them regenerable. It
drives the same servers and the same seeded scene as the suite above — its
config spreads `playwright.config.ts` rather than restating it — and writes one
PNG per step into `guides/images/`.

Kept out of `npm run test:integration` on purpose. A run of it rewrites every
PNG under `guides/images/`, and those are then committed — a suite that edits
the working tree is not a suite. CI does not run it either.

What it adds to that scene — the stickers to scan, stock on a shelf, something
measured whose scan asks how much, and the questions the sheet import leaves
behind — is in `frontend/capture/scene.ts`, and every code and quantity in it
is fixed, so a run against an unchanged app rewrites almost nothing. Two
pictures do change every time and cannot not: one is of the movements, which a
run appends to and nothing may edit, and one carries the date it was printed.
Which pictures exist at all is `frontend/capture/shots.ts`, and `npm test`
fails when one of them is missing from `guides/images/`, when the guide that
claims it does not draw it, and when `guides/images/` holds a PNG no shot
claims.

`capture/` is measured by the coverage thresholds like anything else. The three
files in it that only a browser can reach — the driver, the gestures against a
live `Page`, and the scene, which shells out to `manage.py` — are excluded by
name in `vite.config.ts`, with the reason beside them.

Run it when you change a screen one of them shows, and commit the PNGs with
that change.

### What CI proves

Some of what this repository's documents claim is executed on every push, and
the rest is not. Which is which is worth knowing before you rely on either.

**This guide's setup is run, not read.** The `Setup instructions` job starts a
clean runner, installs mise off the line in [Prerequisites](#prerequisites),
and activates it with the line
[Activate mise, then open a new shell](#activate-mise-then-open-a-new-shell)
tells you to add — nothing else, and no shim directory bolted onto the path
behind your back. From there it types what is printed here and only that:
`scripts/bootstrap-dev.sh`, then a bare `uv` and a bare `npm`, with no prefix
a reader of this file would not have. A job reaching for
`mise exec --` instead would be a green tick over a command this guide does
not print, which is worse than no job at all.

Then it asks whether any of it worked: the seed has to have left rows behind
in the catalogue, the labels, the places, the people and the ledger, and the
two servers — started with the two lines the bootstrap script signs off with —
have to answer a request. That is the sentence at the top of this file being
kept rather than repeated.

**The quickstart is run too.** The `Compose stack` job is
[README](README.md#quickstart) followed by somebody who has only Docker: it
copies `.env.sample`, brings the three services up, asks each of them for a
page, and then types the two commands that page calls not optional. Nothing
else finds out whether the hardening every service declares lets the stack
serve anything, or whether the seed's own refusal is satisfied by the file the
quickstart tells you to copy.

**Deployment is rendered, and what it renders is put to the application.** No
cluster exists in CI, so the `Helm chart` job runs the `helm lint` and
`helm template` commands
[deployment](docs/deployment.md#from-an-empty-cluster-to-a-first-sign-in)
prints — including the administrative ingress, which the default render does
not draw. Rendering is not the whole of it:
`backend/src/inventory/tests/test_chart.py` renders the chart in the `Backend`
job, takes the request a probe would make and the environment the same
manifest supplies, and asks Django what it answers. A manifest that is valid
YAML and describes a pod this application would refuse is the failure that
suite exists for, and it is one `helm lint` cannot see. Everything from the
install onwards is still unproven, and that document says so where it asks you
to type it.

**A command any document names has to exist.**
`backend/src/inventory/tests/test_documented_commands.py` holds every
`manage.py` subcommand, `npm run` script, file under `scripts/` and chart value
the documents mention against what the repository actually has, and names the
file and the line of anything stale. It costs nothing and it catches the
commonest rot there is, which is a rename.

**And a command this repository has must be named somewhere.** The same file
asks it the other way about, which is the direction that actually rots: a
`manage.py` subcommand or an `npm run` script *added* and never written up
keeps every other check green. A couple of them genuinely want no write-up, and
`backend/src/inventory/tests/undocumented.allow` is where saying so goes — its
header says how an entry is written, and an entry that stops being needed is
reported rather than left lying.

**And CI activates mise with the line printed above.** The `Setup instructions`
job's whole claim is that it types what this guide prints, which rests on the
one line in
[Activate mise, then open a new shell](#activate-mise-then-open-a-new-shell).
That line is retyped in the workflow rather than shared with anything, so the
same file compares the two and fails if they have drifted apart.

**A control either guide names has to be on the screen.** Each guide keeps one
typographic promise to its reader: a thing you press or type into is set in
bold, and the screen's own words back to you are in italics. That promise is
also what makes the guides machine-readable, so nothing lists the controls
twice — `frontend/capture/controls.ts` reads the short bold phrases out of the
guide itself, and `frontend/integration/guide-controls.spec.ts` walks the scene
the pictures are taken from and fails naming whatever the app no longer offers.

Each guide is held to the screens it is about, and not to the union of both:
the volunteer's to the app, the administrator's to the app and to `/admin/`,
because its first section says it is about the two of them. Pooling them would
let a field on a Django page answer for a button a volunteer is told to press.
The walk has to work for its names — a menu's choices exist only while the menu
is open, and the box asking who you are is gone the moment you answer it — so
it opens what it must and harvests before it moves on.

Comparing regenerated PNGs would catch more and would also fail on a font or a
shadow; this fails on the change that would actually mislead somebody.

What none of it can see is whether a guide has gone on describing a job nobody
does any more, or stayed quiet about one that has appeared. Somebody has to
read them, which is why that is in the [Definition of Done](#definition-of-done)
instead.

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
| `manage.py`, `wsgi.py`, `asgi.py`, `gunicorn.conf.py` | `backend/pyproject.toml` → `[tool.coverage.run] omit` | Entry points run by Django or the server, never by tests. `gunicorn.conf.py` computes nothing of its own — what it calls is covered |
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
| Using the app to move stock | [guides/volunteer.md](guides/volunteer.md) |
| Running the catalogue, the people and the labels | [guides/administrator.md](guides/administrator.md) |
| How the guides' pictures are made | [The guides' screenshots](#the-guides-screenshots) |
| Which documents CI executes, and how far | [What CI proves](#what-ci-proves) |
| Development setup and workflow | This file |
| Code style and linting | [Code style](#code-style) |
| API schema and how it stays current | [The API schema](#the-api-schema) |
| Typing requirements | [Typing](#typing) |
| Testing and coverage requirements | [Testing and coverage](#testing-and-coverage) |
| Reading logs while developing | [Reading the logs while you work](#reading-the-logs-while-you-work) |
| What one commit contains, and its message | [Commits](#commits) |
| How work is reviewed and reaches `main` | [Pull requests](#pull-requests) |
| How to contribute | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Architecture and technology choices | [docs/architecture.md](docs/architecture.md) |
| Inventory data model | [docs/data-model.md](docs/data-model.md) |
| Deployment | [docs/deployment.md](docs/deployment.md) |
| Telemetry: where it can be sent, and what it may carry | [docs/observability.md](docs/observability.md) |
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

It reads every Markdown file **and the comments of everything else** — scripts,
workflows, the chart's templates, and the application's own docstrings and
comments. Those are documentation of how this repository works, and a docstring
is the easiest place of all to re-derive a decision record.

No directory is excluded, so a file added or moved under one already read is
read by default. What decides whether a file is read at all is its extension —
`.md`, `.sh`, `.py`, `.yml`, `.yaml`, `.ts`, `.tsx` — which leaves the
templates that carry no extension of their own, `nginx.conf.template` and the
Dockerfiles, unread. Widening that is a matter of naming them. What is left
out *within* a file is not prose: fenced blocks and
tables in Markdown, and in code, anything a file *uses* rather than *says* — a
string handed to `RunSQL` is a value however much of it reads like a sentence.
Addressing is left out too, wherever it appears: a Markdown link, a bare path
or URL in a comment, and a bare "decision 0016" all name a thing rather than
explain it, and two files naming the same thing are obeying the rule.

The judgement this leaves is real and is per passage. A docstring beside the
invariant it enforces is the code explaining itself, which is a different thing
from a topic having two homes; when it is the first, the fix is usually still
to state the rule here and cite the record rather than reproduce its argument.

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
- [ ] **Code that changes something says so.** Run
      `scripts/check-telemetry.sh`: its header states what it reads and what it
      leaves alone, and `scripts/check-telemetry.allow` is where a module that
      is right to stay quiet is argued. What is worth recording, and what may
      never be recorded at all, is
      [docs/observability.md](docs/observability.md)
- [ ] **Documentation is consistent with the change.** If the change alters
      setup steps, commands, environment variables, architecture, the API
      surface, or the deployment procedure, the canonical document for that
      topic is updated in the same pull request.
- [ ] **The two guides still describe this app.** Weigh the change against
      [guides/volunteer.md](guides/volunteer.md) and
      [guides/administrator.md](guides/administrator.md): neither may name a
      role the app has dropped, nor omit one of its flows. This is the part no
      checker sees — [What CI proves](#what-ci-proves) is the part that is seen
- [ ] A decision that future readers would ask "why?" about has a record in
      [docs/decisions/](docs/decisions/)
- [ ] **No cryptography was written.** An established library, or a thin
      wrapper over one's public API, or the work stopped and asked — see
      [rule 3 in AGENTS.md](AGENTS.md#three-rules-that-are-not-negotiable)

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
  work — but only one issue may be *closed* by it. An epic does not count: it
  groups a batch and does no work of its own, so it finishes when its children
  do and its closure rides with the last of them.

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
above. A guardrail rather than a gate.

**Amending is recognised, within one limit.** The flag above is for running it
by hand; as a hook it is told nothing about how git was invoked, so it works the
shape out instead — `HEAD` already closed the issue the message names, *and* the
summary line is still `HEAD`'s. Both, because either alone would also describe a
fresh commit claiming a closure that the one before it made. So amending to
revise a body, or to fold in work you forgot to stage, passes; amending to
rewrite the summary is refused, and so is a reword during a rebase.

That pair narrows the shape; it does not pin it down. A brand new commit whose
subject repeats `HEAD`'s word for word, closing nothing itself, is read as an
amend and accepted — and nothing given to a hook run against one message could
decide otherwise. `scripts/check-batch.sh` is what covers it, by reading the
whole range instead: an issue closed twice is an objection there, and a branch
carrying one does not merge.

To change a summary, reach for `git commit --fixup=reword:<commit>`, which puts
the new one in an `amend!` for `git rebase --autosquash` to fold in. The
`reword:` is the part that matters — a plain `--fixup` throws its own message
away and the old summary survives the fold.

It also runs on every commit you make, without your arranging anything:
`.beads/hooks/commit-msg` is a link to it that arrives with the clone, and
[bootstrap](#clone-and-bootstrap) points git at the directory holding it. That
directory is beads' own, and it holds five hooks of beads' making — a second
one is not an option, because `core.hooksPath` is one path and beads' git
integration goes quiet the moment it names anywhere else.

Two consequences of that pointer being one path, both worth knowing before you
are surprised by them. Beads' own five start running too, so a commit, a
checkout and a pull each cost a fraction of a second more than they did. And
anything you keep in git's default `.git/hooks` stops running — `pre-commit`,
husky, a hook of your own — so move what you want kept into `.beads/hooks`;
bootstrap says so when it finds any, rather than switching them off quietly.

Two ways it can be absent, both of which say so rather than passing silently.
A clone that never ran bootstrap has the hook and no `core.hooksPath`, and
`scripts/check-setup.sh` tells you which of the two is missing. A checkout that
lost the link fails CI, where the same script runs as `--shipped-only` — its
header says why the halves are split and which one a runner can be asked.

It asks a third thing, because a hook that runs and an interpreter it can reach
are not the same question: the checker reads the tracker through `python3`, and
a hook inherits whatever `PATH` invoked it rather than an activated shell's. So
a perfectly wired clone can still refuse every commit that stages the tracker,
and the refusal, like the others, names the program rather than blaming the
commit.

There is one way past all of it, `git commit --no-verify`, and the rule about
using it is in [AGENTS.md](AGENTS.md#git).

History before this section predates it, and is not the example to follow:
several commits close five issues each.

---

## Pull requests

Nothing reaches `main` except through a pull request, and `main` is protected so
that there is no other way in.

This is the first point at which the anonymous clone in
[Prerequisites](#prerequisites) is not enough: pushing needs a credential. Add
an SSH key to your GitHub account and point the remote at it —

```bash
git remote set-url origin git@github.com:lotia/nycmesh-inventory-tng.git
```

— or keep the HTTPS remote and let `gh auth login` install a credential helper
for it. Contributors without write access push to a fork instead;
[CONTRIBUTING.md](CONTRIBUTING.md) is that path.

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

Mark the pull request ready when the batch is complete and CI is green. What
the batch holds is posted there as a comment, read off the commits rather than
typed, so the list is the one that was checked.

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

**Each findings comment carries a marker**, on a line of its own anywhere in the
body:

```
<!-- review-cycle: code-review -->
<!-- review-cycle: simplify -->
```

They are what [the landing gate](#when-a-branch-is-ready-to-merge) reads as
evidence that a pass happened, and they are the reason it can record what it
found rather than what it was told. A review submitted through GitHub's review
API — which is what `/code-review --comment` leaves behind — already counts for
the first, so in practice the marker only has to be typed on the simplify
comment. It is the same device as the `<!-- batch-contents -->` marker CI posts,
for the same reason: a marker survives rewording and prose does not.

### Merging

Squash merge and merge commits are disabled on this repository, so the merge
button cannot collapse a batch into a single commit. **Rebase merge** replays
each commit onto `main` individually. Why it is arranged that way rather than
left to discipline is
[0017](docs/decisions/0017-review-through-pull-requests.md).

That setting, and what `main` accepts, are GitHub's rather than the
repository's, so they are written down as `scripts/repo-settings.sh` rather than
left as something somebody once clicked. `--check` reports what has drifted;
running it without puts it back, which is what to do after adding or renaming a
job in CI that ought to be required. A weekly job runs `--check` and reports,
so drift is found rather than remembered — and it runs on any pull request that
touches CI's job names, because those decide what `main` requires.

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

### When a branch is ready to merge

Four of these `main` enforces itself, and there is no way to merge without
them:

- every required check green **on the head being merged**
- the branch not behind `main`
- every review conversation resolved
- a linear history, which is why the merge is `--rebase`

Two it cannot see, and they are the ones a person has to hold to:

- `scripts/check-batch.sh origin/main..HEAD` is clean, so every commit belongs
  to exactly one issue — note it accepts a `Refs:`-only commit, so "belongs to"
  is not the same as "closes"
- the review pass in [Pull requests](#pull-requests) has actually happened, and
  its findings have been triaged and answered

Nobody is asked to weigh those against anything. A branch that does not meet
them is one to finish, and whoever finished it merges it — an agent working a
batch does not stop to ask, for the same reason a contributor with write access
does not.

`scripts/landing-gate.sh` is what holds a Claude Code session to that second
pair. It ships with the repository and is registered in the tracked
`.claude/settings.json`, so a fresh clone, a fork and every worktree get it. It
refuses `gh pr merge` until the cycle has been recorded against the exact head
being merged, refuses `gh pr ready` while the checks are not green, refuses a
push that would land on `main`, and refuses a bare `git push --force` or `-f` —
`--force-with-lease` is free, and [AGENTS.md](AGENTS.md#git) says why the two
are on opposite sides of that line.

"Would land on `main`" is asked of git rather than read off the command line,
so a bare `git push` on a checked-out `main` and `git push origin HEAD` are
both refused, and a branch called `batch/main-fix` is not.

`record` stores what it found on the pull request — the review submissions and
the marker comments from [One review pass](#one-review-pass-findings-filed-per-issue),
by id, author and timestamp — and refuses to write a receipt for a stage with
nothing behind it:

```bash
scripts/landing-gate.sh record <pr>     # as its own command; see below
scripts/landing-gate.sh status          # what is recorded, and on what evidence
scripts/landing-gate.sh clear [<pr>]
```

Two things about it are worth knowing before you meet them.

**It fails closed, on purpose.** A missing `python3` or `gh`, or a `gh` that is
unauthenticated or rate-limited, makes it *refuse* the command and name the
program it could not find — see
[Prerequisites](#prerequisites) for the three an agent session needs. It used to
fail open in exactly those three ways, which is worse than having no gate at
all: the rule above goes on being believed while nothing is checking it, and
nothing announces that the guard has stopped working. Only the commands it
guards are affected; everything else runs as normal.

**With one exception, and it is the gate's own source.** If a rebase stops on a
conflict *in `scripts/landing-gate.sh` itself*, the half-written file cannot
judge anything, and every command it guards is then refused over something that
has nothing to do with the command. The gate stands down there, saying so on
stderr, and it takes three things at once: the matcher has already failed, git
reports an operation actually in flight, and the file carries conflict markers.
Any two of the three leave it guarding as usual. This is the one place it fails
open, and it is deliberate.

Deliberate, and worth less than it first looks — which is worth knowing before
leaning on it. Whether the guarded verbs appear at all is settled before the
matcher is consulted, so anything without `gh` or `push` in it never needed
this file to be readable: continuing or abandoning the rebase was never
prevented. What the stand-down releases is the guarded pair themselves, the
force-with-lease that ends a collapse and a merge that nothing is checking. The
break that really does take a session down is markers in the shell half, where
bash exits with the status the harness reads as *blocked* before any of this
runs — out of reach from inside the file, and `inventory-tng-ghqk` records both
the measurement and what it would take to cover.

It cannot cover every shape of that. Markers in the *shell* half mean bash never
parses the file, so nothing in it runs and nothing in it can help; the symptom
is a hook error rather than a refusal, and the answer is to resolve the markers
with an editor. `inventory-tng-ghqk` records why a wrapper was weighed and not
taken.

A `gh` that is merely *slow* is the same case, and it needs its own deadline
rather than the hook's: a hook killed for exceeding its timeout prints no
verdict, and no verdict is read as permission. So the gate gives `gh` a shorter
deadline than the timeout registered in `.claude/settings.json` and refuses in
time to say so. Set `GH_DEADLINE` if a slow network makes it refuse honest
commands — but raise the registered timeout with it, or the harness kills the
gate before it can speak. The one place the same reasoning points the other way
is `clear <pr>`: rather than treat a receipts file it cannot parse as empty and
rewrite it, it refuses and leaves the file alone. `clear` with no argument is
the way out, and it removes the file whole.

**`record` must be its own command.** Piping it — `record 12 | tail -2 && gh pr
merge 12` — takes the pipeline's exit status, so the record does not take
effect and the merge is then refused with "No review cycle has been recorded",
which reads like the record failed rather than like the shell ate it.

What it is not is a security boundary. It reads a command line, and a command
line has more spellings than any reader has patterns; the enforcement that
matters is the four rules `main` holds itself. It is a guardrail against
forgetting, and [0020](docs/decisions/0020-who-merges.md) is the decision about
what may rest on one.
