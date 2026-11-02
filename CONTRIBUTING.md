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

Then push a branch and open a pull request: `git push -u origin <branch>`, or
`jj git push --change @` with jj.

## Requirements

- [uv](https://docs.astral.sh/uv/). It installs the Python version the backend
  asks for and every dependency; every command below runs through `uv run`.
- [Node.js](https://nodejs.org/), at the version in `mise.toml`, for the scan
  page in `frontend/`. With mise, `mise install` fetches it.
- A container runtime with compose, such as Docker or Podman, for the database
  (and, if you want it, the app).
- Optional: [mise](https://mise.jdx.dev/), for Node and a few shortcuts.

## Local settings

```sh
cp .env.example .env
```

`.env` holds your local settings: the database's port and URL, and any logging
or telemetry settings you want. Git ignores it, so it never reaches a commit;
change `.env.example` only to change what everyone starts from. Compose, the
run command and migrations read `.env`, and the tests read its database
settings. A value set in your shell wins over the file.

The database listens on port 5433, so it can run beside MeshDB's. If that port
is taken, change `INVENTORY_DB_PORT` in `.env`; `DATABASE_URL` follows it.

## Running the app

```sh
docker compose up -d --wait db           # or: podman compose up -d --wait db
cd backend
uv run alembic upgrade head              # create or update the schema
uv run python -m inventory_tng --reload  # http://127.0.0.1:8000/api/docs
```

`--reload` restarts the server when you save a file. `--host`, `--port` and
`--workers` are there too (`--help` lists them). The run command starts uvicorn
itself; uvicorn's own logging flags are not used, the `LOG_*` settings are
(Logging, below).

### The scan page

The scan page and the registration page are TypeScript, in `frontend/`
(docs/decisions/0010). The backend serves them at `/scan` and `/register`,
and answers 503 until they are built. Build them once, or run Vite's dev
server while you work on them:

```sh
cd frontend
npm ci                   # once, and after package-lock.json changes
npm run build            # into frontend/dist, which the backend serves
npm run dev              # or: the dev server, http://localhost:5173
```

With the build, open <http://127.0.0.1:8000/scan>. A new build is picked up
without restarting the backend when `APP_ENV=development`.

To have edits show as you save, set `VITE_DEV_SERVER=http://localhost:5173`
in `.env`, restart the backend and run `npm run dev`. The page still comes
from the backend, so the device cookie and the API work as in production;
its scripts come from Vite. The port is required. Each server says where its
pages' scripts come from in its `frontend` log line at startup:
`source=build` or `source=dev_server`.

The camera and Save work only over HTTPS or on `localhost`, so a phone on
your network cannot use them yet; inventory-tng-8i4.13.2 is for that.

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

### Registering a device

Pages that record anything need a registered device (docs/decisions/0009).
Until administrator pages exist, make a code from the command line:

```sh
uv run python -m inventory_tng.registration_codes create --uses 1 --days 1
```

It prints a phrase, once. Enter it at <http://127.0.0.1:8000/register>.
With `--base-url http://<your-laptop>:8000` it also prints a link and a QR
code that open that page with the phrase filled in. With
`APP_ENV=development` the cookie works over plain http.
`revoke`, `devices` and `revoke-device` do the rest; `--help` lists them.

### Running the app in a container

```sh
docker compose up -d --build --wait app  # or: podman compose; http://127.0.0.1:8000/api/docs
docker compose logs -f app
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
docker compose up -d --wait db   # or: podman compose up -d --wait db
cd backend

# Optional: let the tools fix what they can. Neither changes behaviour.
uv run ruff check --fix
uv run ruff format

# Then check, in this order.
uv run ty check                  # types
uv run ruff check                # lint
uv run ruff format --check       # formatting
uv run pytest                    # tests, against the database started above
```

For the frontend:

```sh
cd frontend
npm run fix                      # optional: Biome's fixes and formatting
npm run typecheck                # types
npm run lint                     # Biome: lint and formatting
npm test                         # Vitest
```

CI runs these checks, in this order, on pushes to `main` and on pull requests,
and builds the frontend.

Only the backend's tests need the database; the other checks take about a
second each. With mise, `mise run fix` runs the fixes and `mise run check`
runs every check; start the database first. Each test run creates a throwaway database
and drops it at the end. Write tests in pytest style.

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
docker compose --profile telemetry up -d dashboard   # or: podman compose
```
