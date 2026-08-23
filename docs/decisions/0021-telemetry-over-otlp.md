# 0021 — Logs on standard output, traces and metrics over OTLP

**Status:** accepted

## Context

This application had no telemetry of any kind. Not thin telemetry — none. There
was no `getLogger` call anywhere in it, no metric, and `GET /api/healthz` ran a
trivial query and said `ok`. Django's shipped configuration was left in place,
and that configuration routes every record to one of two handlers chosen by
mutually exclusive filters, neither of which a deployment can have: a console
handler that only runs with `DEBUG` on, and an email handler that needs an
`ADMINS` list. So a deployed process discarded everything, silently, including
the tracebacks Django writes for unhandled exceptions.

The price was paid before it was noticed. A misconfigured probe presented as a
crash loop against a database that was perfectly healthy, and the reason it took
as long as it did to diagnose is that the `DisallowedHost` explaining it reached
nowhere at all. What the cluster showed was a 400 with no body and a container
being killed. Everything needed to explain that had been generated and thrown
away.

Adding "some logging" would have fixed that particular afternoon. The question
this record settles is the shape of the thing, because the parts interact: what
a record contains decides what can be searched, where it is written decides what
survives a crash, and who renders it decides whether anybody reads it at all.

## Decision

### Logs go to standard output as JSON, and are collected from there

Not exported over OTLP from inside the application. Three reasons, in the order
they matter.

A line written to a stream survives the process that wrote it. An exporter with
a buffer does not: the records explaining a crash are the ones still in the
buffer when it happens. `kubectl logs` keeps working when the telemetry
pipeline is the thing that is broken. And nothing is placed in the request path
that can fail, block or add latency of its own.

Traces and metrics are different and do go over OTLP, because there is no
equivalent of "the runtime already collects your standard output" for them.

### One processor chain, two drawings

`structlog` is the accepted dependency, and it is one dependency doing two
jobs. Every record — this application's, Django's, DRF's, gunicorn's — goes
through the same chain and is then drawn either as JSON or in columns for a
person.

The property being bought is that the two differ **only** in drawing. A
developer debugging against console output is looking at the same fields, with
the same names, that a deployment emits; there is no second configuration that
only exists in production and that therefore nobody has read. The alternative
considered was a `logging.Formatter` subclass of about sixty lines and no
dependency, which this repository's habits argue for. It was rejected because
it is two renderers to write and maintain rather than one chain with two ends,
and because bridging stdlib logging so that a library's records carry the same
keys as ours is the part that is fiddly and that structlog already does.

### Rendered on read, wherever something is collecting

Where a collector is watching — under compose, and deployed — the process
writes JSON and nothing else. Readability is a *read-time* concern: a
pretty-printer in this repository redraws the stream in the terminal of whoever
is reading it.

This is better than a readable write format in two ways that are not obvious.
There is one format in the system rather than two, so nothing has to be
configured before a stream can be parsed. And the reader can measure the
terminal, which the writing process cannot: a process logging into a pipe has no
width, and guessing one is how output ends up wrapped for a terminal nobody has.

Native development, which has no collector and no pipe, draws columns directly.

### Nothing adapts silently

Layout is chosen by measuring the terminal, and then **announced**: one line at
startup naming the width found, the layout picked, what that layout leaves out,
and the variable that overrides it.

Output that changes shape on its own without saying so is magic, and the cost
lands on somebody else — a developer comparing their console with a colleague's
and finding a column missing has no way to discover why. The same rule is why a
log level or a layout name the process does not recognise stops it at boot
rather than quietly becoming the default. Being given something other than what
you asked for, without being told, is worse than being stopped.

### Timestamps carry the date and the offset

A container logs UTC; the terminal reading it does not. A bare `14:32:07` is
therefore not merely terse, it is misleading — and a line pasted into an issue
should say when it happened without anyone having to ask where it came from.
Narrow layouts drop the date when *drawing*; the record itself always has it,
so what a collector receives never depends on who was watching.

### Context is bound, not passed

Request and process identity — a request id, the method, the templated path, the
status, `trace_id`, `span_id`, a surrogate user id — is bound for the life of a
request rather than passed as arguments. A developer writing `log.info("...")`
with no keys at all still gets all of it, and only ever types the keys specific
to that line.

This is the decision that makes instrumenting the rest of the application cheap
instead of a discipline nobody keeps. Its rendering half: the console hides
keys that are on every line, because a request id repeated forty times is noise,
and shows them on demand when following one request is the point.

### The SDK starts in the worker, not by wrapping the command

It is started from a `post_fork` hook in gunicorn's own configuration, and from
the WSGI module, rather than by wrapping the command in
`opentelemetry-instrument`. The wrapper is rejected because it hides the
arrangement in a command line, and because the configurator it relies on is
reachable no other way.

**A correction, recorded because this record asserted otherwise.** The usual
argument for `post_fork` is that a `BatchSpanProcessor`'s exporter thread does
not survive `fork()`, so an SDK initialised in the master exports nothing at
all. That was the reasoning here, and it is not true of the version this
project pins: measured on SDK 1.44.0, it registers a fork handler and rebuilds
the batch processor in the child, and a span created after a fork is exported.

The hook stays anyway, for a reason that does not depend on upstream: it puts
the provider in the process that serves the requests whatever `preload_app` is
set to. What changes is the claim — an argument that has stopped being true is
worse than no argument, because the next person reads it and believes it.

**The other half is not a matter of upstream behaviour and does still bite.**
Django's instrumentation reads `settings` on the way in and, finding none
configured, calls `settings.configure()` itself — binding that process to an
empty settings object with no apps, no urlconf and no database, for good. From
`post_fork` that is exactly the state, because gunicorn has not imported the
application yet. So the framework is instrumented from the WSGI module and
never from the hook.

**And it is instrumented before the application is built, not after.** That is
the second half of the same constraint, and getting it wrong cost a release:
instrumenting Django does one thing, which is to put a middleware at the front
of `settings.MIDDLEWARE`, and Django's handler reads that list exactly once, as
it is constructed. A module that builds the application and *then* instruments
changes a list nothing will read again, so the server runs without the
middleware — no span for a request, no HTTP metrics, and an empty `trace_id` on
every record because there is no request span for one to belong to. Nothing
reports it; the traces simply arrive with a database query at the root.

So the entry modules call `django.setup` themselves, then start, then build.
`django.setup` is what `get_wsgi_application` would have called first anyway,
so making it explicit changes the order and nothing else. `inventory-tng-iqff.1`
is where that was found, and what makes it worth writing down here is that
Django's test client builds a handler per request and therefore cannot see the
difference: the tests asserting server spans passed for months against an
arrangement no server had.

### The debug flag is signed, and the sampler is not `ParentBased` alone

A request may ask to be recorded in full, which sets the W3C sampled bit and a
custom sampler honours it. It may not be a bare `ParentBased` sampler: that
hands an unauthenticated client the decision about what the backend records, and
with function-level tracing behind it that is a work-amplification vector rather
than a convenience. The flag is gated by a signed, expiring token.

Ordinary traffic is sampled at a rate that is configurable rather than fixed.
The code's default is deliberately conservative, and the configurations this
repository ships override it upwards explicitly — so a deployment that sets
nothing cannot flood a collector somebody else sized, while the rate actually
wanted is visible in a file rather than implied by a default.

### No personal data, enforced by an allowlist

Telemetry is the easiest place in this system to leak personal data, because
nobody reads a span attribute the way they read a template. The mechanism is
deny-by-default: attributes not on an allowlist do not reach an exporter.

An allowlist that has to be edited to add a field makes each addition a decision
somebody takes on purpose. A denylist is a list of the leaks that have already
been found. IP addresses are personal data and are not exempt, and neither is a
stable pseudonymous identifier for a volunteer who never signed in —
pseudonymised is not anonymous.

**Amended 2026-08-23, at Ali's asking: there is a way to look.** The paragraphs
above were written as though deny-by-default were the only state, and that is
not the decision. A volunteer's address is exactly what somebody chasing a
throttle, a scanner, or one failing request has to see, and a rule with no way
to look is one people route around — by reading a production database, by
turning off the thing that was protecting them, or by giving up on the
investigation. So there is a setting that re-admits an enumerated group, and
deny-by-default is where it rests rather than where it is nailed.

What makes that safe to have is not the setting but the four conditions on it,
and they are the decision. It is off in the code and in every configuration this
repository ships, so turning it on is an act somebody performs on a release they
are watching. A value it does not recognise stops the process, because being
misread in the permissive direction is a disclosure and there must be no
spelling that quietly means "redacted". The process announces it at startup,
because this record already refuses to let a console layout change silently and
it would be absurd to hold this to a lower bar. And what it emits is marked, so
that turning it on is reversible: whoever holds the collector can find that data
and delete it rather than reasoning about which window was affected.

It is a second enumerated group rather than a hole in the first, so what it
admits is readable in one place. The cost is stated rather than implied:
telemetry then contains personal data, and wherever it lands inherits that —
retention, access and deletion — for as long as it is kept. That is a reason to
turn it on for an afternoon, not a reason to have refused it.

**The debug token does not carry this permission**, decided in
`inventory-tng-nb8.4` and recorded here because the two mechanisms are next to
each other and the temptation to merge them will come round again.

The token is a better shape than an environment variable in every way that
suggests merging them: it is per request, it expires by itself, and an
administrator hands it out deliberately. What decides it is that the two
permissions have different consequences and different lifetimes. Recording a
request in full costs processor time and collector volume, and stops when the
token does. Recording somebody's address creates a disclosure and a retention
obligation in whatever the telemetry lands in, and that outlives the token by
however long that place keeps things — which is not a property the person who
minted the token controls, or necessarily knows.

The tokens also travel: an administrator sends one to a volunteer through a
chat message or an email, where it is forwarded, quoted and kept. A link that
quietly widened what is recorded about everybody near it wherever it was pasted
is not a link worth having made, and "it expires" is no comfort about data
already written down.

### The chart does not render a collector

A collector is infrastructure shared by everything on a cluster, not a component
of this application. This repository ships one for development, where it doubles
as the worked example, and documents how to stand one up for a cluster. Where
that is deployed is not settled — it may be CodeNOW, it may be a cluster NYC
Mesh runs itself — so nothing here may assume a platform that collects container
output on your behalf.

## Consequences

Whatever collects container output is the whole of the log story, so a cluster
that intends to be debuggable needs a collector shipping it somewhere durable.
Nothing outlives a pod otherwise. [deployment.md](../deployment.md#reading-the-logs)
is where that is said to whoever is standing one up.

`structlog` is a runtime dependency of the backend image.

Turning tracing on later does not change the shape of a record: `trace_id` and
`span_id` are in the contract from the start and are empty until there is a
tracer, so a collector's parsing and a saved stream both survive the day the SDK
arrives.

And logging stops being something a change may skip. It is an acceptance
criterion on new work, checked by tooling rather than by whether a reviewer
happened to look.
