"""Who may ask this server to record a request in full, and how much it may cost.

A volunteer meeting a failure is the person whose request needs recording, and
volunteers do not sign in -- decision 0012. So the flag that says "record this
one properly" cannot be an administrator's, or it would only ever debug the
sessions that were never the problem.

WHAT IT CANNOT BE is the W3C sampled bit on its own, which is a header anybody
can set on a request that needs no credential. Decision 0021 argues the refusal
and docs/observability.md states it; what follows from it is everything below.

SO IT IS A SIGNED, EXPIRING TOKEN. An administrator mints one, hands it to the
volunteer, and it stops working by itself. `TimestampSigner` gives all of that
for nothing: the signature is the authority, the timestamp is the expiry, and
rotating `DJANGO_SECRET_KEY` invalidates every signature at once -- which is as
much revocation as something with no store behind it can offer.

AND IT IS RATE LIMITED. `inventory/throttling.py` is the same idea for the two
endpoints that take no credential; this is a third write that takes none, and
it is metered the same way. Each token carries an id of its own, so the limit
is that token's rather than everybody's.

WHAT IT DOES NOT DO is admit personal data. `TELEMETRY_PERSONAL_DATA` stays a
separate, process-wide setting and this carries no part of it -- a decision
taken rather than a gap, and argued in decision 0021 under the amendment that
introduced that setting.

BEFORE DJANGO, deliberately. The check is a WSGI wrapper rather than a Django
middleware, because OpenTelemetry's instrumentation puts its own middleware at
the front of the chain: anything registered as a middleware runs after the
request's span has already been created and sampled, which is too late to
decide anything about it.
"""

import functools
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from opentelemetry import metrics

from inventory_tng import refusals

# What the SPA sends, and what it looks like once WSGI has mangled it.
HEADER = "X-Debug-Trace"
ENVIRON = "HTTP_X_DEBUG_TRACE"

# Separates these signatures from every other thing this application signs with
# the same key, so a token minted here cannot be presented anywhere else.
SALT = "inventory_tng.debugging"

# Long enough to reproduce something with somebody over a call, short enough
# that a link pasted into a channel is not a standing authority. The volunteer
# does not have to be quick and the token does not have to be revoked by hand.
DEFAULT_LIFETIME_SECONDS = 3600

# What one token may cost. Not a limit on the volunteer -- it is a limit on how
# much work a leaked link can make this server do, and on how much a collector
# can be made to hold. A person reproducing a fault makes tens of requests.
DEFAULT_RATE = "60/min"

# Counted in Django's cache, which is per process by default -- the same caveat
# the append throttles carry, and docs/deployment.md says it once for both.
CACHE_PREFIX = "debug-trace:"

# Whether the request being served is one somebody proved they may record. Read
# by the sampler, which has no other way to be told: a sampler is called with a
# trace id and a span name and nothing about the request behind them.
_authorised: ContextVar[bool] = ContextVar("inventory_tng.debugging", default=False)

# Asked for at import, which is safe and was worth checking rather than
# assuming: a meter obtained before the SDK starts is a `_ProxyMeter`, and its
# instruments are proxies that begin recording the moment `set_meter_provider`
# is called. Measured on the pinned API. So there is no first-use dance, and
# `opentelemetry.metrics` costs nothing extra -- `logs.py` has already imported
# the API package by the time this module is read.
COUNTER = metrics.get_meter("inventory_tng.debugging").create_counter(
    "inventory.debug_traces",
    description="Requests presenting a debug-tracing token, by what was made of it.",
)


def lifetime(seconds: int | None = None) -> int:
    """How long a newly minted token is good for, refusing a number that is not.

    Nought and below are refused rather than taken. A token minted with a
    lifetime of nought is expired before it is printed, and `minted` answers
    the same way it answers a forgery -- so the feature would be off, the
    command would still print something that looked like a token, and the only
    sign of it would be `inventory.debug_traces{outcome="refused"}` climbing,
    which this application's own documentation teaches an operator to read as
    somebody guessing at signatures.
    """
    held = int(settings.DEBUG_TRACE_LIFETIME_SECONDS if seconds is None else seconds)
    if held < 1:
        raise ValueError(
            f"DEBUG_TRACE_LIFETIME_SECONDS={held!r} would mint tokens that have already expired. "
            "Set a number of seconds a person can use."
        )
    return held


def allowance() -> tuple[int, int]:
    """How many requests one token may have recorded, and over what window."""
    return refusals.rate(settings.DEBUG_TRACE_RATE, "DEBUG_TRACE_RATE")


def signer() -> signing.TimestampSigner:
    """The signer, with `SECRET_KEY_FALLBACKS` explicitly out of it.

    `fallback_keys` defaults to that setting, which is Django's way to rotate
    a signing key without signing everybody out: old keys keep verifying while
    the new one signs. That is right for a session and wrong for this. Rotating
    the key is the only revocation this design offers -- there is no store to
    delete a token from -- so a rotation that kept honouring the old key would
    revoke nothing at all, silently, at the moment somebody was rotating
    BECAUSE a token had leaked. Empty here, so it means what it is documented
    to mean.
    """
    return signing.TimestampSigner(salt=SALT, fallback_keys=[])


def mint() -> str:
    """A token, signed and stamped, for one volunteer to carry.

    The value signed is a random id and nothing else. It is what the rate
    limit counts against, so two tokens minted the same afternoon are metered
    apart; and it means the token says nothing about the person holding it,
    which matters because a token travels through the same channels a name
    would and neither should be in a log.
    """
    return signer().sign(secrets.token_hex(8))


def minted(token: str) -> str:
    """The id inside a token, or nothing at all if it should not be honoured.

    One answer for every way a token can fail -- unsigned, tampered with,
    signed with a key that has since been rotated, or simply too old. A caller
    that could tell them apart could learn which by trying, and there is
    nothing useful to do differently about any of them.
    """
    if not token.strip():
        return ""
    try:
        return str(signer().unsign(token.strip(), max_age=lifetime()))
    except signing.BadSignature:
        return ""


def within_allowance(identifier: str) -> bool:
    """Whether this token has any of its allowance left in this window.

    A fixed window rather than a rolling one, and a counter that can lose a
    race, both for the reason DRF's own throttles accept the same: what is
    being bounded is the order of magnitude a leaked link can cost, and an
    exact answer would need somewhere shared to keep it.
    """
    count, window = allowance()
    key = f"{CACHE_PREFIX}{identifier}"
    try:
        used = cache.incr(key)
    except ValueError:
        cache.set(key, 1, window)
        used = 1
    return used <= count


def counted(outcome: str) -> None:
    """Record that a token was presented, and what came of it.

    A metric rather than a log line: what anybody wants to know is whether the
    refused count is climbing, which is a shape over time. `outcome` is on
    `redaction`'s allowlist; the token and its id are not, and never go near a
    span.
    """
    COUNTER.add(1, {"outcome": outcome})


def authorised(token: str) -> bool:
    """Whether this request may be recorded in full, and say so in a metric.

    The empty case is first and answers without touching the signer, because
    it is every request that is not being debugged -- which is all of them.
    """
    if not token.strip():
        return False

    identifier = minted(token)
    if not identifier:
        counted("refused")
        return False
    if not within_allowance(identifier):
        counted("throttled")
        return False

    counted("honoured")
    return True


def debugging() -> bool:
    """Whether the request being served asked to be recorded, and may be."""
    return _authorised.get()


@contextmanager
def asked(token: str) -> Iterator[None]:
    """The flag, for the life of one request and no longer.

    Set and reset rather than left for the next request to overwrite: a worker
    thread serves one request after another, and a flag that outlived its
    request would record somebody else's. Written once, because that reset in a
    `finally` is the subtle part and the two wrappers below both need it.
    """
    held = _authorised.set(authorised(token))
    try:
        yield
    finally:
        _authorised.reset(held)


# The name of the one route that carries a token and is not a request being
# traced: nginx asks it before forwarding a batch of the browser's spans, so it
# arrives once per exporter flush -- every few seconds, for the length of a
# session.
#
# THE NAME AND NOT THE PATH. The path was written here as a literal, which made
# it the third copy of the same string -- `urls.py`, here, and
# `frontend/nginx.conf.template` -- and the one whose going out of step is
# silent: a route renamed with this left behind starts quietly draining the
# allowance again, which is the defect this whole function exists to have
# fixed. Reversed rather than compared, so the URLconf is the single place the
# path is written.
VERIFYING = "debug-trace"


@functools.cache
def _verifying_path() -> str:
    """Resolved once, and not at import: `urls` reaches `views`, which reaches
    this module, so asking at import time is a cycle.
    """
    from django.urls import reverse

    return reverse(VERIFYING)


def spends_the_allowance(path: str) -> bool:
    """Whether a request on this path should be charged to the token.

    `DebugTraceVerifyView` states the rule; this is where it is kept, because
    the wrapper below runs OUTSIDE Django and therefore reaches the view that
    was written believing it did not. So nginx's `auth_request` was spending
    the allowance meant for the requests it was authorising -- around a fifth
    of a 60/min budget at an ordinary export interval, with `counted` filing
    each one as an honoured trace besides -- and once the window ran out the
    volunteer's own requests stopped being recorded while the exporter kept
    posting.

    A test through Django's `Client` cannot see any of this: that client does
    not go through `inventory_tng.wsgi.application`, which is the thing that
    charges. `inventory_tng.context` names that divergence as exactly the
    defect inventory-tng-iqff.1 was.
    """
    return path != _verifying_path()


def guarded(application: Any) -> Any:
    """The WSGI application, wrapped so the sampler can be told before it runs."""

    def serving(environ: dict[str, Any], start_response: Any) -> Any:
        charged = environ.get(ENVIRON, "") if spends_the_allowance(environ.get("PATH_INFO", "")) else ""
        with asked(charged):
            return application(environ, start_response)

    return serving


def presented(scope: dict[str, Any]) -> str:
    """The token out of an ASGI scope, where headers are a list of byte pairs."""
    wanted = HEADER.lower().encode()
    return next((value.decode() for name, value in scope.get("headers", ()) if name.lower() == wanted), "")


def guarded_asgi(application: Any) -> Any:
    """The same, for the other shape of application.

    Written even though nothing here serves ASGI today -- the image runs
    gunicorn over WSGI -- because the alternative is an entry point that
    silently lacks a guard the other one has, which is the kind of asymmetry
    that is discovered by somebody switching servers during an incident.

    Only an HTTP scope is looked at. A lifespan or a websocket scope has no
    request to record and no headers worth reading.
    """

    async def serving(scope: dict[str, Any], receive: Any, send: Any) -> None:
        http = scope.get("type") == "http" and spends_the_allowance(scope.get("path", ""))
        with asked(presented(scope) if http else ""):
            await application(scope, receive, send)

    return serving
