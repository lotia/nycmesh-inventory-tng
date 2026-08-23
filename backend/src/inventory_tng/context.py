"""What every record written during a request carries without being asked.

Decision 0021's "context is bound, not passed": a developer writing
`log.info("batch recorded")` with no keys at all still gets the request it
happened in. This is the binding half. `logs.context` is the half that merges
what is bound onto each record, and `redaction.ALLOWED_LOG_KEYS` is what
declares the names -- three files, one contract, and this is the only one that
knows a request exists.

WHY A REQUEST ID WHEN THERE IS A TRACE ID. Because `trace_id` is empty unless
the trace was sampled, which at the shipped ratio most are not. A request id
is on every record of every request, so the ordinary "show me everything that
happened while this went wrong" works without anybody having sampled the right
one. Generated here rather than read from a header: an id a caller supplies is
an id a caller can repeat, and correlating across services is what `trace_id`
is for.

WHAT IS BOUND WHEN, because it cannot all be bound at once. The id and the
method are known as the request arrives. The route is not -- it is what URL
resolution produces -- and neither is the user, who is whoever the
authentication middleware settled on. So those two are bound at `process_view`,
which is after both and still before the view runs.

THE RECORD THIS WRITES IS NOT THE ACCESS LOG, and the difference is the whole
reason it exists. gunicorn's access line is written after Django has finished,
outside the instrumentation, so it can carry neither a trace id nor a route --
measured, and on inventory-tng-nb8.9. This one is written inside the
middleware chain, beneath OpenTelemetry's own middleware, so it carries both:
it is what makes a request findable from its trace, and the trace findable
from it.

ONE THING IT CANNOT REACH, said here so nobody goes looking for it. Django
writes its own record for a 404 or a 500 from `BaseHandler.get_response`,
which is after the middleware chain has unwound -- so those records have
neither this request's id nor its trace id, and no middleware can give them
one. What they say is on the record this writes instead, which carries the
status, the route and the duration for every request whatever it ended as. A
WSGI wrapper could bind outside the chain and cover them; it is not used here
because Django's test client does not go through one, so the arrangement under
test would not be the arrangement that runs -- which is exactly the defect
inventory-tng-iqff.1 was.
"""

import time
import uuid
from collections.abc import Callable
from typing import Any

import structlog
from django.http import HttpRequest, HttpResponse

log = structlog.get_logger("inventory.request")

# What a request is called before anything has resolved it. A record written
# by a middleware that runs before `process_view` -- a CSRF refusal, say --
# still gets a route rather than nothing, and it is not a path, so it cannot
# become a series of its own in a metric or a search.
UNRESOLVED = "unresolved"


class RequestContext:
    """Bind the request onto every record it produces, and say how it ended.

    Placed at the top of this project's own middleware, so as much of Django's
    stack as possible runs inside it and a refusal from one of them is still a
    record with a request id on it.

    Cleared in a `finally`. A worker thread serves one request after another
    and structlog's context variables are the thread's, so a request that left
    its own id behind would label somebody else's records with it.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=uuid.uuid4().hex,
            method=request.method,
            route=UNRESOLVED,
        )
        started = time.perf_counter()
        try:
            response = self.get_response(request)
            log.info(
                "request finished",
                status=response.status_code,
                duration=round((time.perf_counter() - started) * 1000, 1),
            )
            return response
        finally:
            structlog.contextvars.clear_contextvars()

    @staticmethod
    def process_view(request: HttpRequest, *unused: Any) -> None:
        """The route and the caller, once Django knows both.

        A SURROGATE for the caller and never a name: the primary key of the
        account, which means nothing outside this database. Anonymous requests
        bind nothing at all rather than a shared word -- a volunteer has no
        identifier here, deliberately, and minting one is the thing
        `inventory-tng-jro` would do and `redaction` already calls personal
        data.
        """
        # Both are settled by the time any `process_view` hook runs, so
        # neither is guarded: Django assigns `resolver_match` immediately
        # before calling these, and the authentication middleware assigned
        # `user` on the way in -- this one is outermost, so every middleware
        # in the list has already had its turn.
        resolved: Any = request.resolver_match
        structlog.contextvars.bind_contextvars(route=resolved.route)
        if request.user.is_authenticated:
            structlog.contextvars.bind_contextvars(user=request.user.pk)
