# 0006 — Telemetry: metrics and logs over OTLP, traces on demand

**Status:** accepted · 2026-10-30 · amended 2026-11-19 (measured and
logged once committed; camera sessions) and 2026-11-27 (every refusal
logged and counted together; a rule's refusal counted once; a form's own
problems counted; a write's metric measured only once committed)

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
  out, what an older build left in storage and what volunteers did with
  it, and a cart discarded to `/api/telemetry`, which records them. Its
  schema limits what may be sent.

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

## Amendment: measured and logged once committed (2026-11-19)

Decided by Ali on 2026-11-19 (inventory-tng-3v8m): what a write did is
measured and logged only once its transaction commits, through one
function, `once_committed` (`core/db_once_committed.py`). The function
that writes a table registers its metrics and log lines there, so every
caller is counted, and a write rolled back records nothing. Before,
routes called measure functions after `session.commit()`, and a new
caller had to remember to: closing a package and length checks were
missed from `inventory.counts.recorded` that way.

We rejected keeping the hand-written calls and adding a test that each
route measures its write.

What was refused, or failed, is measured and logged at once, as nothing
of it commits. Debug lines trace an attempt as it happens.

### A write's metric measured only once committed (2026-11-27)

Decided by Ali on 2026-11-27 (inventory-tng-8xs0): a write's metric is
a `WriteMetric` (`core/write_metrics.py`), with no `add()` or
`record()`. Its one method, `on_commit()`, registers the measure and the
write's log line with `once_committed` together, so `ty check` refuses a
write measured by hand, before its commit or after. Before, a codebase
test caught one after a commit, but none before: a call site cannot tell
a write's success from a refusal or a note. Every other counter or
histogram is declared where it is made too: given to `refusals()`, or
to `at_once()` (`core/metrics_at_once.py`) with why it measures no
write, from a fixed list, such as a browser's report or a failure; a
codebase test fails on one made bare. We rejected an allow list of the
notes logged inside a transaction, or of the metrics added at once,
which every new one would need.

## Amendment: camera sessions (2026-11-19)

Decided by Ali on 2026-11-19 (inventory-tng-8i4.63): the scan page sends
one report per camera session, when the camera closes, saying how it
ended, how long the camera took to open, and the labels read in four
kinds: item, package, place and unknown. Browsers are told apart by the
decoder in use, the browser's own or the ponyfill (0010), so no user
agent is read. Each kind of page report has its own allowance, refilled
every hour, so a hub tablet left open all day keeps reporting.

A session is also reported as the page is hidden or left with the camera
open, by `sendBeacon`, which outlives the page; shown again, what follows
is a session of its own. Reports lost offline are not kept for later:
they are numbers, not records, and stay out of the outbox. The endpoint
reads no cookie, so a report cannot be joined to a registered device.

## Amendment: every refusal logged and counted together (2026-11-27)

Decided by Ali on 2026-11-27 (inventory-tng-h3k0, inventory-tng-vj5h):
one helper logs a refusal and counts it together, with its reason from a
fixed list in code. Each kind of refusal is declared by its call's
signature (`@refusals` in `core/refusals.py`), every attribute a
`Literal`, so `ty check` refuses a reason not on its list before any test
runs, and a codebase test fails on a refusal logged by hand. About thirty
refusals had been logged but never counted, such as the emailed password
reset not made.

Only values the app learns as it runs can be off their list: a check
constraint the database names, a page's template, a table. Such a
refusal, or one with an attribute left out, is still counted, with that
attribute `unlisted`, and logged as an error naming the refusal and the
attribute, never the value, so no count is lost.

Each kind of refusal keeps a counter of its own, named for what is
refused (`inventory.<things>.refused`, by `reason`), rather than one
`inventory.refusals` counter told apart by an attribute. What is refused
differs in its unit (a request, a form, a report), and a total across
kinds would mean nothing, so OpenTelemetry's naming conventions give each
its own name. Counters that measured refusals already kept their names
and attributes, and now count through the helper, but for those of a
rule's refusals, folded into one below.

Operator commands log their refusals but count none: telemetry starts
only in the server, so a counter in a command would always read nothing.

### Rules refusing a change, counted once (2026-11-27)

Decided by Ali on 2026-11-27 (inventory-tng-v8sq): a change a rule
refuses is logged and counted once, in `core/db_errors.py`'s
`_attempted()`, which every page's change passes through
(`inventory.changes.refused`, by `rule`). The rule's name is the
attribute, from `core/rule_names.py`'s `RuleName`, a `Literal` that
`enforce()`, `RuleBroken` and `web/forms.py`'s `RULES` take, rather than
a reason per page. Many such refusals had been said on the page and never
logged.

Pages that had counted their own rule refusals no longer do, so none is
counted twice: adjustments, answers to lines recorded more than moved,
package tracking, reading rules, the item page's forms, unit changes and
recounts, and a count's Bring it back. A count form keeps its own counter
for what its form refuses before the save. A form shown before its row
changed is counted by `inventory.edits.stale` alone, by table.
`tests/codebase/test_rule_refusals.py` fails on a rule refusal caught
anywhere else, and on a rule missing from the list.

### A form's own problems, counted where the page shows them (2026-11-27)

Decided by Ali on 2026-11-27 (inventory-tng-v8sq): a form's own problems
(a box left empty, a number that is not one) are logged and counted too,
by form and kind from fixed lists. A form's check says each as a
`Problem` with its kind (`web/form_problems.py`), and `page()` in
`web/pages.py`, which draws every page, counts those it shows
(`inventory.forms.problems`, by the page's template and `kind`). A rule's
words beside a box are no `Problem`, so a rule refusal is not counted
again. The count form's box problems and an answer to a line recorded
more than moved refused by its form are counted here alone now.
`tests/codebase/test_form_problems_counted.py` fails on a forms module
that leaves a phrase of its own bare.
