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
from pathlib import Path
from typing import Any

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
