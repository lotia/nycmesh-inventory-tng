# 0005 — Logging with structlog

**Status:** accepted · 2026-10-30

## Context

Developers want colourised, readable logs, and Kubernetes collects JSON from
the app's output. uvicorn, SQLAlchemy and Alembic all log through
Python's standard `logging`, and their lines must look like ours. Levels come
from the environment. We want the code small and typed, with few
dependencies.

## Decision

- **structlog**, routed through the standard `logging`. One handler writes
  every line, ours and the libraries', in one format.
- **`LOG_FORMAT`** is `console` (colour, the default) or `json` (set by the
  Helm chart). It is always set explicitly, never guessed from whether the
  output is a terminal.
- **`LOG_LEVEL`** sets the overall level, and **`LOG_LEVELS`** sets single
  loggers' levels, such as SQLAlchemy's.
- Exporting logs over OpenTelemetry, and the request id on each line, are in
  0006. Where logging is set up is in 0007.

We rejected loguru, because it replaces the standard `logging` rather than
joining it, and has not been released since 2024. We rejected the standard
`logging` with rich and a JSON formatter, because its colour output cannot
show structured fields.

## Consequences

- One new dependency, structlog, which has none of its own.
- Every log line has the same fields in development and production.
