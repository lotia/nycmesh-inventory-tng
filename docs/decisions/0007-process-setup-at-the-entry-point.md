# 0007 — Process setup happens at the server entry point

**Status:** accepted · 2026-10-30

## Context

Logging (0005) and telemetry (0006) both set up state for the whole process:
handlers on the root logger, and OpenTelemetry providers that export from
background threads. Two constraints pull against each other:

- Tests build apps with `create_app()`. If it reconfigured the root logger,
  it would remove the handler pytest's `caplog` uses, and tests could no
  longer check what was logged.
- gunicorn starts one process and forks the workers from it. Providers built
  before the fork would be shared by every worker, with the parent's identity
  and its exporter connections, which are not safe to share. So each worker
  builds its own.

## Decision

- **`create_app()` builds the app and nothing else:** routes, the engine, the
  lifespan. It never touches process-wide state.
- **A separate server entry point** is what gunicorn and uvicorn load. It
  configures logging, starts the telemetry providers, then calls
  `create_app()`. gunicorn's `worker_exit` hook shuts the providers down, so
  a stopping worker flushes its last batch.
- **One logging setup function, called everywhere a process starts:** by the
  entry point, by gunicorn's configuration for the master's own lines, and by
  `migrations/env.py` when Alembic runs from the command line. Every process
  logs in the same format.
- **gunicorn's `preload_app` stays off**, so the entry point runs once in each
  worker, after the fork.
- Tests call `create_app()` directly. Tests of the setup call the entry
  point's functions themselves.

We rejected doing the setup inside `create_app()`, because tests would lose
`caplog`. We rejected setting up once before the fork, because the workers
would share one set of providers.

## Consequences

- There is one more module than the app strictly needs, and a rule that
  `create_app()` stays free of global setup. Comments in both places point
  here.
- Turning on `preload_app` would make the workers share providers, so the
  gunicorn configuration says not to.
