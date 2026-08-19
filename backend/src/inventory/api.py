"""API-wide response policy: how errors are rendered and how they are declared.

Both live here rather than beside the one feature that prompted them, because
settings names them for the whole API -- ``EXCEPTION_HANDLER`` and
``DEFAULT_SCHEMA_CLASS`` -- and a reader changing the 404 body should not have
to know that global policy sits in a module named after rate limiting.
"""

import math
from typing import Any

from drf_spectacular.openapi import AutoSchema
from drf_spectacular.utils import Direction
from rest_framework.exceptions import Throttled
from rest_framework.permissions import SAFE_METHODS
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from inventory.serializers import ThrottledSerializer
from inventory.throttling import AppendThrottle


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
