"""What telemetry may carry, which is only what is named here.

Nothing reaches an exporter unless one of the lists below says it may. The
argument for arranging it that way is decision 0021's "No personal data,
enforced by an allowlist"; what an operator does about it, including the
setting that re-admits a group, is docs/observability.md. This is the list
itself, and the reason each entry is on the side of it that it is.

ONE LIST, NOT THREE. Three mechanisms enforce it, because the SDK gives three
hooks and no fewer -- docs/observability.md names them. All three read the sets
below, which is the property that matters: there is one answer to "is this
field allowed", not one per signal to keep in step.

BOTH SPELLINGS OF THE HTTP CONVENTIONS. The instrumentation pinned here emits
the old names -- `http.status_code` rather than `http.response.status_code` --
and the stable ones are named below anyway. Not because both are emitted, but
because deny-by-default fails safe on a rename: an upgrade that switched
spellings would find a list that knew only the old ones and drop everything,
and blank traces are a fault to diagnose at three in the morning rather than a
line to have written now. Nothing can leak either way, which is why the
`OTEL_SEMCONV_STABILITY_OPT_IN` question can wait for whoever wants the stable
names and is not settled here.

WHAT NO LIST CAN DO. The free text of a log message, and of an
`exception.message`, is whoever wrote it. This governs fields. And the resource
-- the service name, `OTEL_RESOURCE_ATTRIBUTES` -- is what whoever deploys this
typed on purpose, so it is not somewhere personal data arrives by accident.
"""

from typing import Any

from inventory_tng.options import setting

# The key and the span attribute that say "this was emitted while personal data
# was being recorded". One name in both, deliberately: whoever has to find that
# data again and delete it should be searching for one string rather than
# remembering which signal spells it which way.
MARKER = "personal_data"

# What stands in for text this cannot read. Said rather than emptied, so that a
# span whose status has been cleared is distinguishable from one that never
# carried a reason -- the first is somebody's business to go and read in the
# logs, the second is nothing.
REDACTED = "(withheld: docs/observability.md)"

# What `TELEMETRY_PERSONAL_DATA` may say. Named states rather than a boolean:
# `false` and `0` and `off` all mean the same thing to a reader and different
# things to different parsers, and this is the one setting in the application
# where being misread in the permissive direction is a disclosure.
STATES = ("redacted", "recorded")
RECORDING = "recorded"

# --------------------------------------------------------------------------
# Span and metric attributes
# --------------------------------------------------------------------------

# What a request is: which endpoint, how it was called, how it went. The
# templated route rather than the URL, which is the distinction the whole of
# this rests on -- `/api/items/{id}` says everything a dashboard needs and
# `/api/items/4172?volunteer=Ada` says who.
ALLOWED_ATTRIBUTES = frozenset(
    {
        MARKER,
        # HTTP, as the pinned instrumentation spells it.
        "http.method",
        "http.route",
        "http.status_code",
        "http.scheme",
        "http.flavor",
        "http.server_name",
        "net.host.name",
        "net.host.port",
        # HTTP, as the stable conventions spell the same things.
        "http.request.method",
        "http.response.status_code",
        "network.protocol.version",
        "url.scheme",
        "server.address",
        "server.port",
        # The database. `db.statement` is allowed and that is a considered
        # choice rather than an oversight: Django parameterises every query it
        # builds, so what the instrumentation records is the statement with
        # placeholders and never the values bound to them. Interpolating a
        # value into raw SQL would put it here, which is a reason not to write
        # that rather than a reason to lose every query from the traces.
        "db.system",
        "db.system.name",
        "db.name",
        "db.namespace",
        "db.user",
        "db.operation",
        "db.operation.name",
        "db.statement",
        "db.query.text",
        # What this application measures about itself. Every one of these is a
        # word chosen in the code -- `inventory.telemetry` lists the counters
        # and `inventory_tng.debugging` the ones it uses -- and never anything
        # a caller supplied.
        "outcome",
        "kind",
        "collection",
        "reason",
        "command",
    }
)

# NOT `net.peer.name` and `net.peer.port`, which look like the database's and
# are also the CALLER's. The old conventions use one pair of names for the far
# end of a connection whichever way it points, so on a client span they are the
# PostgreSQL host and on a server span they are the address and port of
# whoever made the request -- read straight out of REMOTE_HOST and REMOTE_PORT
# by the WSGI instrumentation. Allowing them for the database would have let
# every request keep its caller's port, and its hostname wherever a reverse
# lookup produced one. They are on the personal list below, which costs the
# database spans a hostname everybody already knows and keeps a name out of
# every server span. The stable conventions do not have this problem:
# `server.address` is the far end and `client.address` is the caller.

# Recorded only while the toggle is on, and enumerated rather than derived, so
# that nothing joins this group by accident. Two kinds of thing: the caller's
# address, which is personal data outright, and the concrete URL, which is not
# personal data in itself but is where an identifier ends up -- a path segment,
# a search term, a name in a query string.
PERSONAL_ATTRIBUTES = frozenset(
    {
        "net.peer.ip",
        "net.peer.name",
        "net.peer.port",
        "http.client_ip",
        "client.address",
        "client.port",
        "http.url",
        "http.target",
        "url.full",
        "url.path",
        "url.query",
        "http.user_agent",
        "user_agent.original",
        "enduser.id",
    }
)

# Allowed on a span's events rather than on the span. The message is free text
# and is the author's responsibility, as the module docstring says; the type and
# the stack are this application's own frames.
EVENT_ATTRIBUTES = frozenset({"exception.type", "exception.message", "exception.stacktrace", "exception.escaped"})

# --------------------------------------------------------------------------
# Log records
# --------------------------------------------------------------------------

# The field contract every record carries, whoever wrote it. `bound` is a list
# of the key names a record inherited rather than stated: names and no values,
# which is what the console reader uses to decide what to hide, so removing it
# would leak nothing and would make `pretty-logs` fall back to guessing.
ALLOWED_LOG_KEYS = frozenset(
    {
        MARKER,
        "timestamp",
        "level",
        "logger",
        "event",
        "exception",
        # `stack_info`, which is what `ProcessorFormatter` puts in the record.
        # `stack` was named here first and is not a key anything writes.
        "stack_info",
        "depth",
        "bound",
        "trace_id",
        "span_id",
        # Bound for the life of a request, per point 6 of the epic. `user` is a
        # SURROGATE identifier and nothing else -- a username or an email
        # address under this key is the leak this module exists to prevent, and
        # no list can catch it, so it is said here where whoever binds it will
        # be reading.
        "request_id",
        "method",
        "route",
        "status",
        "user",
        # Attached by libraries through `extra=`. `status_code` is Django's on
        # every 4xx and 5xx; the last two are `refusals` saying how much it
        # held back.
        "status_code",
        "duration",
        "alias",
        "suppressed",
        "suppressed_since",
        # What this application's own records say about what it did. Counts,
        # words chosen in the code, and surrogate identifiers -- never a name,
        # an address or anything a caller typed. `inventory.telemetry` is the
        # matching list for the metrics. `reason` is already above, admitted
        # for the same argument.
        "outcome",
        "kind",
        "collection",
        "command",
        "counted",
        # The message a browser's failure carried. Free text, and named here
        # for the same reason `reason` is: `inventory.views` bounds its length
        # and nothing can police what somebody wrote in a `throw`.
        "detail",
        "lines",
        "code",
        "item",
        "location",
        "volunteer",
        "transaction",
        # Free text, carrying exactly the responsibility the message carries:
        # declared because "why did that happen" is the field this application
        # will otherwise invent under six different names, and refused as a
        # place to put a value -- `reason="volunteer not found"` is a sentence,
        # `reason=volunteer.email` is the leak this module exists to prevent.
        "reason",
    }
)

# `sql` and `params` are here rather than above because of what Django's query
# logger puts in them -- docs/observability.md says what, and why the span
# attribute `db.statement` is a different thing and is allowed. They appear
# only with DJANGO_DEBUG on, so no deployment has ever written one; putting
# them here costs nothing and makes the claim true in every environment rather
# than only in the ones that happen not to exercise it.
#
# WHAT THIS DOES NOT DO, and the documents were wrong about it until a review
# said so: it does not keep the statement out of the record. Django writes the
# same query into the log MESSAGE with its parameters already interpolated, and
# a message is free text -- the boundary this module's docstring names. So
# these two keys being held back removes the duplicate copy and nothing else,
# and a developer reading queries does not need the toggle to do it.
PERSONAL_LOG_KEYS = frozenset({"sql", "params", "client_ip"})

# structlog's own two, which `ProcessorFormatter.remove_processors_meta` takes
# out a step after this runs. Named rather than matched on a leading
# underscore: a pattern would have exempted every key somebody happened to
# spell that way from the allowlist entirely.
META = frozenset({"_record", "_from_structlog"})


# The two lists again, joined once rather than per span and per record. A union
# of frozensets costs about 255 nanoseconds, `kept` runs once for a span and
# again for each of its events and links, and this is a constant of two module
# constants -- so it was 3 to 6 per cent of an eight-microsecond span, paid in
# exactly the mode that also emits the most.
ATTRIBUTES_ADMITTED = ALLOWED_ATTRIBUTES | PERSONAL_ATTRIBUTES
EVENT_ATTRIBUTES_ADMITTED = EVENT_ATTRIBUTES | PERSONAL_ATTRIBUTES
LOG_KEYS_ADMITTED = ALLOWED_LOG_KEYS | PERSONAL_LOG_KEYS

# gunicorn's access line, which is the one record in this system an allowlist
# cannot reach: it is a MESSAGE, assembled by gunicorn from a format string,
# and this module governs fields. So the format itself is the redaction.
#
# What gunicorn's own default would have written, and what each part costs:
# `%(h)s` is the caller's address; `%(r)s` is the request line, which carries
# the query string -- and this application has `/api/volunteers?search=Ada`, so
# that is a volunteer's name in every access record; `%(a)s` is the user agent,
# a device fingerprint in all but name. None of the three survives here.
#
# What is kept is what an access line is actually read for, and no more. The
# path is concrete rather than templated because gunicorn has never heard of
# Django's routes; it carries surrogate ids, which the epic settles as
# acceptable, and never a query.
ACCESS_LOG_FORMAT = "%(m)s %(U)s %(s)s %(b)s %(D)s"

# And what the toggle admits, since it would otherwise be the one place in the
# system that says "personal data" and means "except over there".
ACCESS_LOG_FORMAT_RECORDING = '%(h)s %(m)s %(U)s%(q)s %(s)s %(b)s %(D)s "%(a)s"'


def access_log_format(admitting: bool) -> str:
    """What gunicorn writes per request, which is a format rather than a list."""
    return ACCESS_LOG_FORMAT_RECORDING if admitting else ACCESS_LOG_FORMAT


def personal_data(requested: str) -> bool:
    """Whether personal data is being recorded, refusing anything unrecognised.

    Refused rather than read as off, which is the opposite of how a permissive
    setting is usually defaulted and is exactly the point -- decision 0021 says
    why. Stopping the process puts the mistake where somebody can see it.
    """
    state = requested.strip().lower()
    if state not in STATES:
        raise ValueError(f"TELEMETRY_PERSONAL_DATA={requested!r} is not one of: {', '.join(STATES)}.")
    return state == RECORDING


def recording(environment: dict[str, str] | None = None) -> bool:
    """The toggle, read from the environment the way every other setting is."""
    return personal_data(setting("TELEMETRY_PERSONAL_DATA", environment))


def announcement(admitting: bool) -> str:
    """What the process says at startup, and nothing at all when it is off.

    One of the four conditions decision 0021 puts on the toggle. Handed back
    rather than printed, for the reason `logs.configure` gives about its own.
    """
    if not admitting:
        return ""
    return (
        f"TELEMETRY_PERSONAL_DATA={RECORDING}: telemetry is carrying personal data, "
        f"marked `{MARKER}` wherever it lands.\n"
        f"Whatever collects it inherits that -- retention, access and deletion. "
        f"Unset it to stop, and purge what was written."
    )


def kept(attributes: Any, permitted: frozenset[str]) -> dict[str, Any]:
    """The attributes that may leave. One job: the caller chooses the list."""
    return {name: value for name, value in (attributes or {}).items() if name in permitted}


def redact_span(span: Any, admitting: bool) -> None:
    """Strip a finished span, in place, before anything can export it.

    In place because `ReadableSpan` is what a processor is handed and rebuilding
    one would mean naming every field it has -- so the day the SDK gains
    another, a rebuild drops it silently while this does not.

    THREE PLACES AN ATTRIBUTE HIDES, not one. The span's own; each event's,
    which is where `record_exception` puts a type and a message; and each
    link's, which nothing here creates today and which the exporter would carry
    if it did. A fourth is not an attribute at all: the status DESCRIPTION,
    which the SDK fills from an exception's own text on every error span. That
    text is written by whatever raised, and in this application that includes a
    database complaining about the value in a unique constraint -- a volunteer's
    address, quoted back. The type is kept; the message is not.
    """
    span._attributes = kept(span.attributes, ATTRIBUTES_ADMITTED if admitting else ALLOWED_ATTRIBUTES)
    if admitting:
        # On the span and not on its events or its links. Whoever has to find
        # this data again and delete it searches spans; a marker repeated on
        # every recorded exception would be a second copy of one fact.
        span._attributes[MARKER] = True

    # `span.events` and `span.links` each build a fresh tuple, and both are
    # empty on nearly every span -- so the properties are read through their
    # own lists, which this function is already doing for the attributes.
    if span._events:
        for event in span._events:
            event._attributes = kept(event.attributes, EVENT_ATTRIBUTES_ADMITTED if admitting else EVENT_ATTRIBUTES)
    if span._links:
        for link in span._links:
            link._attributes = kept(link.attributes, ATTRIBUTES_ADMITTED if admitting else ALLOWED_ATTRIBUTES)

    if not admitting and span.status is not None and span.status.description:
        span._status = type(span.status)(status_code=span.status.status_code, description=REDACTED)


def processor(admitting: bool) -> Any:
    """The `SpanProcessor` that does it, built rather than imported.

    The SDK is imported inside, for the reason `telemetry.start` gives: a
    checkout with no collector configured should not pay for importing it, and
    this module is reached from `settings.py` on every command there is.

    Registered before the exporting processor, so `on_end` has stripped the
    span by the time anything queues it.
    """
    from opentelemetry.sdk.trace import SpanProcessor

    class Redacting(SpanProcessor):
        def on_end(self, span: Any) -> None:
            redact_span(span, admitting)

    return Redacting()


def views(admitting: bool) -> list[Any]:
    """The same allowlist, as the metrics SDK's own way of saying it.

    A `View` over every instrument, keeping the named attributes and dropping
    the rest -- which is deny-by-default expressed in the API rather than
    bolted beside it, and is why metrics need no equivalent of the processor
    above. The marker rides on the resource instead: a metric's attributes are
    what its series is keyed by, and adding one to every series would double
    the cardinality of the whole export for a constant.
    """
    from opentelemetry.sdk.metrics.view import View

    return [View(instrument_name="*", attribute_keys=set(ATTRIBUTES_ADMITTED if admitting else ALLOWED_ATTRIBUTES))]


# Settled once by `logs.configure` and read by the processor below, which takes
# no configuration of its own -- the same arrangement, and for the same reason,
# as the console layout beside it.
_admitting = False


def settle(admitting: bool) -> None:
    """Tell the record processor what the process decided."""
    global _admitting
    _admitting = admitting


def redact_record(logger: Any, name: str, event: dict[str, Any]) -> dict[str, Any]:
    """structlog's last shared processor: drop every key not named above.

    Last, so that it sees the record as it will be drawn -- after the bound
    context has been merged, after a library's `extra=` has been added, and
    before either renderer. A `logging.Filter` would run earlier and on a
    `LogRecord`, which is not yet the thing that gets written.

    `_record` and `_from_structlog` are structlog's own machinery and are named
    one at a time rather than admitted as "anything underscored", which is what
    this did until a review pointed out that `log.info("x", _email=...)` was
    therefore exempt from the allowlist. The formatter removes both itself one
    step later.

    A key whose value is None is not written. `ExtraAdder`, given an allowlist,
    reads each name straight off the record rather than out of `extra=`, so
    every standard attribute this list happens to name -- `stack_info` is the
    one -- arrives as a null on every record from a library. A field nobody
    set is not a field, and a collector should not be parsing one.
    """
    permitted = LOG_KEYS_ADMITTED if _admitting else ALLOWED_LOG_KEYS
    kept_record = {key: held for key, held in event.items() if held is not None and (key in META or key in permitted)}
    if _admitting:
        kept_record[MARKER] = True
    return kept_record
