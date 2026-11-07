# 0008 — Redaction: secrets always, personal data outside development

**Status:** accepted · 2026-10-31 · amended 2026-11-01 (secrets may be
unmasked in development)

## Context

Logs (0005) and telemetry (0006) leave the pod. Volunteers' names, emails and
phone numbers must not go with them, and neither must passwords, tokens or
sessions. Code is meant not to log them at all (CONTRIBUTING.md, Logging),
but a field can be logged by mistake. Libraries add data of their own:
PostgreSQL quotes values in its error messages (`Key (email)=(...) already
exists`), and OpenTelemetry's instrumentation records query text, query
strings, user agents and client addresses on spans. While debugging, though,
that data is often exactly what is needed.

## Decision

- **`APP_ENV` is `development` or `production`**, and defaults to
  production, so forgetting it is safe.
- **Secrets are masked always**, by field name, in every environment and
  output (console, JSON, OTLP). The one exception: `UNMASK_SECRETS=true` with
  `APP_ENV=development`. Anywhere else, setting it stops the app at startup
  rather than being ignored, and while it is on each process logs a warning.
- **Outside development, personal data is redacted too.**
  - Logs: fields whose names mean personal data, and `sensitive_*` fields,
    are masked. So are the values that database and validation errors quote,
    in exception messages and tracebacks.
  - Traces: an allow-list of span attributes at the exporter, failing closed
    (inventory-tng-8i4.39 has the list and the evidence).
- **In development, nothing but secrets is redacted** by default, for the
  most information while debugging. `LOG_REDACTION=on` or
  `TRACE_REDACTION=on` turns redaction on, to test it.
- **Outside development, redaction is turned off only for a limited time**,
  one signal at a time: `LOG_REDACTION_OFF_UNTIL` or
  `TRACE_REDACTION_OFF_UNTIL`, an ISO 8601 time at most
  `REDACTION_OFF_MAX_HOURS` (default 6) away. Past it, redaction comes back on
  by itself. While it is off, each worker warns at startup and hourly, and
  log lines and spans are marked.
- **The OpenTelemetry Collector's redaction processor is a second layer**,
  not wired to these switches, and never the only one: the Datadog Agent has
  no generic allow-list.

We rejected redacting only in the Collector or Datadog: the data would leave
the pod unredacted, each operator would configure it again, and our tests
could not check it.

## Consequences

- Masking by field name cannot see inside free text or message strings, so
  the rule about what to log still applies; masking is the safety net.
- Fields named for personal data are masked even when the value is harmless.
  `name` counts only alone or after a word for a person (`volunteer_name`),
  so `database_name` is kept. Log ids instead of names.
- Redacted tracebacks lose quoted names too (tables, constraints). Log
  `describe_error()` alongside, which keeps the constraint.
- Built in stages: secrets, personal data and `APP_ENV` (inventory-tng-8i4.37);
  the log switches (8i4.38); traces (8i4.40).
- Unredacted spans still have the values of secret query parameters masked
  (`token`, and `code`, which carries the registration phrase and sign-in
  codes), unless `UNMASK_SECRETS=true`. A span that leaves unredacted outside
  development carries `inventory.redaction=off`.
