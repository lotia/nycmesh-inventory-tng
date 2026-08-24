"""What stands in for a credential on the two endpoints that take none.

Volunteers append without signing in, which makes rate limiting a requirement
of that decision rather than a later hardening -- see
docs/decisions/0012-two-populations.md.

The throttles themselves. What a client receives when it hits one, and how
that is declared in the schema, is inventory/api.py -- both are API-wide
policy that settings names for every endpoint, not only these two. The numbers
are configuration, and live in .env.sample.
"""

from django.conf import settings
from rest_framework.permissions import SAFE_METHODS
from rest_framework.request import Request
from rest_framework.throttling import AnonRateThrottle, SimpleRateThrottle, UserRateThrottle
from rest_framework.views import APIView


class CountingThrottle(SimpleRateThrottle):
    """The half every throttle here shares: which methods it counts.

    A mixin rather than a condition written into each ``allow_request``,
    because the predicate is asked TWICE and the two askers are far apart. The
    request being served asks it, and so does ``PolicyAwareAutoSchema``, which
    documents the 429 exactly where one can happen. Two spellings of one
    predicate is how an operation comes to advertise a refusal it cannot make,
    or make one it never advertised -- and with a read limit beside a write
    limit, "throttled means a write" stopped being true.

    Stated once here so a fourth throttle is one class rather than three
    coordinated edits: a subclass says what it counts and inherits the rest.

    Derived from ``SimpleRateThrottle`` rather than left a bare mixin, which is
    what both of DRF's below already derive from. Nothing is inherited from it
    that a bare mixin would not have got through the MRO anyway -- what it buys
    is that ``super()`` here has a static type, so the rate limiting can be
    deferred to by name instead of through an ``Any``. Never instantiated
    itself, so carrying no scope of its own costs nothing.
    """

    #: Whether an unsafe method is counted, and whether a safe one is. The two
    #: throttles below are opposites, which is the reason this is two flags
    #: rather than a single "reads too".
    counts_writes = False
    counts_reads = False

    def counts(self, method: str) -> bool:
        """Whether this throttle can refuse a request made this way."""
        return self.counts_reads if method in SAFE_METHODS else self.counts_writes

    def allow_request(self, request: Request, view: APIView) -> bool:
        if not self.counts(request.method or ""):
            return True
        return super().allow_request(request, view)


class AppendThrottle(CountingThrottle, UserRateThrottle):
    """A limit on appending, counted per client and never on a read.

    Reads are exempt because the endpoint that takes a volunteer's name is also
    the pick-list the client searches as somebody types, and a limit sized for
    submissions would be exhausted by typing.

    Derived from ``UserRateThrottle`` rather than ``AnonRateThrottle`` so the
    bucket is the client's address while nobody signs in and the account
    afterwards, instead of the limit vanishing the moment a session exists.
    """

    counts_writes = True


class AppendBurstThrottle(AppendThrottle):
    """Stops a loop. Scope name is the key in DEFAULT_THROTTLE_RATES."""

    scope = "append-burst"


class AppendSustainedThrottle(AppendThrottle):
    """Stops a loop that paces itself under the burst limit."""

    scope = "append-sustained"


# What a credential-free write endpoint takes. Named once so the two endpoints
# provably share one posture, and so a third would be added by copying a name
# rather than by remembering a pair.
APPEND_THROTTLES = [AppendBurstThrottle, AppendSustainedThrottle]


class ReportThrottle(AppendThrottle):
    """A budget of its own for the failures a browser reports.

    NOT `APPEND_THROTTLES`, and the difference is the traffic rather than the
    posture. DRF keys a bucket on the scope and the client, not on the view, so
    sharing a scope means sharing a budget -- and the two endpoints that shared
    one were both a volunteer deliberately writing something. This one the app
    posts to BY ITSELF, once per failing call: a backend answering 5xx while
    somebody types in a search box spends the whole allowance on reports, and
    the volunteer's actual batch is refused 429 the moment the server comes
    back. Losing a report is a cost worth paying; losing the batch is the thing
    this system exists not to do.
    """

    scope = "report"


REPORT_THROTTLES = [ReportThrottle]


class EnrolmentThrottle(AppendThrottle):
    """A budget of its own for devices asking to be enrolled. PROVISIONAL.

    THE SAME NUMBER AS THE BURST LIMIT AND A DIFFERENT BUCKET, which is the
    whole of the fix and is `ReportThrottle`'s argument arriving one endpoint
    along: a scope is a budget, so two endpoints naming one scope spend one
    allowance. `.env.sample` records that volunteers at a hub share an address,
    and a wrong code invites retrying in a way a submission does not -- so ten
    people mistyping it twice each emptied what the next volunteer's
    five-hundred-line batch needed. Losing an enrolment attempt costs a tap;
    losing the batch is the thing this system exists not to do.

    The rate is the burst rate's own value, set in settings beside it: what had
    to be separate is the bucket, and inventing a second number to configure
    would be a knob nobody could size. `inventory-tng-81f7.4` removes this with
    the posture that needs it.
    """

    scope = "enrolment"


#: What a credential-free enrolment endpoint takes; see `DeviceEnrolmentView`.
ENROLMENT_THROTTLES = [EnrolmentThrottle]


class AnonymousReadThrottle(CountingThrottle, AnonRateThrottle):
    """A limit on READING, counted only against a caller with no session.

    PROVISIONAL, and off unless ``ANONYMOUS_READ_RATE`` names a rate --
    ``inventory_tng.postures`` is what the five demo settings are and
    ``inventory-tng-81f7.4`` is what removes them. Empty is today's behaviour,
    which is no read limit anywhere.

    THE EXACT OPPOSITE OF ``AppendThrottle`` ABOVE, and the pair is worth
    reading together: that one exempts every safe method because the pick-list
    is queried as somebody types, and this one counts nothing else.

    WHAT IT IS FOR, because sizing it as though it were the defence against
    enumeration is the mistake ``inventory-tng-81f7.1`` was filed to prevent.
    Against copying the roster a limit does almost nothing -- that is
    forty-three requests, measured. Against asking whether a given address
    belongs to a volunteer, one request per address, it turns seconds into
    hours of very obvious traffic. So it is attached to the pick-list, which is
    where that question can be asked, and not spread over every read as though
    the roster were what it protected.

    ``AnonRateThrottle`` rather than ``UserRateThrottle``: its cache key is
    None for anybody signed in, so an administrator reading the same endpoint
    is not counted at all.
    """

    scope = "anonymous-read"
    # Reads, and nothing else -- the exact opposite of the throttles above.
    # The METHOD and not the rate: whether a limit is configured is a
    # deployment's business and changes nothing about which operations may
    # answer 429, and the schema is generated once and committed, so a promise
    # that varied with an environment variable could not be checked.
    counts_reads = True

    def get_rate(self) -> str | None:
        """The setting itself, rather than a key in ``DEFAULT_THROTTLE_RATES``.

        The other throttles here take their numbers from that map, which DRF
        reads once per process out of ``REST_FRAMEWORK``. This one is read from
        the setting on each request, so that ``ANONYMOUS_READ_RATE`` is the one
        name the value lives under -- which is what
        ``inventory-tng-81f7.4``'s grep depends on -- and so a test can state a
        rate without rebuilding the whole ``REST_FRAMEWORK`` block.

        None where nothing is set, which is the value DRF reads as "no limit".
        """
        return settings.ANONYMOUS_READ_RATE or None


# What the pick-list carries beside its append limits. Named for the reason
# APPEND_THROTTLES is named: so the two endpoints that carry it are provably
# carrying one posture rather than two spellings of it.
ANONYMOUS_READ_THROTTLES = [AnonymousReadThrottle]


class LabelSheetThrottle(AnonymousReadThrottle):
    """A budget of its own for printing a sheet. PROVISIONAL.

    The third time this argument is made in this file and the same one both
    previous times: a scope is a budget, so an endpoint that shares one spends
    another's. Here the two are a volunteer TYPING in the pick-list, which is
    a request per keystroke, and a print run, which is forty QR encodes and is
    the one request in this API its own docstring calls expensive. Sharing
    `anonymous-read` meant act two's typing could empty what a print run
    needed, and it is the print run that costs somebody a walk to the printer.

    The same number, out of a different bucket: `get_rate` is inherited, so
    both are `ANONYMOUS_READ_RATE` and there is still one figure to size. What
    a scope buys here is only the separation.
    """

    scope = "label-sheet"


#: What the one expensive read carries; see `LabelSheetView`.
LABEL_SHEET_THROTTLES = [LabelSheetThrottle]
