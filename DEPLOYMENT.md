# Deployment

How to run the app on a server. To run it on your own computer, see
[CONTRIBUTING.md](CONTRIBUTING.md#running-the-app).

There is no production container image or Helm chart yet
(inventory-tng-8i4.18). Until there is, the app runs from a checkout.

## What it needs

- PostgreSQL. Development and the tests use 18.
- [uv](https://docs.astral.sh/uv/), which installs the Python version the
  backend asks for.
- HTTPS in front of it, from a reverse proxy or ingress. Outside development
  the device cookie is `Secure`: over plain http a browser never keeps it,
  so no device stays registered.

## Settings

Set these in the environment. Only `DATABASE_URL` is required.

- `DATABASE_URL`: `postgresql://user:password@host:port/database`.
- `APP_ENV`: leave unset in production. `development` is for local work
  only: it stops masking personal data in logs and lets the cookie travel
  over http.
- `FORWARDED_ALLOW_IPS`: behind a proxy, the proxy's address, so the client
  address and scheme come from its headers.
- `LOG_FORMAT`: `json` for a log collector; the default is `console`.
- `LOG_LEVEL`, `LOG_LEVELS`: `INFO` by default.
- `LOG_CLIENT_ADDRESS`: `false` when the proxy logs client addresses itself.
- `LOG_HEALTH_CHECKS`: `false` logs successful health checks at debug.
- `OTEL_EXPORTER_OTLP_ENDPOINT`: sends logs and metrics over OTLP/HTTP; off
  when unset.

What each logging setting does is in
[CONTRIBUTING.md](CONTRIBUTING.md#logging).

## Installing, migrating and starting

```sh
cd backend
uv sync --locked --no-dev
uv run alembic upgrade head    # before starting a new version
uv run python -m inventory_tng --host 0.0.0.0 --port 8000 --workers 2
```

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

It prints a phrase, a link and a QR code, once. `revoke`, `devices` and
`revoke-device` manage codes and devices; `--help` lists them.
