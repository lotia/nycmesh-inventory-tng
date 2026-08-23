# Observability

What this application records about itself, where that can be sent, and what it
is not allowed to record about the people using it.

**Two readers.** A developer wants somewhere for telemetry to go on their own
machine and a way to look at it, which is the first section and needs one
command. Somebody standing a cluster up wants to know what this repository
ships (a collector for development, and nothing for production), what it does
not (any destination at all), and what they have therefore to decide — which is
[Choosing a destination](#choosing-a-destination) onwards. Both want the last
two sections, because what may be recorded about people is not a deployment
setting.

Reading a log stream in a terminal is documented where it is done rather than
repeated here — while developing in
[DEVELOPERS.md](../DEVELOPERS.md#reading-the-logs-while-you-work), and from a
cluster in [deployment.md](deployment.md#reading-the-logs), which is also where
a deployment's own settings are listed. Why the arrangement is what it is, in
every case, is [decision 0021](decisions/0021-telemetry-over-otlp.md).

## Somewhere to send it, on a laptop

From a clone, this is three commands. The first two are the ordinary setup in
[DEVELOPERS.md](../DEVELOPERS.md#running-it) — copy `.env.sample` to `.env`,
bring the stack up — and the third is this one, which adds a collector
behind a profile so that nobody who has not asked for telemetry pays for a
second container:

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

## Choosing a destination

Nothing above this line applies to a deployment. A laptop's collector is one
container that forgets everything; a deployment has to decide where telemetry
lands, who can read it, how long it is kept, and what happens when that place
is unreachable. **This repository ships no answer to that**, and the section
after this one says why the chart does not try.

Where this application will run is not settled — it may be CodeNOW, it may be a
Kubernetes cluster NYC Mesh hosts itself on Proxmox — so nothing here assumes a
platform that collects a container's output for you. If yours does, you will
know; if it does not, the whole log story is yours to build, and
[deployment.md](deployment.md#reading-the-logs) is what that costs when nobody
has.

There are three shapes, and the thing that actually distinguishes them is
**where the credential lives**. That is the part nobody explains and the part
that decides how much of this you can change later.

### Straight to a vendor

Point `OTEL_EXPORTER_OTLP_ENDPOINT` at the vendor's ingest URL and put its API
key in `OTEL_EXPORTER_OTLP_HEADERS`. Nothing else to run.

The key is then in the application's own environment, which means it is in the
Secret every backend pod mounts, it rotates on a redeploy, and the pods talk to
the internet. There is no queue: a vendor that is unreachable loses whatever
was in flight, in a process that is also serving requests. Changing vendor is a
redeploy of the application.

Right for getting started, and for a deployment small enough that the vendor's
free tier covers it.

### Through a collector you run

Point the application at an OpenTelemetry Collector inside the cluster; the
collector holds the credential and forwards.

The application then knows one plaintext in-cluster address and no secret at
all, which is the point. The collector can fan out to more than one
destination, can be given a disk-backed queue so that a destination being down
is a delay rather than a loss, and can drop or rename attributes without a
release of this application. Changing vendor becomes a change to the
collector's configuration.

The cost is a component to run and to keep upgraded. Right for anything that is
going to be around, and it is the shape to grow into.

### Standard output, scraped by an agent

Logs are already there — this application writes them and nothing else — so an
agent on the node can pick them up with no cooperation from the application at
all. Traces and metrics still need one of the two above.

The credential lives in the agent, which is usually one DaemonSet for the whole
cluster and somebody else's problem if the cluster already has one. Ask before
building anything: a cluster that already ships container output somewhere has
solved the log half already.

## For a cluster you host yourself

Concretely, because "run a collector" is not an instruction.

**A log agent, as a DaemonSet.** One per node, reading the container output the
runtime already writes.

| | |
| --- | --- |
| **Grafana Alloy** | The Grafana stack's own, and the natural choice if the backend is Loki. Speaks OTLP as well as scraping, so it can be the only agent |
| **Fluent Bit** | The smallest of them, tens of megabytes of memory per node, and the one to pick if the node budget is tight |
| **Vector** | The most capable at reshaping a stream before it lands; heavier, and worth it only if that is a problem you have |

**A collector, as a Deployment.** Not a DaemonSet: the application posts to it
over the network rather than to a node-local address, so it scales with
telemetry volume rather than with node count, and two replicas behind a Service
survive an upgrade. A sidecar per pod is the third option and is for isolating
one noisy application, which is not this problem.

Between them that is roughly 100–200 MB of memory per node for the agent, plus
whatever the collector Deployment is given — a few hundred megabytes for a
deployment this size. The storage is the part that surprises people, and it is
decided by retention rather than by traffic.

**Where it lands**, with real costs:

| | |
| --- | --- |
| **Grafana stack** — Loki, Tempo, Prometheus, Grafana | Four components, each of which can be given object storage rather than volumes, which is what makes the retention affordable. The most work to stand up and the most widely known once it is. What the development collector here is, unbundled |
| **SigNoz** | OpenTelemetry-native, one UI over all three signals, and fewer moving parts to reason about — but a ClickHouse underneath it that somebody has to look after |
| **Elastic with Kibana** | The heaviest by a distance, in memory and in operator attention, and the reason to think twice: a volunteer organisation running this is committing somebody's evenings to it |

**Retention is the decision that costs money**, not the choice of backend. Pick
it first — a fortnight of logs and a couple of days of traces answers almost
every question anybody actually asks — and size the storage to that.

### The chart does not render a collector

Deliberately, and [decision 0021](decisions/0021-telemetry-over-otlp.md) is
where that is argued. What it means in practice: rendering one here would give
the next application on the cluster a second copy of it, and would make an
upgrade of this chart move a piece of the cluster's plumbing.

`django.otlpEndpoint` is the whole of the connection between them, and it is
empty by default. A release that says nothing starts no SDK at all.

## How much is recorded

`OTEL_TRACES_SAMPLER_ARG` is a fraction between 0 and 1.

**The code's own default is `0.1`**, deliberately conservative: a release that
configures nothing cannot flood a collector somebody else sized. **Every
configuration this repository ships sets `1.0`** — `.env.sample`, `compose.yaml`
and `django.tracesSamplerArg` in the chart — so the behaviour intended is the
one that runs, and the safe behaviour is what happens in its absence.

To sample a subset, lower the shipped value: `0.05` keeps one trace in twenty.
Logs are unaffected by it — sampling is about traces, which are expensive per
request in a way records are not.

They are not, however, unconditionally complete, and the one exception is worth
knowing before an incident rather than during one. Records under
`django.security` — a `Host` this deployment does not answer to, a CSRF failure,
a session that would not decode — are rationed by `DJANGO_SECURITY_LOG_RATE`,
because that path is internet-facing and takes no credential.
[deployment.md](deployment.md#reading-the-logs) is what that means. It is a
ration rather than a silence: whatever is held back is counted, and the count
arrives on the next record from that same logger, so a gap is always labelled
with its size.

`OTEL_TRACES_SAMPLER` picks which sampler: `parentbased_traceidratio` (the
default and the one to keep), `always_off`, or `always_on`. A value that is
none of these stops the process rather than quietly becoming the default.
`OTEL_SDK_DISABLED=true` turns everything off, which is the switch to reach for
during an incident.

**A caller cannot ask for more.** A request arriving already marked as sampled
is put through the same rate rather than recorded outright: `TraceIdRatioBased`
decides from the trace id, so a browser and this backend running the same rate
reach the same answer about the same trace and it stays in one piece — without
either end trusting the other. That matters because the sampled bit is one
header on a request that needs no credential.

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
