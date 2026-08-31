"""What stands in for a credential on the two endpoints that take none.

Volunteers append without signing in, which makes rate limiting a requirement
of that decision rather than a later hardening -- see
docs/decisions/0012-two-populations.md.

The throttles themselves. What a client receives when it hits one, and how
that is declared in the schema, is inventory/api.py -- both are API-wide
policy that settings names for every endpoint, not only these two. The numbers
are configuration, and live in .env.sample.
"""

from rest_framework.permissions import SAFE_METHODS
from rest_framework.request import Request
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from inventory.permissions import DEVICE_ENROLLED, presented_device


class CountingThrottle(UserRateThrottle):
    """What every limit here shares, so a subclass says only what it counts.

    Two throttles now want the same bucket and opposite halves of the traffic
    -- appends, and reads by somebody with no account -- and the bucket is the
    interesting part. Stated once so that a third is one method rather than a
    copy of ``get_ident`` and its whole argument.

    Derived from ``UserRateThrottle`` rather than ``AnonRateThrottle`` so the
    bucket is the client's address while nobody signs in and the account
    afterwards, instead of the limit vanishing the moment a session exists.
    """

    def counts(self, request: Request) -> bool:
        """Whether this request is one this limit is about."""
        raise NotImplementedError

    def allow_request(self, request: Request, view: APIView) -> bool:
        if not self.counts(request):
            return True
        return super().allow_request(request, view)

    def get_ident(self, request: Request) -> str:
        """The device, where one is carried, and DRF's reading of the address otherwise.

        WHAT IT IS FOR. A hub of volunteers behind one address shares one
        allowance -- `inventory-tng-81f7.1` records it, and decision 0023
        records the same shape for the reading of the header -- so one person's
        batch can exhaust what the room needs. A device identifier is per
        browser, so it separates them.

        AND ALMOST NOTHING REACHES IT YET, which is worth knowing before
        believing the paragraph above about a running deployment.
        `UserRateThrottle` consults this only for a caller it found no account
        for. Every endpoint but one asks for a session, so for those the bucket
        is still the account and this is a precondition rather than a repair --
        the way `TRUSTED_PROXIES` is one under decision 0023, and
        `inventory-tng-gnhl` is what makes it reachable.

        THE ONE is `ClientFailureView`, which names `AllowAny` and carries
        `ReportThrottle` below. It reaches this and gets no benefit from it,
        because `frontend/src/telemetry/report.ts` posts there with a bare
        `fetch` rather than through `api/client.ts`, so no device header
        arrives. `inventory-tng-wpf2` is the client half of that.

        It is not a stronger bucket, only a narrower one, and that is worth
        stating rather than leaving to be assumed: anybody may mint another
        device, so this raises the cost of taking the whole room's allowance
        rather than removing it. `.env.sample` bounds how fast that can be
        done, on `DEVICE_ENROLMENT_RATE`, and `Device.enrolled_from` is what
        makes a burst of it findable afterwards.

        A revoked device deliberately falls back to the address rather than
        keeping a bucket of its own. It is refused before it reaches a view
        anyway -- `DeviceNotRevoked` -- and letting a cut-off device name its
        own bucket would hand it a fresh allowance per identifier for the
        refusals themselves.
        """
        state, identifier = presented_device(request)
        if state == DEVICE_ENROLLED:
            return f"device:{identifier}"
        return str(super().get_ident(request))


class AppendThrottle(CountingThrottle):
    """A limit on appending, counted per client and never on a read.

    Reads are exempt because the endpoint that takes a volunteer's name is also
    the pick-list the client searches as somebody types, and a limit sized for
    submissions would be exhausted by typing. What counts THOSE is
    ``AnonymousReadThrottle`` below, which is this one's mirror image.
    """

    def counts(self, request: Request) -> bool:
        return request.method not in SAFE_METHODS


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


class AnonymousReadThrottle(CountingThrottle):
    """A limit on reading, counted only against a caller with no account.

    ``AppendThrottle``'s mirror image, and the pair is worth reading together:
    that one exempts every safe method because the pick-list is queried as
    somebody types, and this one counts nothing else.

    WHAT IT IS FOR, because sizing it as though it were the defence against
    copying the roster is the mistake `inventory-tng-81f7.1` was filed to
    prevent. Against copying, a limit does almost nothing: `display_name` is
    matched with `icontains`, so a single letter returns 54 of 86 names and six
    searches sweep the lot. Measured, not supposed.

    What it IS for is the membership question -- whether a given address
    belongs to a volunteer, asked one request at a time. .env.sample states
    both sides of that, because the person changing the number is reading
    there rather than here.

    ONLY A CALLER WITH NO ACCOUNT, so the number never has to be a compromise
    between a stranger and an administrator working all day.

    THE SIZE IS SET BY TYPING RATHER THAN BY THE ATTACK, which sounds
    backwards and is not: neither pick-list debounces, for the reason
    `ItemList.tsx` gives, so a keystroke is a request and one name is a dozen.
    A limit tight enough to trouble the oracle refuses somebody mid-name, and
    what it would buy against a roster sweep is nothing.
    """

    scope = "anonymous-read"

    def counts(self, request: Request) -> bool:
        if request.method not in SAFE_METHODS:
            return False
        return not (request.user and request.user.is_authenticated)


# Named for the reason APPEND_THROTTLES is: so the endpoints carrying it are
# provably carrying one posture rather than two spellings of it.
ANONYMOUS_READ_THROTTLES = [AnonymousReadThrottle]


class DeviceEnrolmentThrottle(UserRateThrottle):
    """What constrains minting, which `inventory_tng.devices` argues is where
    the guard is rather than in the signature.

    A bucket of its own is the part that is this module's: sharing the append
    allowance would let a room enrolling spend what a volunteer's batch needs,
    which is the argument `ReportThrottle` above makes about its own traffic.

    Not derived from `AppendThrottle`: that one exempts reads, and this
    endpoint has none to exempt.
    """

    scope = "device-enrolment"


DEVICE_ENROLMENT_THROTTLES = [DeviceEnrolmentThrottle]
