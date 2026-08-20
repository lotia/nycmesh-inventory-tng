"""Fixtures shared by the catalogue and ledger tests.

They describe one small, real scene -- a warehouse, a volunteer holding stock,
and a radio -- so tests in either module read as statements about NYC Mesh
rather than about test data.
"""

import time
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
import yaml
from allauth.account.authentication import AUTHENTICATION_METHODS_SESSION_KEY
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client
from pytest_django.fixtures import Settings

from inventory.models import Category, Item, Location, Volunteer
from inventory.tests.helpers import sign_in_locally

# The one this suite signs in with. Not a secret and never one: the account is
# made and destroyed inside a test database.
ADMINISTRATOR_PASSWORD = "not-a-real-password"

# The generated schema, committed so it can be read without running anything.
# See DEVELOPERS.md#the-api-schema.
SCHEMA_PATH = Path(settings.BASE_DIR).parent / "openapi.yaml"


@pytest.fixture(autouse=True)
def _forget_throttle_history() -> None:
    """Rate-limit counters live in the cache, which no transaction rolls back.

    Without this, one test's writes are counted against the next one's limit
    and whether a test sees a 429 depends on what ran before it.
    """
    cache.clear()


@pytest.fixture
def category() -> Category:
    return Category.objects.create(name="Radios")


@pytest.fixture
def item(category: Category) -> Item:
    return Item.objects.create(name="LiteBeam", category=category)


@pytest.fixture
def volunteer() -> Volunteer:
    return Volunteer.objects.create(display_name="Sean")


@pytest.fixture
def warehouse() -> Location:
    return Location.objects.create(name="131 Broome", kind=Location.Kind.WAREHOUSE)


@pytest.fixture
def custody(volunteer: Volunteer) -> Location:
    return Location.objects.create(
        name="Sean",
        kind=Location.Kind.VOLUNTEER_CUSTODY,
        held_by=volunteer,
    )


@pytest.fixture
def client(client: Client) -> Client:
    """The Django test client, signed in.

    Every endpoint but the index and the health check requires a session
    today, so almost every API test needs this. Overriding the built-in
    ``client`` fixture rather than adding a name means a test reads as
    ordinary unless it deliberately calls ``logout()``.
    """
    client.force_login(User.objects.create_user(username="tester", password="not-a-real-password"))
    return client


@pytest.fixture
def administrator() -> User:
    """Somebody who may reach the Django admin."""
    return User.objects.create_superuser(username="editor", password=ADMINISTRATOR_PASSWORD)


@pytest.fixture
def editor(administrator: User) -> Client:
    """A second client, signed in as somebody who may change things.

    Its own ``Client`` rather than the one above logged in again: that one is
    the ordinary volunteer every refusal is asserted against, and a test
    needing both sessions needs them at the same time.

    Signed in through the app's own door rather than with ``force_login``,
    because a destructive operation asks when this session last proved who it
    was (decision 0014 point 5, ``RecentlyAuthenticated``) and ``force_login``
    leaves no answer to that. A test that wants the other case -- a session
    old enough to be asked again -- has ``stale`` below.
    """
    return sign_in_locally(administrator, ADMINISTRATOR_PASSWORD)


@pytest.fixture
def stale(editor: Client) -> Client:
    """The same administrator, whose sign-in is no longer recent enough.

    Wound back rather than waited out: the window is fifteen minutes and a
    test suite is not going to sit through one. What is moved is the timestamp
    allauth itself records and reads, so this is the same state a session
    reaches by being left open.
    """
    # Held rather than re-read: `Client.session` builds a store from the cookie
    # each time it is asked, so writing through the property and saving through
    # it again saves a different object than the one that was changed.
    session = editor.session
    stale_at = time.time() - settings.ACCOUNT_REAUTHENTICATION_TIMEOUT - 1
    session[AUTHENTICATION_METHODS_SESSION_KEY] = [
        {**record, "at": stale_at} for record in session[AUTHENTICATION_METHODS_SESSION_KEY]
    ]
    session.save()
    return editor


@pytest.fixture(scope="session")
def schema() -> Mapping[str, Any]:
    """The committed OpenAPI document, parsed once for the whole run.

    The contract clients are generated from, so several modules assert against
    it; at 66KB, parsing it per test is most of a second of the suite.
    """
    # Read-only: one parsed document is shared by the whole run, and a test
    # that reassigned a key in it would fail an unrelated module later.
    return MappingProxyType(yaml.safe_load(SCHEMA_PATH.read_text()))


@pytest.fixture
def _static_files_are_not_collected(settings: Settings) -> None:
    """Serve static files straight from the apps, as a development checkout does.

    Outside DEBUG the project hashes static files through WhiteNoise's manifest
    storage, which is right for the built image -- the image runs collectstatic
    -- and impossible under test, where nothing has. Every admin template opens
    with ``{% static 'admin/css/base.css' %}``, so without this a rendering test
    fails on the manifest rather than on the page. Overridden here rather than in
    settings.py so the deployed behaviour is exactly what it was.
    """
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
