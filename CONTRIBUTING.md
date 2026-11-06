# Contributing

## Version control and issues

Issues are tracked with [beads](https://github.com/gastownhall/beads) (`bd`).
For version control, use git or [jj](https://jj-vcs.github.io/jj/), whichever
you prefer. Coding agents always use jj.

### Setup, once per clone

```sh
git clone <url>           # or: jj git clone <url>
bd bootstrap              # fetch the issue database; never `bd init`
```

To use jj in a git clone, run `jj git init --colocate` in it.

Issues live in a database under `.beads/`, not in commits, and travel through
the same remote on a ref of their own. `bd sync` pulls and pushes them. Run it
before you start and after you change an issue.

### Working on an issue

```sh
bd sync
bd ready                  # pick something unblocked
bd update <id> --claim
# make one commit (or jj change) for the issue,
# with a message that starts with "<id>: "
bd close <id>
bd sync
```

Then run `mise run check` on what you are about to push, every time, and
push only if it passes: there is no pull request to catch what it would
have. Push straight to `main`, where CI checks it again: `git push origin
main`, or with jj, `jj bookmark set main -r <change>` and `jj git push`.

## Requirements

- [mise](https://mise.jdx.dev/). It installs every other tool, at the
  versions in `mise.toml`, and runs the tasks below. The tasks are shell
  scripts, for macOS and Linux; on Windows, work in WSL.
- Podman or Docker, with compose, for the database (and, if you want it, the
  app). `mise run compose -- <arguments>` runs compose with Podman if it is
  installed, else Docker. To choose, set `INVENTORY_COMPOSE` in `.env`, such
  as `INVENTORY_COMPOSE=docker compose`.

Activate mise in your shell (`mise activate`, in its docs), so the tools it
installs are on your `PATH` in the clone. Then, in the clone, and again after
a pull that changes a dependency:

```sh
mise trust               # once: lets mise use this clone's mise.toml
mise run setup
```

It installs the tools in `mise.toml` (uv then installs the Python version the
backend asks for), the backend's and frontend's dependencies, and makes a
`.env` from `.env.example` if there is none. Every command below runs Python
through `uv run`.

## Local settings

`.env` holds your local settings: the database's port and URL, and any logging
or telemetry settings you want. Git ignores it, so it never reaches a commit;
change `.env.example` only to change what everyone starts from. Compose, the
run command and migrations read `.env`, and the tests read its database
settings. A value set in your shell wins over the file.

The database listens on port 5433, so it can run beside MeshDB's. If that port
is taken, change `INVENTORY_DB_PORT` in `.env`; `DATABASE_URL` follows it.

## Running the app

```sh
mise run dev             # http://127.0.0.1:8000, Ctrl-C stops it
```

It runs, in order:

1. `mise run setup`, which installs what a pull changed;
2. the database, and its migrations;
3. two servers side by side, each line of output marked with which:
   `dev-backend`, which renders every page and restarts when you save its
   Python, and `dev-vite`, Vite's dev server for the frontend (The scan
   page, below). If either fails, the other stops too.

`mise run dev-backend` or `mise run dev-vite` runs one on its own. The
backend runs with `APP_ENV=development`, whatever `.env` says.

The backend is `uv run python -m inventory_tng --reload`, in `backend/`.
`--host`, `--port` and `--workers` are there too (`--help` lists them). The
run command starts uvicorn itself; uvicorn's own logging flags are not used,
the `LOG_*` settings are (Logging, below).

### Some data to work with

```sh
cd backend
uv run python -m inventory_tng.seed     # or: --password <yours>
```

On an empty database it makes an administrator, `dev`, with a password it
prints once, and a few categories, items, places, volunteers and stock.
Sign-in codes become optional, so `dev` signs in with the password alone.
Run again, it only sets `dev`'s password, and only if you give one. It
refuses a database it did not seed, and needs `APP_ENV=development`, as
`.env.example` sets, also in the app container:
`mise run compose -- exec app python -m inventory_tng.seed`.

### Migrations

Until the first production deploy, a change may edit an existing migration
instead of adding one, so the schema's history stays short. After a pull
that does, your local database no longer matches its migrations: rebuild
it, losing its data, with `mise run compose -- down --volumes`, then
`mise run dev`. Once production runs, a migration is never edited: add a
new one.

### Locking a row

Lock rows with `db.locked` (a select) or `db.get_locked` (`Session.get`),
never `with_for_update` itself: they also read the rows again as locked,
which a plain locking read does not do for a row already loaded. `db.py`
says why; `tests/test_locking.py` fails on `with_for_update` anywhere else.

### The scan page

The scan page and the registration page are TypeScript, in `frontend/`
(docs/decisions/0010). The backend serves them at `/scan` and `/register`,
and answers 503 until they are built, unless Vite's dev server serves them.

Under `mise run dev` their scripts come from Vite's dev server, at
`VITE_DEV_SERVER` (from your shell or `.env`, else `http://localhost:5173`;
the port is required), so edits show as you save. A backend you run on its
own does the same only if `VITE_DEV_SERVER` is set. The page still comes from
the backend, so the device cookie and the API work as in production.

Without the dev server, the backend serves a build instead:

```sh
cd frontend
npm run build            # into frontend/dist, which the backend serves
```

A new build is picked up without restarting the backend when
`APP_ENV=development`. Each backend says where its pages' scripts come from
in its `frontend` log line at startup: `source=build` or `source=dev_server`.

The scan page needs a registered device, and some catalogue rows to choose
from. The camera works only over HTTPS or on `localhost`.

#### On a phone

```sh
mise run phone
```

This runs the app in its container (below) behind a Cloudflare quick
tunnel, which gives it a public `https://….trycloudflare.com` address with a
real certificate, and prints a one-use registration code for it, with a QR
code. Scan that with the phone to register it, then go on to the scan page.
Pages come from the container's build, not Vite's dev server. The address
changes each time the tunnel starts, and anyone who has it reaches your app
while it runs, so stop it when you are done.

The pages call the API through types generated from its schema. After you
change an endpoint, regenerate both, and commit `frontend/src/api/`:

```sh
cd backend
uv run python -m inventory_tng.openapi   # frontend/src/api/openapi.json
cd ../frontend
npm run api                              # frontend/src/api/schema.d.ts
```

A backend test fails while the schema is out of date, and CI fails while the
types are. `npm run api` installs the generator from `frontend/tools/openapi/`
first, which has its own TypeScript 5 (docs/decisions/0010).

### Styles

Every page shares one stylesheet, `frontend/src/styles.css`, which Tailwind
builds (docs/decisions/0014). Under `mise run dev` it comes from Vite's dev
server and reloads on save; otherwise from the build. Without a build, pages
are plain HTML. The app container builds it into its image, so a class new
to a template shows there only after `mise run compose -- up -d --build app`.
Administrator pages put their content in `<main class="admin page-width">`,
which spaces their forms. When you style a page:

- Write each class name whole. Tailwind finds them by reading the templates
  and the frontend as text, so one built from parts is never generated.
- Use the tokens in `@theme` (`bg-band`, `text-heading`, …), not colours of
  your own: the dark scheme gives each token its own value.
- Make markup that repeats a Jinja macro or a Preact component.

### Registering a device

Administrator pages need someone signed in. To make the first administrator,
set `SETUP_TOKEN` in `.env` to any 32 characters or more, restart, and open
<http://127.0.0.1:8000/setup>. With `APP_ENV=development` the cookies work
over plain http.

Pages that record anything need a registered device (docs/decisions/0009).
Make a registration code on the Devices page, signed in, and enter its
phrase at <http://127.0.0.1:8000/register>, or scan its QR code with a phone.
`uv run python -m inventory_tng.registration_codes` does the same from the
command line.

### Mail

To see the mail the app sends, start Mailpit, which catches it all and sends
nothing on: `mise run compose -- --profile mail up -d mail`. Set these in `.env`
and restart, then read the mail at <http://localhost:8025>:

```sh
MAIL_HOST=localhost
MAIL_PORT=1025
MAIL_SECURITY=none
MAIL_FROM=inventory@localhost
SITE_URL=http://127.0.0.1:8000
```

`MAIL_SECURITY=none` works only with `APP_ENV=development`. For the app
container, set `INVENTORY_APP_MAIL_HOST=mail` too.

### Running the app in a container

```sh
mise run compose -- up -d --build --wait app  # http://127.0.0.1:8000/api/docs
mise run compose -- logs -f app
```

This runs the backend the way production does, in a container beside the
database, for when you want to check that it behaves the same there. The
image builds the scan page too, and serves Brotli and gzip copies of its
files, as production will. It does not replace uv: the checks, new
migrations and your editor still use it. The container migrates the
database when it starts, then serves with `--reload`.
Your `backend/src` and `backend/migrations` are mounted into it, so it picks
up your edits. Pass `--build` again after changing dependencies or
`frontend/`.

It reads your `.env`, except `DATABASE_URL` and `OTEL_EXPORTER_OTLP_ENDPOINT`,
because inside the container `localhost` is the container, and
`VITE_DEV_SERVER` and `FRONTEND_DIST`, so it serves the bundle it built.
To send its telemetry to the dashboard, start the dashboard (Logging, below)
and set `INVENTORY_APP_OTLP_ENDPOINT=http://dashboard:18890`. It takes
port 8000, as the run command does; `INVENTORY_APP_PORT` moves it. The
client address it logs is the container network's, not your browser's.

## Running the checks

```sh
mise run compose -- up -d --wait db
cd backend

# Optional: let the tools fix what they can. Neither changes behaviour.
uv run ruff check --fix
uv run ruff format

# Then check, in this order.
uv run ty check                  # types
uv run ruff check                # lint
uv run ruff format --check       # formatting
uv run pytest --cov              # tests, against the database started above
```

For the frontend:

```sh
cd frontend
npm run fix                      # optional: Biome's fixes and formatting
npm run typecheck                # types
npm run lint                     # Biome: lint and formatting
npm run coverage                 # Vitest, with coverage (npm test: without)
```

CI runs these checks, in this order, on pushes to `main` and on pull requests,
and builds the frontend.

Only the backend's tests need the database; the other checks take about a
second each. Start the database first. Then `mise run fix` runs the fixes
and `mise run check` runs every check, or one side of them:

```sh
mise run fix-backend    # or: mise run fix-frontend
mise run check-backend  # or: mise run check-frontend
mise run check-changed-lines  # both sides, then coverage of what you changed
mise run check-scripts  # shellcheck, on the scripts in mise-tasks/
mise run check-toml     # taplo: lint and formatting (mise run fix-toml formats)
```

Each test run creates a database of its own on that server and drops it at
the end; a run that was killed before it could is dropped by a later one.
During the tests, `DATABASE_URL` points nowhere, so nothing reaches your own
data: a test that needs the database takes the `database_url` fixture. Write
tests in pytest style.
`tests/test_requests_at_once.py` and `tests/test_locking.py` hold tests of
requests at the same time, on connections that really commit, marked by
kind (the first one's docstring): `uv run pytest -m concurrency` runs only
those, and `-m "not end_to_end"` skips the ones through the whole app.

### Coverage

`mise run check` and CI measure which code the tests run, and fail when:

- **the whole falls below its floor**: 94% of the backend's lines and
  branches (`fail_under` in `backend/pyproject.toml`), and the frontend's
  floors in `frontend/vite.config.ts`. Vitest raises those as coverage
  rises: commit that change with yours.
- **any line you added or changed is not run by a test**, or a branch on it
  is taken only one way (`[tool.diff_cover]` in `backend/pyproject.toml`).
  `mise run check-changed-lines` compares with `origin/main`, so run it
  before you push; CI compares with what `main` was before the push. For a
  line not worth a test, write `# pragma: no cover` (backend) or
  `/* v8 ignore next */` (frontend) with a comment saying why.

Only `uv run pytest --cov` and `npm run coverage` measure, so a test file
run alone is never held to the floors. Their reports
(`backend/coverage.xml`, `frontend/coverage/`) are not committed. Code that
only the type checker sees (`if TYPE_CHECKING:`), the API types generated
in `frontend/src/api/`, and the frontend's page entry files are left out.

## Dependency updates

Each kind of dependency is updated with its own tool. Make each update an
issue and a commit of its own, and run `mise run setup` and `mise run check`
after it.

- **Tools in `mise.toml`**: `mise outdated --bump` lists newer releases;
  `mise upgrade --bump` moves every pin, `mise upgrade --bump <tool>` one. CI
  installs the same pins.
- **Python packages**: `uv lock --upgrade` in `backend/` moves everything in
  `uv.lock`, or `uv lock --upgrade-package <name>` one. `pyproject.toml` gives
  each dependency a floor at a minor version only, such as `fastapi>=0.162`:
  `uv.lock` pins what is installed, and is always committed. `uv add` writes
  a patch too (`>=0.162.0`); trim it, as a test checks. Two exceptions: a
  `0.0.z` package keeps its patch, there being no minor to name, and a series
  released only as betas, such as OpenTelemetry's instrumentation, floors at
  the minor's first beta (`>=0.66b0`).
- **npm packages**: `npm outdated`, then `npm update` (or `npm install
  <name>@<version>`) in `frontend/`; commit `package-lock.json`.
- **GitHub Actions** in `.github/workflows/ci.yml`: by hand, to a release at
  least two weeks old.

uv's exact version is the pin in `mise.toml`, which CI reads too.
`backend/pyproject.toml` names it twice more, both by minor version:
`required-version`, a floor, and `uv_build`, the build backend, capped below
the next minor (`uv_build>=0.13,<0.14`). A patch release of uv needs only
`mise upgrade --bump uv`. A new minor needs the `uv_build` cap moved with it,
or the build uses an older backend than the uv running it. The floor stops a
uv older than it, not a newer one installed some other way, so use the uv
mise installs. If uv stops with `Required uv version … does not match the
running version`, its advice to run `uv self update` is wrong here: change
the pin in `mise.toml` or the floor instead.

## Documentation

README.md, CONTRIBUTING.md and DEPLOYMENT.md are for people setting up,
running and working on the app. Keep them:

- **Current.** They describe what the code on `main` needs, nothing more. A
  change that alters a step, a command, a setting or a behaviour a doc
  describes updates that doc in the same change.
- **Free of history.** Why something is the way it is, what it replaced and
  what was rejected go in a decision record under `docs/decisions/`. Link to
  it when a reader needs the reason.
- **Short and clear.** Steps a reader can follow in order, in plain words.
  Cut what the reader does not need to do the task.

Change an existing doc rather than adding one, and add a new file only when
an issue asks for it.

## Logging

Use `debug` for detail you need when tracing a problem, `info` for what an
operator wants to see, `warning` for something unexpected that was handled,
and `error` for a failure, with its exception.

Log ids, which only an administrator can look up, rather than anything that
names a person:

| | In logs | In metric attributes |
| --- | --- | --- |
| Ids (volunteer, admin user, item, location, label, transaction), install numbers, admin usernames | yes | no |
| The client's address | only if `LOG_CLIENT_ADDRESS` is on (the default; turn it off in Kubernetes, where the ingress keeps it) | no |
| Volunteer names, emails, phone numbers, street addresses | no | no |
| Free text (notes, reasons, searches), request bodies, database error messages | no | no |
| Secrets (passwords, tokens, cookies, sessions, TOTP, connection strings) | never | never |

Metric attributes take values from a fixed list in code. For a database error,
log `describe_error(error)`: its class, code and constraint, never its message,
which can quote values.

As a safety net, fields are masked by name (docs/decisions/0008). Secrets are
masked always. Outside development (`APP_ENV`, which defaults to
`production`), so are personal data, fields named `sensitive_*`, and the
values database and validation errors quote in tracebacks. So name a field
for what it holds. `UNMASK_SECRETS=true`
shows secrets too, for debugging; it works only with `APP_ENV=development`,
and the app refuses to start with it anywhere else.

Logs are colourised on a terminal. `LOG_LEVEL=DEBUG` turns on debug logs,
`LOG_LEVELS` sets single loggers' levels (`sqlalchemy.engine=INFO` shows SQL,
with its parameters hidden), and `LOG_FORMAT=json` prints the JSON that
production emits. Every request is logged once, health checks included;
`LOG_HEALTH_CHECKS=false` moves successful ones to `debug`. In tests, check
log lines with structlog's `capture_logs()`.

Exporting logs and metrics over OpenTelemetry stays off unless
`OTEL_EXPORTER_OTLP_ENDPOINT` is set (or a signal's own endpoint, such as
`OTEL_EXPORTER_OTLP_LOGS_ENDPOINT`). Export is OTLP over HTTP (port 4318),
not gRPC. Of the other standard variables, `OTEL_SERVICE_NAME`,
`OTEL_RESOURCE_ATTRIBUTES` and the SDK's timing settings apply, and
`OTEL_LOGS_EXPORTER=none` or `OTEL_METRICS_EXPORTER=none` stops one signal.
To see telemetry locally, start the dashboard, run the server with
`OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318`, and open
<http://localhost:18888>:

```sh
mise run compose -- --profile telemetry up -d dashboard
```
