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


class AppendThrottle(UserRateThrottle):
    """A limit on appending, counted per client and never on a read.

    Reads are exempt because the endpoint that takes a volunteer's name is also
    the pick-list the client searches as somebody types, and a limit sized for
    submissions would be exhausted by typing.

    Derived from ``UserRateThrottle`` rather than ``AnonRateThrottle`` so the
    bucket is the client's address while nobody signs in and the account
    afterwards, instead of the limit vanishing the moment a session exists.
    """

    def allow_request(self, request: Request, view: APIView) -> bool:
        if request.method in SAFE_METHODS:
            return True
        return super().allow_request(request, view)


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
