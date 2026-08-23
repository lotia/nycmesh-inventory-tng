# Observability

What this application records about itself, and what it is not allowed to
record about the people using it.

Two halves of this are documented where they are used rather than repeated
here: reading logs while developing is
[DEVELOPERS.md](../DEVELOPERS.md#reading-the-logs-while-you-work), and reading
them from a cluster — along with where traces and metrics are sent — is
[deployment.md](deployment.md#telemetry). Why the arrangement is what it is, in
every case, is
[decision 0021](decisions/0021-telemetry-over-otlp.md).

## What telemetry may carry

**Nothing reaches an exporter unless this application has declared it.** Not
"everything except the fields somebody found a problem with" — a list of what
to strip is a list of the leaks already discovered, and the next instrumented
library arrives carrying fields nobody wrote down. So a span attribute, a
metric attribute or a log key that is not named in
[`backend/src/inventory_tng/redaction.py`](../backend/src/inventory_tng/redaction.py)
is dropped before anything can send it. That file is the list, and it carries
the reason each entry is where it is.

Three mechanisms enforce it, one per signal, because that is what the SDK gives
hooks for: a span processor that runs before the exporting one, a metrics
`View` applied to every instrument, and the last processor in the log chain.
All three are fed from the same two sets.

What is allowed, in outline:

| Signal | Allowed |
| --- | --- |
| Spans and metrics | the templated route, the method, the status, the scheme and protocol, this server's own name and port; the database system, name, account and statement |
| Log records | the field contract every record carries — timestamp, level, logger, message, exception, `trace_id`, `span_id` — the keys bound for the life of a request, and the handful libraries attach through `extra=` |

What is denied, and is worth naming because each is a field somebody will
eventually miss:

- **The caller's address.** An IP address is personal data under the GDPR, and
  this application already handles one in order to throttle. The throttle needs
  it in memory; nothing needs it in a telemetry backend.
- **The concrete URL and its query string.** `http.route` says
  `/api/items/{id}`, which is what a dashboard groups by. `http.url` says
  `/api/items/4172?volunteer=Ada`, which is who.
- **The user agent**, which is a device fingerprint in all but name.
- **`sql` and `params`.** Django's own query logger interpolates the parameters
  into the statement before logging it, so those two keys carry whatever was in
  the query — which in this application includes volunteers' names. The
  `db.statement` attribute on a span is a different thing and is allowed: the
  instrumentation records the statement Django built, which has placeholders
  where the values go.

  Holding those two keys back removes a *duplicate* and no more: Django writes
  the same interpolated query into the message as well, and a message is free
  text. So a developer reading queries locally needs `DJANGO_DEBUG` and the
  logger's level and nothing else — the toggle below changes nothing about it.
  Anywhere a collector is watching, `DJANGO_DEBUG` is off and Django's query
  logger is silent whatever its level says.

**A boundary this cannot police**, said here rather than discovered: the free
text of a log message, and of an exception's message, is whoever wrote it. An
allowlist governs fields. No list can read a sentence.

## Recording personal data on purpose

Deny-by-default is the resting state, not the only state. An IP address is
exactly what somebody debugging a throttle, a scanner, or one volunteer's
failing request needs to see, and a mechanism with no way to look is one people
work around.

```bash
TELEMETRY_PERSONAL_DATA=recorded
```

That re-admits one enumerated group — the caller's address, the concrete URL
and query string, the user agent, and the `sql` and `params` log keys — and
nothing else. It is a second list rather than a hole in the first, so what it
admits can be read in one place and nothing joins it by accident.

Four things are true of it, and each is there to make it recoverable rather
than permanent:

- **It is off unless somebody turns it on.** In the code, in `.env.sample`, in
  `compose.yaml`, and in the chart, which never ships anything else.
- **A value it does not recognise stops the process.** There is no spelling
  that quietly means `redacted`. Believing redaction is on when it is off is
  the one failure worse than not having the toggle at all.
- **The process says so at startup**, once, naming the setting — on standard
  error, in every drawing, including the JSON a deployment writes.
- **What it emits is marked.** Every record, every span and the resource itself
  carry `personal_data`, so whoever holds the collector can find that data
  again and delete it without having to work out which window was affected.

**What turning it on makes true**, plainly: the telemetry then contains personal
data, and every obligation that carries — how long it is kept, who can read it,
how it gets deleted — moves to whatever it lands in. Decision 0021 is where that
trade is weighed. In practice: turn it on for the afternoon, look, turn it off,
and purge what was marked.

It is not what to set to read SQL while developing, though it looked like it:
`DJANGO_LOG_LEVELS` and `DJANGO_DEBUG` are the whole of that, for the reason
given under `sql` and `params` above.
