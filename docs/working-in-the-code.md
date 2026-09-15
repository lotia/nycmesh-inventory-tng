# Working in the code

Where things are, the commands you will type most, and what to know about the
database, the logs and the API's description of itself. Setting the machine up
is [the guide](../DEVELOPERS.md); this page starts where it stops.

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
[docs/architecture.md](architecture.md) for why.

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
| Add the pick-list a demonstration needs | `uv run python src/manage.py seed_demo_data --with-demo-roster` — 86 invented names, two of them worn by two people, and the measured share carrying no address |
| Make sure there is an administrator | `uv run python src/manage.py ensure_administrator --username=you` with `DJANGO_SUPERUSER_PASSWORD` set — creates one if absent, says so and changes nothing if not. A deployment runs it for you; see [The first administrator](deployment.md#the-first-administrator) |
| Show the label codes to scan | `uv run python src/manage.py show_label_codes` — reads the database, so it is right after any seed |
| Make the login that survives a wipe | `uv run python src/manage.py seed_integration_data --i-know-this-creates-a-published-login` — see [Signing in](local-development.md#signing-in) |
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
| Regenerate the sign-in stylesheet | `npm run theme:css` — see below |
| Add a dependency | `npm install <package>` |

`npm install` updates `package-lock.json`. **Commit it.**

**Run `npm run theme:css` after any change to `src/theme.ts`.** Django renders
the pages under `/accounts/` — sign in, the TOTP challenge, enrolment — and
they are styled from this app's theme, resolved into CSS custom properties and
written to `backend/src/inventory/static/accounts/theme.css`. That file is
generated and committed, the same arrangement `capture/` uses for the guide
screenshots and for the same reason. `npm test` goes red when it is not what
the theme now resolves to, so this is a step you are reminded of rather than
one you have to remember. Why it is generated rather than copied by hand, and
why a symlink is not available, is in
[`frontend/scripts/theme-css.ts`](../frontend/scripts/theme-css.ts).

**Which node these run on is not always the one pinned**, and the symptom when
it is not can be hundreds of failures at once with nothing in them naming a
version. Every command in the table above therefore prints one line saying so
before it does anything, and [`mise.toml`](../mise.toml) asks mise to put this
project's tools ahead of the machine's rather than behind them, which settles it
for any shell that has activated mise.

For a shell that has not, name the directory on the command:

```bash
env PATH="$(mise where node)/bin:$PATH" npm test
```

`mise exec -- npm test` looks like the answer and is not. It resolves the
command it is given, then hands the child a `PATH` with the system's copy still
in front — so npm, the script it runs, and every worker vitest spawns all look
up `node` again and find the other one.

### Deployment chart

| Task | Command |
| --- | --- |
| Lint the chart | `helm lint infra/helm/inventory-tng` |
| Preview rendered manifests | `helm template test infra/helm/inventory-tng` |

## Database and migrations

PostgreSQL 18, reached through a single `DATABASE_URL`. Django settings read it
via `django-environ`, and one other variable bounds how long a connect to it
may take — [`.env.sample`](../.env.sample) says why that bound exists and
[deployment](deployment.md#health-checks) says what it must stay under.
Nothing else configures the database.

When you change a model:

```bash
uv run python src/manage.py makemigrations
uv run python src/manage.py migrate
```

Commit the generated migration file alongside the model change. In production,
migrations run as a separate Kubernetes Job before new pods start, never from a
running web pod — see [docs/deployment.md](deployment.md).

### Importing the old spreadsheet

No part of setting up, and deliberately not in the path above: it needs an
exported workbook, which is not in this repository and is not ours to publish.
What `manage.py import_sheet` does, the four steps it composes, and when you
would run one of them on its own are in
[the data model](data-model.md#migrating-the-existing-sheet).

## Reading the logs while you work

The backend writes one kind of record and draws it two ways: in columns for a
person, or as JSON for something that parses. Same fields, same names, same
values — only the drawing differs, which is what makes debugging against your
own terminal worth anything. [Decision 0021](decisions/0021-telemetry-over-otlp.md)
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
[Prerequisites](../DEVELOPERS.md#prerequisites) installs it.

### Sending it somewhere instead of reading it

A terminal is one reader; a collector is the other, and this repository ships
one for development behind a compose profile — one command to start, and
somewhere for traces and metrics to go as well as logs.
[docs/observability.md](observability.md#somewhere-to-send-it-on-a-laptop)
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
[observability.md](observability.md#what-telemetry-may-carry) is why.

Both halves are comma-separated `logger=LEVEL` pairs laid over `DJANGO_LOG_LEVEL`.
A level Python does not know stops the process rather than becoming `INFO`
quietly — which is the general rule here, and the reason you will never be
looking at output you did not ask for.

`inventory=DEBUG` is also the switch for the level below this: every function
a request called, recorded as spans rather than as lines.
[observability.md](observability.md#every-function-a-request-called) is
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

Every one of these is in [`.env.sample`](../.env.sample) with its default. Where a
*deployment* sends all this instead is
[docs/deployment.md](deployment.md#reading-the-logs).

## The API schema

The API describes itself. `/api` lists its entry points, `/api/docs` renders
the full description for humans, and `/api/schema` serves that description as
an OpenAPI 3.1.1 document. The same document is committed at
[`backend/openapi.yaml`](../backend/openapi.yaml) so it can be read, diffed and
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
[decision 0010](decisions/0010-openapi-version.md).

