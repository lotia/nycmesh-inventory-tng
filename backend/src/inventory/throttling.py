"""What stands in for a credential on the two endpoints that take none.

Volunteers append without signing in, which makes rate limiting a requirement
of that decision rather than a later hardening -- see
docs/decisions/0012-two-populations.md.

Everything the limit involves is here: the two throttles, the body a client
gets when it hits one, and the schema entry that documents that body. Stated
once, so an endpoint cannot be limited without saying so in the schema, and the
schema cannot promise a shape the handler does not send. The numbers themselves
are configuration, and live in .env.sample.
"""

import math
from typing import Any

from drf_spectacular.openapi import AutoSchema
from drf_spectacular.utils import Direction
from rest_framework.exceptions import Throttled
from rest_framework.permissions import SAFE_METHODS
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView
from rest_framework.views import exception_handler as drf_exception_handler

from inventory.serializers import ThrottledSerializer


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


def exception_handler(exc: Exception, context: Any) -> Response | None:
    """DRF's handler, with a throttled response a client can act on.

    DRF's own body is a sentence with the number of seconds written into the
    prose. This replaces it with the number in a field of its own, next to a
    constant code, so a client can count down and retry without parsing
    English.
    """
    response = drf_exception_handler(exc, context)
    if isinstance(exc, Throttled) and response is not None:
        # Throttled carries `wait`, which is None when a throttle refuses
        # without saying for how long, and which the DRF stubs omit.
        seconds = math.ceil(getattr(exc, "wait", None) or 0)
        response.data = {
            "detail": "Too many submissions from here. Nothing was saved; send this again shortly.",
            "code": "throttled",
            "retry_after_seconds": seconds,
        }
        # DRF sets this header too, but truncates where this rounds up. Setting
        # it again keeps the header and the field the same number, so a client
        # obeying either one waits long enough.
        response["Retry-After"] = str(seconds)
    return response


class ThrottleAwareAutoSchema(AutoSchema):
    """Documents 429 on every operation that can actually answer with one.

    The alternative is naming the response on each throttled view, which is the
    same promise written in as many places as there are endpoints. Here the
    schema follows the throttle: attach one and the response is documented,
    remove it and the promise goes away with it.
    """

    def _get_response_bodies(self, direction: Direction = "response") -> dict[str, Any]:
        responses = super()._get_response_bodies(direction)
        if direction == "response" and self._is_throttled_write():
            responses["429"] = self._get_response_for_code(ThrottledSerializer, "429", direction=direction)
        return responses

    def _is_throttled_write(self) -> bool:
        if self.method in SAFE_METHODS:
            return False
        return any(isinstance(throttle, AppendThrottle) for throttle in self.view.get_throttles())
