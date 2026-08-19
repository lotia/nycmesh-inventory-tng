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

from inventory.permissions import administrators_only, open_to_anybody
from inventory.serializers import DetailSerializer, ThrottledSerializer
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


# What marks an operation as one decision 0012 reserves for somebody signed
# in. A status code cannot carry that on its own: every endpoint but the index,
# the health check and /api/me refuses an anonymous caller with a 403 too, so
# 403 says "not you" without saying who it would have been. This sentence says
# it, and it is appended by the schema rather than typed on each view, so it
# cannot be attached to an operation that is not actually reserved.
ADMINISTRATORS_ONLY = "Requires an administrator. See docs/decisions/0012-two-populations.md for who that is and why."


class PolicyAwareAutoSchema(AutoSchema):
    """Documents the refusals a view's own policy can produce.

    Three of them: 429 where a throttle is attached, 403 where anything at all
    guards the endpoint, and 404 where the path addresses one row. The
    alternative is naming each response on each view, which is the same promise
    written in as many places as there are endpoints. Here the schema follows
    the view -- attach a throttle, a permission or a lookup and the response is
    documented; take it away and the promise goes with it.

    Which operations an administrator has to make is a separate question from
    which can answer 403, and it is answered in the description; see
    ADMINISTRATORS_ONLY above.
    """

    def get_description(self) -> str:
        description = super().get_description()
        if not self._is_administrators_only():
            return description
        return f"{description}\n\n{ADMINISTRATORS_ONLY}".strip()

    def _get_response_bodies(self, direction: Direction = "response") -> dict[str, Any]:
        responses = super()._get_response_bodies(direction)
        if direction == "response":
            if self._is_throttled_write():
                responses["429"] = self._get_response_for_code(ThrottledSerializer, "429", direction=direction)
            if self._is_guarded():
                responses.setdefault("403", self._get_response_for_code(DetailSerializer, "403", direction=direction))
            if self._addresses_one_row():
                responses.setdefault("404", self._get_response_for_code(DetailSerializer, "404", direction=direction))
        return responses

    def _is_throttled_write(self) -> bool:
        if self.method in SAFE_METHODS:
            return False
        return any(isinstance(throttle, AppendThrottle) for throttle in self.view.get_throttles())

    def _is_guarded(self) -> bool:
        """Whether anything at all can refuse this operation.

        Not "is it administrators only": the project's default permission is
        IsAuthenticated, so a read refuses an anonymous caller just as a write
        refuses a volunteer. Both are a 403 and a client has to handle it.
        """
        return not open_to_anybody(self.view)

    def _is_administrators_only(self) -> bool:
        return administrators_only(self.view, self.method)

    def _addresses_one_row(self) -> bool:
        """A path with a parameter in it can be asked for a row that is not there.

        True of the detail endpoints and of nothing else: a collection answers
        with an empty page rather than a 404.
        """
        return "{" in self.path
