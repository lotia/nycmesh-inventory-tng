# Observability

What this application records about itself, and what it is not allowed to
record about the people using it.

Reading a log stream in a terminal is documented where it is done rather than
repeated here — while developing in
[DEVELOPERS.md](../DEVELOPERS.md#reading-the-logs-while-you-work), and from a
cluster in [deployment.md](deployment.md#reading-the-logs), which is also where
a deployment's own settings live. Why the arrangement is what it is, in every
case, is [decision 0021](decisions/0021-telemetry-over-otlp.md).

## Somewhere to send it, on a laptop

The stack in `compose.yaml` ships a collector, behind a profile so that nobody
who has not asked for telemetry pays for a second container:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4318 \
  podman compose --profile telemetry up -d
```

Grafana is then on <http://localhost:3000>, with no sign-in: it has Tempo,
Prometheus and Loki behind it, already wired to each other.

**On the command line rather than in `.env`**, and that is worth a sentence.
`http://collector:4318` is a name on the container network; the settings module
reads the same `.env` file, so a backend you run natively beside the stack —
which is the ordinary development arrangement — would take that name, fail to
resolve it, and retry against nothing. Keep the address where it is only true.
A native backend wants `http://localhost:4318`.

Logs are the half that needs a second command, and the reason is the whole
architecture rather than an omission. The backend writes them to standard
output and nothing else, so getting them into a collector is somebody else's
job — on a Kubernetes node it is a log agent, and here it is a pipe:

```bash
OTEL_SERVICE_NAME=inventory-tng-backend \
OTEL_RESOURCE_ATTRIBUTES=deployment.environment=compose \
  podman compose logs -f backend | scripts/ship-logs
```

Those two are the same values `compose.yaml` gives the backend, repeated
because this end runs on your machine and inherits nothing from the container.
They are read by OpenTelemetry's own resource detector, so what they mean here
is exactly what they mean to the SDK exporting the spans.
They have to match: a collector files logs and spans by their resource, so a
different service name puts them under two unrelated services, and a missing
`deployment.environment` makes a dashboard scoped by it drop every log record
while showing every span.

`scripts/ship-logs` reads that stream and posts it, passing every line onward
so `| scripts/pretty-logs` can sit after it and draw what a collector received.
It is a development tool: no retry, no queue on disk, and a collector that is
down loses whatever was in flight.

### What it proves

Make a request — `curl localhost:8000/api/healthz` — and all three signals are
there within a few seconds:

| In Grafana | What to look for |
| --- | --- |
| **Explore → Tempo**, search | one trace per request, named for its route, with each database query beneath it as its own span |
| **Explore → Prometheus** | `http_server_duration_milliseconds_count`, by method, status and scheme |
| **Explore → Loki**, `{service_name="inventory-tng-backend"}` | the records the backend wrote, with `logger` and `severity_text` to filter on |

**One caveat about following a request from its trace into its logs**, because
it is the thing somebody will try first. A record written *inside* a request
carries the trace's id and Grafana will jump straight to it. Django's own
records for a 4xx or a 5xx do not, and cannot: it logs those from
`BaseHandler.get_response`, after the middleware chain has returned and the
instrumentation has already ended its span. Neither does an access line, from
`runserver` or from gunicorn, which is written outside the application
altogether. So the jump works for records this application writes itself, and
writing them is `inventory-tng-nb8.9`.

### Why this image

`grafana/otel-lgtm` — Grafana, Tempo, Prometheus, Loki and an OpenTelemetry
Collector in one container, no configuration to write, OTLP in on 4317 and
4318. Grafana's own project, and documented by it as being for development,
demo and testing rather than for production, which is exactly the claim being
made here.

What it was chosen over, against one container and no configuration to write:

- **SigNoz**, **Uptrace**, **HyperDX** — OpenTelemetry-native and closer to
  what a production deployment might want, but each is a compose stack of its
  own: a query service, a UI and a ClickHouse to keep. Three or four containers
  to bring up a laptop.
- **OpenObserve** — genuinely one container and lighter than this, and a real
  candidate. It loses on the criterion that decided it: following one request
  across its logs, its metrics and its traces is Grafana's oldest trick, and
  the trace-to-logs link is already configured in this image with nothing to
  set up.
- **Jaeger all-in-one** — traces and nothing else.
- **Elastic with Kibana** — the heaviest of them, and the reason to think twice
  rather than the thing to reach for.

The second criterion is that this doubles as the worked example for whoever
stands up a cluster, and the Grafana stack is the one a volunteer organisation
is most likely to run for itself.

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
