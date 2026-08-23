"""What a refused request is allowed to write.

Django logs a `django.security.<Exception>` record for every
`SuspiciousOperation` it raises -- a `Host` it does not answer to, a body too
large, too many form fields -- and it attaches the exception, so the record
carries a traceback. Giving those records somewhere to go was
inventory-tng-zya; it is also what turned them into a cost.

MEASURED, against a real gunicorn: one request carrying a `Host` nobody listed
produces about 1.4 KB at ERROR, nine frames of which are traceback. The ingress
is internet-facing, that path takes no credential, and nothing rate limits it --
so an ordinary host scanner at a few hundred requests a second writes hundreds
of megabytes an hour. It buries the errors somebody is looking for, and fills
the node's disk on the way, because nothing outlives a pod
(docs/deployment.md#reading-the-logs).

TWO SEPARATE COSTS, so two separate answers.

The traceback is the per-record half, and it is the cheapest thing in this
system to give up: the nine frames are the same three Django functions whatever
the request was, so they say nothing the message does not. What
inventory-tng-adj actually needed was the hostname that was refused, and that
is in the message.

The rate is the other half, and stripping the traceback does not touch it --
seven times less of an unbounded number is still unbounded. So a bounded number
of refusals is written per window and the rest are counted, with the count put
on the next one that is written. Counted rather than merely dropped: a limit
that hid how much it hid would turn a flood into a quiet log, which is the
failure the whole of this epic exists to end.

WHAT IS DELIBERATELY NOT DONE. The level stays at ERROR and the message is
never altered. Lowering a security log's level has no upside here -- the volume
was never in the level -- and an edited message is one nobody can grep for.

PER PROCESS. gunicorn runs several workers and each holds its own count, so
what a deployment actually writes is this rate times the worker count. Said in
.env.sample rather than corrected for: a shared counter needs somewhere shared
to keep it, and that is a round trip on the path of a request that is already
being refused.
"""

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime

# The logger family this bounds, and the only one. Every record under it is
# Django refusing a request, which is to say every one of them was caused by
# input nobody had to authenticate to send. An application logger is a
# different thing entirely -- somebody wrote that line on purpose -- and
# rationing it would hide the failures this arrangement exists to show.
#
# The family is what is bounded; the LOGGER is what is counted. Django puts
# more than one thing under here -- a refused Host, a CSRF failure, a session
# it could not decode -- and one window shared between them would let a host
# scanner spend the whole allowance and starve the two that mean somebody's
# session is broken. Each logger under the family gets a window of its own.
SECURITY = "django.security"

# What a deployment that says nothing gets. High enough that a genuinely
# misconfigured ingress -- the case decision 0021 was written about -- is
# unmissable in the first few seconds, low enough that a scanner costs a couple
# of kilobytes a minute rather than a disk. The count on the next record says
# how much larger the true number was, so the rate does not have to be sized
# for the flood in order to measure one.
DEFAULT_RATE = "10/min"

# The same shape as the throttle rates in .env.sample, deliberately: this
# repository already asks people to write `20/min` and a second spelling for
# the same idea is one more thing to look up. Not DRF's own parser, which is a
# method on a throttle instance and lives behind an import of the framework --
# and this is read while logging is being configured, which is part-way through
# the settings module, before there is a Django to import anything from.
PERIODS = {"s": 1, "min": 60, "hour": 3600, "day": 86400}


def rate(requested: str, setting: str = "DJANGO_SECURITY_LOG_RATE") -> tuple[int, int]:
    """`10/min`, as a count and a window in seconds, refusing anything else.

    Refused rather than defaulted, like every other setting here: being given a
    rate other than the one you asked for, and not being told, is how a limit
    turns out to have been wrong during the incident it was set for.

    `setting` is only which name the complaint uses. A second setting spelled
    the same way -- `inventory_tng.debugging`'s -- reads it through here rather
    than growing a parser of its own, and somebody reading the refusal has to
    be told which of the two they got wrong.
    """
    written, separator, period = requested.strip().partition("/")
    named = period.strip().lower()
    if not separator or named not in PERIODS:
        raise ValueError(
            f"{setting}={requested!r} is not `<count>/<period>`. The period is one of: {', '.join(PERIODS)}."
        )
    try:
        count = int(written.strip())
    except ValueError:
        raise ValueError(f"{setting}={requested!r} does not begin with a whole number.") from None
    if count < 1:
        # Nought is refused rather than read as "none at all". Somebody
        # quietening a noisy deployment would reach for it, and the quiet that
        # follows is indistinguishable from nothing having gone wrong.
        #
        # It is not the only way to that quiet, and this does not pretend
        # otherwise: `DJANGO_LOG_LEVELS=django.security=CRITICAL` silences the
        # family outright, and that knob is Django's rather than this
        # module's. What is refused here is a rate of nought, which is the one
        # spelling somebody would expect to mean "less, not none".
        raise ValueError(
            f"{setting}={requested!r} would allow none at all. Set a rate you can live with, "
            "or, where it is a logger this rations, raise that logger's level instead."
        )
    return count, PERIODS[named]


@dataclass
class Window:
    """What one logger has written and held back in the window it is in."""

    opened: float
    written: int = 0
    held: int = 0
    since: str = ""


class Bounded(logging.Filter):
    """Strip the traceback from a refusal, and write at most so many a window.

    A handler filter rather than a logger one, and that is not a preference:
    Python consults a logger's filters only on the logger the call was made on,
    and Django logs to `django.security.DisallowedHost` -- a child. Filters
    named on the parent would never run. The handler sees every record, which
    is why this checks the name itself.
    """

    def __init__(self, count: int, window: int, family: str = SECURITY) -> None:
        super().__init__()
        self.count = count
        self.window = window
        self.family = family
        # Built once, because this filter is on the HANDLER and therefore sees
        # every record in the process rather than every refusal. What it costs
        # a record that is none of its business is one comparison, and it was
        # a string being formatted first.
        self.beneath = f"{family}."
        # Two threads per gunicorn worker is not the arrangement today, but a
        # counter reached from a handler is reached from whatever thread logged,
        # and a lost increment here is a record written that should not have
        # been -- which is the failure mode this class exists to prevent.
        self._lock = threading.Lock()
        # One `Window` per logger under the family. A dict rather than a fixed
        # set of keys because Django names the exception -- `DisallowedHost`,
        # `SuspiciousFileOperation` -- so what turns up is decided by what
        # arrives. It cannot grow without bound: the names are Django's own
        # `SuspiciousOperation` subclasses, and there are a dozen of them.
        self._windows: dict[str, Window] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        if not (record.name == self.family or record.name.startswith(self.beneath)):
            return True

        # The record is mutated rather than copied. There is one handler, it
        # has not emitted yet -- a handler filters on the way in -- and a copy
        # would mean a second LogRecord per refused request, which is a cost of
        # exactly the kind being removed.
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None

        now = time.monotonic()
        with self._lock:
            counting = self._windows.setdefault(record.name, Window(opened=now))
            if now - counting.opened >= self.window:
                counting.opened = now
                counting.written = 0
            if counting.written >= self.count:
                if not counting.held:
                    counting.since = datetime.now().astimezone().isoformat(timespec="milliseconds")
                counting.held += 1
                return False
            counting.written += 1
            if counting.held:
                # An instant rather than a duration, because the record
                # carrying the count can be long after the flood that produced
                # it: nothing is written when nothing arrives, so the summary
                # waits for the next refusal however far off that is. Saying
                # when the counting started is true whenever it is read;
                # "in the last minute" would not be.
                record.suppressed = counting.held
                record.suppressed_since = counting.since
                counting.held = 0
                counting.since = ""
        return True
