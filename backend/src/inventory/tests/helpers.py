"""Helpers shared by the tests.

Here rather than in conftest.py because they are plain functions: pytest
collects fixtures from conftest, and a helper that is imported reads better at
the call site than one that is injected.
"""

import copy
import io
import json
import logging
import logging.config
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import pyotp
from allauth.account.authentication import AUTHENTICATION_METHODS_SESSION_KEY
from allauth.mfa.totp.internal.auth import TOTP, generate_totp_secret
from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from inventory_tng import redaction

# Files outside the backend that tests assert against, named once.
#
# Several modules read these -- what the image collects, what the proxy
# forwards, what its content policy says -- and each used to name its own path
# and build it its own way, one from `REPO_ROOT` and another from `BASE_DIR`.
# Two spellings of one path is the shape `charts.py` warns about: the copies
# agree until they do not, and the one that drifts goes on passing.
NGINX_TEMPLATE = Path("frontend") / "nginx.conf.template"
BACKEND_DOCKERFILE = Path("backend") / "Dockerfile"


def wind_back(client: Client) -> Client:
    """The same session, whose sign-in is no longer recent enough.

    Wound back rather than waited out: the window is fifteen minutes and a
    test suite is not going to sit through one. What is moved is the timestamp
    allauth itself records and reads, so this is the same state a session
    reaches by being left open.

    Takes the client rather than making one, because WHICH session is stale is
    the only thing callers differ on -- conftest's `stale` winds back one that
    enrolled a second factor, and test_second_factor.py winds back one that
    signed in with a password and never did.
    """
    # Held rather than re-read: `Client.session` builds a store from the cookie
    # each time it is asked, so writing through the property and saving through
    # it again saves a different object than the one that was changed.
    session = client.session
    stale_at = time.time() - settings.ACCOUNT_REAUTHENTICATION_TIMEOUT - 1
    session[AUTHENTICATION_METHODS_SESSION_KEY] = [
        {**record, "at": stale_at} for record in session[AUTHENTICATION_METHODS_SESSION_KEY]
    ]
    session.save()
    return client


def shipped(path: Path) -> str:
    """The text of a file in this repository, whatever the caller's directory."""
    return (settings.REPO_ROOT / path).read_text()


@contextmanager
def applied(config: dict[str, Any], admitting: bool = False) -> Iterator[io.StringIO]:
    """Apply a logging configuration, with its one handler pointed at a buffer.

    The buffer stands in for standard output because pytest has already
    replaced that by the time a test runs. Substituting the destination and
    nothing else keeps every other part of the configuration under test --
    which handler, at which level, attached to which loggers. That the
    destination itself is right is asserted separately, so the substitution
    cannot hide a handler writing to the wrong place.

    Restores the real configuration afterwards, because `dictConfig` is global:
    a test that left its own arrangement in place would be heard from much
    later, in whichever test happened to run next. `redaction.settle` is the
    same story one module along -- what `logs.configure` would have called, and
    what the caller would otherwise have to remember to undo.

    Three test modules had a copy of this and one of them had already grown the
    `settle` half the others lacked, which is what a shared helper is for.
    """
    redaction.settle(admitting)
    buffer = io.StringIO()
    substituted = copy.deepcopy(config)
    substituted["handlers"]["stdout"]["stream"] = buffer
    logging.config.dictConfig(substituted)
    try:
        yield buffer
    finally:
        redaction.settle(False)
        logging.config.dictConfig(settings.LOGGING)


def written_by(stream: io.StringIO, logger: str) -> list[dict[str, Any]]:
    """Every record one named logger wrote, in order.

    Beside `every_record` because it is that with one question asked of it, and
    because a test module that declared its own was the third copy of the same
    comprehension.
    """
    return [record for record in every_record(stream) if record["logger"] == logger]


def one_record(stream: io.StringIO) -> dict[str, Any]:
    """The single JSON record written to a buffer."""
    return json.loads(stream.getvalue())


def every_record(stream: io.StringIO) -> list[dict[str, Any]]:
    """Every JSON record written to a buffer, in order."""
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def post(client: Client, name: str, body: dict[str, Any], *args: Any) -> Any:
    return client.post(reverse(name, args=args), data=body, content_type="application/json")


def patch(client: Client, name: str, body: dict[str, Any], *args: Any) -> Any:
    return client.patch(reverse(name, args=args), data=body, content_type="application/json")


# --------------------------------------------------------------------------
# Signing in. See test_sign_in.py, and
# docs/decisions/0013-administrator-sign-in.md for what it is all for.
# --------------------------------------------------------------------------

# Every provider decision 0013 point 1 names, with credentials that are not
# credentials. Nothing here is ever sent anywhere: what a test can prove on
# this side of the boundary is that a configured provider is offered and that
# a completed callback buys nothing.
PROVIDERS: dict[str, Any] = {
    "google": {"APPS": [{"client_id": "not-a-client-id", "secret": "not-a-secret"}]},
    "slack": {"APPS": [{"client_id": "not-a-client-id", "secret": "not-a-secret"}]},
    "openid_connect": {
        "APPS": [
            {
                # The id is part of the callback URL registered with the
                # provider, which is why a deployment names it rather than
                # inheriting one.
                "provider_id": "oidc",
                "name": "Single sign-on",
                "client_id": "not-a-client-id",
                "secret": "not-a-secret",
                "settings": {"server_url": "https://idp.example.net"},
            }
        ]
    },
}

# What each provider hands back about a person, in that provider's own shape.
# Slack's is the one worth looking at: it identifies an account by workspace
# *and* user, so its uid is a pair, which is the sort of thing a shared
# payload would have hidden.
PROVIDER_PAYLOADS: dict[str, dict[str, Any]] = {
    "google": {"sub": "google-uid", "email": "newcomer@example.com", "name": "New Comer"},
    "slack": {
        "sub": "slack-uid",
        "email": "newcomer@example.com",
        "name": "New Comer",
        "https://slack.com/team_id": "T0NYCMESH",
        "https://slack.com/user_id": "U0NEWCOMER",
    },
    "oidc": {"sub": "oidc-uid", "email": "newcomer@example.com", "name": "New Comer"},
}


def activate_totp(user: User) -> str:
    """Give ``user`` a TOTP key and return its secret.

    The secret is what an authenticator app would have been shown, so a test
    holding it can produce codes the way a phone does.
    """
    secret = generate_totp_secret()
    TOTP.activate(user, secret)
    return secret


def start_local_sign_in(user: User, password: str) -> Client:
    """A password accepted, and nothing else yet.

    The state decision 0013 point 3 is about, and the subject of most of
    test_sign_in.py: authenticated as far as allauth is concerned, and held at
    its door by RequireSecondFactor until a TOTP key exists.
    """
    client = Client()
    client.post(reverse("account_login"), {"login": user.get_username(), "password": password})
    return client


def sign_in_locally(user: User, password: str) -> Client:
    """Sign in the way decision 0013 point 2 keeps available: password, then code."""
    secret = activate_totp(user)
    client = start_local_sign_in(user, password)
    client.post(reverse("mfa_authenticate"), {"code": pyotp.TOTP(secret).now()})
    return client


# ---------------------------------------------------------------------------
# One walk over the URLconf, for every audit that needs one
# ---------------------------------------------------------------------------

# A value per converter a route may be written with, so that a route carrying
# an argument can be reversed at all. Keyed by the name the route spells.
#
# Deliberately not exhaustive, and `routes()` refuses loudly rather than
# skipping when it meets a converter that is missing here. A walk that quietly
# passed over the routes it could not build would be an audit with a hole in
# exactly the shape of whatever was added last.
SPECIMENS: dict[str, Any] = {"int": 1, "str": "specimen", "slug": "specimen", "uuid": UUID(int=0)}


@dataclass(frozen=True)
class Route:
    """One registration this repository owns, with everything an audit asks of it."""

    name: str
    # The route as the URLconf spells it -- `api/items/<int:pk>` rather than
    # the path somebody asked for. What a span is named after, and what tells
    # a hundred item lookups apart from a hundred routes.
    route: str
    # The view CLASS, however the registration spells it. `.cls` is where
    # `APIView.as_view()` puts it and `.view_class` is where Django's own
    # `View.as_view()` does; a router-mounted ViewSet has neither, and then
    # this is None rather than a guess.
    view: type | None
    arguments: dict[str, Any]
    url: str
    pattern: Any


def routes() -> list[Route]:
    """Every route `inventory_tng.urls` declares itself.

    THE ONE PLACE THAT DECIDES WHAT A ROUTE IS. Six hand-rolled walks existed
    before this and they disagreed: three asked `getattr(callback, "cls",
    None)` and two asked `isinstance(pattern, URLPattern)`, so the day an
    endpoint moved behind a router the first three stopped seeing views and
    passed in silence -- and the repair would have been needed in six places.
    `inventory-tng-s047`.

    AN `include` IS NOT WALKED, and that is a boundary rather than an
    oversight: what is behind `accounts/` is allauth's routing and what is
    behind `admin/` is Django's, and asserting about their internals means
    asserting about somebody else's design -- an upgrade that renamed a route
    would fail this build for no reason of ours. So the mount is what gets
    asserted about instead, at the point where this repository chose it:
    `test_every_mount_this_urlconf_makes_is_one_the_record_argued` says what
    that reaches and what it gives up.
    """
    from django.urls import URLPattern, reverse
    from django.urls.converters import get_converters

    from inventory_tng import urls

    named = {type(converter): name for name, converter in get_converters().items()}
    found = []
    for pattern in urls.urlpatterns:
        if not isinstance(pattern, URLPattern):
            continue
        arguments: dict[str, Any] = {}
        for argument, converter in pattern.pattern.converters.items():
            kind = named.get(type(converter))
            assert kind in SPECIMENS, (
                f"{pattern.pattern} takes a {kind}, which inventory.tests.helpers.SPECIMENS has no "
                "value for -- add one rather than letting this route go unwalked"
            )
            arguments[argument] = SPECIMENS[kind]
        # REFUSED RATHER THAN SKIPPED, like a missing specimen above. An
        # unnamed route cannot be reversed, so it cannot be walked -- and a
        # walk that passed silently over it would leave the audits blind to
        # exactly the registration somebody added without a name.
        assert pattern.name, f"{pattern.pattern} is registered without a name, so no audit here can reach it"
        callback = pattern.callback
        found.append(
            Route(
                name=pattern.name,
                route=str(pattern.pattern),
                view=getattr(callback, "cls", None) or getattr(callback, "view_class", None),
                arguments=arguments,
                url=reverse(pattern.name, kwargs=arguments),
                pattern=pattern,
            )
        )
    return found


def admits_anonymously(view: type, method: str, url: str, mounted: dict[str, Any] | None = None) -> bool:
    """Whether every permission class would let an anonymous request through.

    THE QUESTION THE AUDIT USED TO ASK WAS A DIFFERENT ONE.
    ``permissions.open_to_anybody`` reads the classes a view NAMES and answers
    True only for ``AllowAny``. That is a fact about spelling, and admission is
    not spelled: ``StaffWrites.has_permission`` returns True for every safe
    method from anybody, its own docstring saying reads are left to whatever
    else guards the endpoint. So ``[StaffWrites, RecentlyAuthenticated]`` --
    the project default with one class removed -- serves every read to a
    stranger while that predicate answers False. Measured, not supposed.
    ``inventory-tng-2hbv``.

    Asked with a REAL request of the method in question, rather than one method
    substituted onto another's request. ``CurrentUserView._permitted`` does the
    latter because it has only the caller's own GET to work with and must not
    invent a body; here there is no caller, so the honest request is the one
    that would actually arrive.

    Built the way the URLconf builds it: ``as_view(permission_classes=...)`` is
    legal and DRF applies it per request, so a view instantiated bare reads as
    closed when the route deliberately opened it.
    """
    from django.contrib.auth.models import AnonymousUser
    from django.test import RequestFactory

    instance = view(**(mounted or {}))
    request = RequestFactory().generic(method, url)
    request.user = AnonymousUser()
    # Both, because a permission class is handed the request and may read the
    # view, and DRF sets these before any of them is consulted.
    instance.request = request
    instance.args, instance.kwargs = (), {}
    return all(permission.has_permission(request, instance) for permission in instance.get_permissions())
