"""API-wide response policy: how errors are rendered and how they are declared.

Both live here rather than beside the one feature that prompted them, because
settings names them for the whole API -- ``EXCEPTION_HANDLER`` and
``DEFAULT_SCHEMA_CLASS`` -- and a reader changing the 404 body should not have
to know that global policy sits in a module named after rate limiting.
"""

import math
from typing import Any

from drf_spectacular.openapi import AutoSchema
from drf_spectacular.plumbing import ResolvedComponent
from drf_spectacular.utils import Direction
from rest_framework.exceptions import NotAuthenticated, PermissionDenied, Throttled
from rest_framework.permissions import SAFE_METHODS
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from inventory.permissions import RecentlyAuthenticated, administrators_only, open_to_anybody
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
    if isinstance(exc, PermissionDenied | NotAuthenticated) and response is not None:
        # Two refusals share a status code and mean opposite things to a
        # client: "this is not yours" is a control to hide, and "sign in again"
        # is a prompt to show somebody who is entitled to the thing. DRF's body
        # carries only the sentence, so the code is added here -- the same
        # move, and the same reason, as the throttled body above.
        #
        # NotAuthenticated as well as PermissionDenied, because it is the same
        # 403 to a client: session authentication offers no challenge header,
        # so DRF renders "you did not say who you are" with this status too. A
        # code on some 403s and not others is a field a client cannot branch on.
        response.data = {
            **response.data,
            "code": "reauthentication_required" if _needs_a_second_look(exc) else "forbidden",
        }
    return response


def _needs_a_second_look(exc: Exception) -> bool:
    """Whether this refusal is RecentlyAuthenticated's rather than anybody else's.

    Read off the code DRF carries: it passes a permission's ``code`` attribute
    into the exception it raises, so the class names itself without anybody
    comparing a sentence that a translation could change.
    """
    return getattr(getattr(exc, "detail", None), "code", None) == RecentlyAuthenticated.code


# What marks an operation as one decision 0012 reserves for somebody signed
# in. A status code cannot carry that on its own: every endpoint but the index,
# the health check and /api/me refuses an anonymous caller with a 403 too, so
# 403 says "not you" without saying who it would have been. This sentence says
# it, and it is appended by the schema rather than typed on each view, so it
# cannot be attached to an operation that is not actually reserved.
ADMINISTRATORS_ONLY = "Requires an administrator. See docs/decisions/0012-two-populations.md for who that is and why."

# The name the field map is published under, referenced by every write.
VALIDATION_ERROR = "ValidationError"

# What DRF already answers with when a serializer refuses a body: the name of
# every field it complained about, mapped to the complaints about that field,
# with `non_field_errors` for complaints about the body as a whole. This
# describes that; it does not change it.
#
# A map whose keys are not known in advance is not expressible as a
# Serializer's own fields, so this is the one error shape in the API written as
# a schema rather than declared as a member of the family in serializers.py.
#
# Written out rather than assembled from drf-spectacular's build_object_type
# and friends: three keys is smaller and reads as the YAML it becomes, and the
# builders would need unwrapping anyway because build_basic_type is typed as
# returning None for a type it cannot resolve. There is no OpenApiResponse
# around it either, for the same reason the siblings do without one -- it
# carries a description for the *response*, and the prose belongs on the
# component, where `Detail` and `Throttled` keep theirs.
VALIDATION_ERROR_SCHEMA = {
    "type": "object",
    "description": (
        "A request refused for what it said -- a body a serializer would not take, or a query "
        "parameter a filter could not parse.\n\n"
        "Each key is a field of the submission and each value is every complaint about that "
        "field; `non_field_errors` carries the complaints about the request as a whole.\n\n"
        "A few operations refuse in a shape of their own, where a field name cannot carry what "
        "they have to say; each of those says so in its own description."
    ),
    "additionalProperties": {"type": "array", "items": {"type": "string"}},
}


class PolicyAwareAutoSchema(AutoSchema):
    """Documents the refusals a view's own policy can produce.

    Four of them: 429 where a throttle is attached, 403 where anything at all
    guards the endpoint, 404 where the path addresses one row, and 400 where
    there is a body to be refused. The alternative is naming each response on
    each view, which is the same promise written in as many places as there are
    endpoints. Here the schema follows the view -- attach a throttle, a
    permission, a lookup or a request body and the response is documented; take
    it away and the promise goes with it.

    Which operations an administrator has to make is a separate question from
    which can answer 403, and it is answered in the description; see
    ADMINISTRATORS_ONLY above.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._request_bodies: dict[tuple[str, str, str], Any] = {}

    def get_description(self) -> str:
        description = super().get_description()
        if not self._is_administrators_only():
            return description
        return f"{description}\n\n{ADMINISTRATORS_ONLY}".strip()

    def _get_request_body(self, direction: Direction = "request") -> Any:
        """The request body, built once however many times it is asked for.

        drf-spectacular builds it for the operation and
        ``_carries_a_body_to_refuse`` below asks for it again, and building it
        resolves every nested serializer and emits every warning that resolving
        them produces -- so without this each write's body is assembled twice
        and each of its warnings is reported twice. Keyed by the operation
        rather than kept in a bare attribute, because a schema instance is only
        promised to a view, not to one path and method of it.
        """
        key = (self.path, self.method, direction)
        if key not in self._request_bodies:
            self._request_bodies[key] = super()._get_request_body(direction)
        return self._request_bodies[key]

    def _get_response_bodies(self, direction: Direction = "response") -> dict[str, Any]:
        responses = super()._get_response_bodies(direction)
        if direction == "response":
            if self._is_throttled_write():
                responses["429"] = self._get_response_for_code(ThrottledSerializer, "429", direction=direction)
            if self._is_guarded():
                responses.setdefault("403", self._get_response_for_code(DetailSerializer, "403", direction=direction))
            if self._addresses_one_row():
                responses.setdefault("404", self._get_response_for_code(DetailSerializer, "404", direction=direction))
            if self._can_refuse_what_it_was_given():
                schema = self._validation_error_component().ref
                responses.setdefault("400", self._get_response_for_code(schema, "400", direction=direction))
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

    def _can_refuse_what_it_was_given(self) -> bool:
        """Whether anything about this operation's input can be refused as a 400.

        Two ways in. A request body, asked of the schema's own build rather
        than of the method alone, so a write that stops taking one stops
        advertising the refusal. And a filter: `django_filters`' DRF backend
        is configured to raise, so `?category=abc` on the item list is refused
        with exactly the field map this component describes -- on the endpoint
        a phone hits most, which is precisely where a generated client should
        have a type for it.
        """
        return bool(self._get_request_body()) or self._is_filtered()

    def _is_filtered(self) -> bool:
        """Whether django-filter has anything to parse for this view.

        Either spelling counts: a view declares a `filterset_class` or names
        `filterset_fields` and lets the backend build one, and both end in the
        same refusal for a value that will not parse.
        """
        return any(getattr(self.view, named, None) for named in ("filterset_class", "filterset_fields"))

    def _validation_error_component(self) -> ResolvedComponent:
        """The field map, registered once and referenced by every write.

        A raw schema handed to `_get_response_for_code` is used as it stands,
        which would inline this object into every write operation and give a
        generated client one anonymous type per endpoint for a single body.
        Registering it costs one call and buys the `$ref` the serializer-backed
        siblings get for free.
        """
        component = ResolvedComponent(
            name=VALIDATION_ERROR,
            type=ResolvedComponent.SCHEMA,
            schema=VALIDATION_ERROR_SCHEMA,
            object=VALIDATION_ERROR,
        )
        self.registry.register_on_missing(component)
        return component

    def _addresses_one_row(self) -> bool:
        """A path with a parameter in it can be asked for a row that is not there.

        True of the detail endpoints and of nothing else: a collection answers
        with an empty page rather than a 404.
        """
        return "{" in self.path
