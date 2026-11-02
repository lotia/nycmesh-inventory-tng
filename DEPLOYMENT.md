# Deployment

How to run the app on a server. To run it on your own computer, see
[CONTRIBUTING.md](CONTRIBUTING.md#running-the-app).

There is no production container image or Helm chart yet
(inventory-tng-8i4.18). Until there is, the app runs from a checkout.

## What it needs

- PostgreSQL. Development and the tests use 18.
- [uv](https://docs.astral.sh/uv/), which installs the Python version the
  backend asks for.
- Node, at the version in `mise.toml`, to build the scan page. It is needed
  only to build, not to run.
- HTTPS in front of it, from a reverse proxy or ingress. Outside development
  the device cookie is `Secure`: over plain http a browser never keeps it,
  so no device stays registered.

## Settings

Set these in the environment. Only `DATABASE_URL` is required.

- `DATABASE_URL`: `postgresql://user:password@host:port/database`.
- `APP_ENV`: leave unset in production. `development` is for local work
  only: it stops masking personal data in logs, lets the cookie travel over
  http, re-reads the scan page's build on every page, and allows
  `UNMASK_SECRETS` and `VITE_DEV_SERVER`.
- `FORWARDED_ALLOW_IPS`: behind a proxy, the proxy's address, so the client
  address and scheme come from its headers.
- `LOG_FORMAT`: `json` for a log collector; the default is `console`.
- `LOG_LEVEL`, `LOG_LEVELS`: `INFO` by default.
- `LOG_CLIENT_ADDRESS`: `false` when the proxy logs client addresses itself.
- `LOG_HEALTH_CHECKS`: `false` logs successful health checks at debug.
- `OTEL_EXPORTER_OTLP_ENDPOINT`: sends logs and metrics over OTLP/HTTP; off
  when unset.
- `FRONTEND_DIST`: the folder holding the compressed scan page (below). It
  defaults to the checkout's `frontend/dist`.
- `VITE_DEV_SERVER`: never in production; the app refuses to start with it.

What each logging setting does is in
[CONTRIBUTING.md](CONTRIBUTING.md#logging).

## Installing, migrating and starting

`UV_NO_DEV=1` keeps the development tools out: every `uv` command below
would otherwise install them.

Build the scan page into a new folder for each version, for example one
named after its commit, with a Brotli and a gzip copy beside each file.
`servestatic` reports success even when compressing fails, so check that it
wrote the copies:

```sh
export UV_NO_DEV=1
cd frontend
npm ci
npm run build
cd ../backend
uv sync --locked
uv run servestatic ../frontend/dist /srv/inventory/static-<commit> --compress --no-zstd
test -n "$(find /srv/inventory/static-<commit>/assets -name '*.br' -print -quit)"
```

Then point `FRONTEND_DIST` at the new folder, migrate and restart:

```sh
uv run alembic upgrade head    # before starting a new version
uv run python -m inventory_tng --host 0.0.0.0 --port 8000 --workers 2
```

The app reads the scan page's build once, at startup, so it serves a new
build only after a restart. Delete the previous version's folder once the
restarted app is serving. Until there is a build, `/scan` and `/register`
answer 503 and the app logs a warning. Its `frontend` log line at startup
says which folder it serves.

`GET /api/healthz` answers 200 when the app can reach the database, and 503
when it cannot. Use it as a readiness check, not a liveness check: a
database outage should take the app out of rotation, not restart it.

The limit on guessing registration phrases is kept per process, so each
worker allows its own ten attempts a minute per address.

## Registering devices

Every phone or computer that records anything must be registered. Until
there are administrator pages, make a registration code on the server:

```sh
cd backend
uv run python -m inventory_tng.registration_codes create --uses 5 --days 1 \
    --base-url https://<your host>
```

It prints a phrase, a link and a QR code, once. The link and the QR code
open the registration page with the phrase filled in; the phrase can also be
typed there, at `/register`. `revoke`, `devices` and `revoke-device` manage
codes and devices; `--help` lists them.

## Scan page settings

Administrators set how the scan page behaves; the settings live in the
database. Until there are administrator pages, change them on the server:

```sh
cd backend
uv run python -m inventory_tng.scan_settings show
uv run python -m inventory_tng.scan_settings set --name-check-minutes 20
```

`--name-check-minutes` is how long a phone goes unused before the page asks
whether the name chosen is still the person holding it; 0 never asks
(docs/decisions/0011).
