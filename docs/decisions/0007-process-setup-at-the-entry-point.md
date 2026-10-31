# 0007 — Process setup happens at the server entry point

**Status:** accepted · 2026-10-30 · amended 2026-10-31 (one uvicorn process per
container; no gunicorn)

## Context

Logging (0005) and telemetry (0006) both set up state for the whole process:
handlers on the root logger, and OpenTelemetry providers that export from
background threads. Tests build apps with `create_app()`. If it reconfigured
the root logger, it would remove the handler pytest's `caplog` uses, and tests
could no longer check what was logged.

FastAPI's deployment guidance is one uvicorn process per container, with
Kubernetes (or compose) running more containers to scale.

## Decision

- **`create_app()` builds the app and nothing else:** routes, the engine, the
  lifespan. It never touches process-wide state.
- **`python -m inventory_tng` runs the server.** It configures logging, then
  starts uvicorn with `log_config=None`, so uvicorn leaves logging to us and
  its lines take our format.
- **uvicorn loads the factory `create_server()`,** which configures logging,
  builds the app with `create_app()`, then starts telemetry around it.
  Under `--reload` or `--workers`, uvicorn starts each process fresh rather
  than forking it, so each one sets itself up.
- **Telemetry flushes when the app's lifespan ends.** Exit hooks do not run,
  because uvicorn re-raises the stop signal after a graceful shutdown.
- **One logging setup function,** called by the run command, the factory and
  `migrations/env.py`. Every process logs in one format.
- **No gunicorn.** In Kubernetes, one process per pod. On a single server,
  one process per container scaled by compose, or uvicorn's `--workers`.

We rejected doing the setup inside `create_app()`, because tests would lose
`caplog`. We rejected gunicorn, because a process manager inside a container
duplicates what the cluster does.

## Consequences

- There is one more module than the app strictly needs, and a rule that
  `create_app()` stays free of global setup. Comments in both places point
  here.
- The app is started with `python -m inventory_tng`, not bare `uvicorn`;
  uvicorn's own logging flags are not used.
