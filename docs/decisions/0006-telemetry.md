# 0006 — Telemetry: metrics and logs over OTLP, traces on demand

**Status:** accepted · 2026-10-30

## Context

The app should be well instrumented, and its data should reach Datadog or
any other OpenTelemetry backend. It is one small service with one database
and no outbound calls, run as one uvicorn process per container (0007), and
behind an ingress proxy that may emit traces of its own. The old attempt was
judged too heavy, especially in the browser: about 1,000 lines and 9 packages.

## Decision

- **Metrics and logs, exported over OTLP** (OpenTelemetry's wire format) by
  the OpenTelemetry SDK. Its FastAPI and psycopg packages measure each
  route's request rate, errors and duration, and each query's duration. A
  gauge reads the connection pool. Our own metrics are named `inventory.*`.
  Logs (0005) are exported the same way.
- **A request id on every log line written during a request**, taken from
  the ingress's headers when it sends one and echoed in the response, so
  ingress logs and app logs join without tracing.
- **No traces by default.** Setting `OTEL_TRACES_EXPORTER=otlp` turns them
  on, recording only requests the ingress chose to trace. The deploy bead
  decides, once the ingress is known.
- **Off unless configured.** With no endpoint, nothing starts, in
  development or tests. An optional dashboard in compose shows telemetry
  locally.
- **The app knows no vendor.** It sends to an endpoint: the Datadog Agent's
  OTLP receiver, or an OpenTelemetry Collector.
- **The scan page uses no OpenTelemetry.** It sends errors, Web Vitals,
  outbox retries, scan-to-save time, a tab learning that a newer build is
  out, and what an older build left in storage and what volunteers did with
  it to `/api/telemetry`, which records them. Its schema limits what may
  be sent.

We rejected always-on tracing and OpenTelemetry in the browser: neither
earns its cost here.

## Consequences

- Five direct dependencies (the SDK, the OTLP exporter, and the FastAPI,
  psycopg and logging packages), about 20 packages, always installed
  and idle unless configured.
- Metric attributes take a few fixed values, so personal data has no field
  to go into.
- A slow request is placed by route and query, not by trace. If that stops
  being enough, tracing is a setting, not a project.
- OpenTelemetry's Python log export is still marked in development upstream,
  so it may change.
- When logs are exported over OTLP, the cluster must not also collect the
  app's output, or each line arrives twice.
- Where the providers start, and why, is 0007.
