"""Fixtures shared by the catalogue and ledger tests.

They describe one small, real scene -- a warehouse, a volunteer holding stock,
and a radio -- so tests in either module read as statements about NYC Mesh
rather than about test data.
"""

import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
import yaml
from allauth.account.authentication import AUTHENTICATION_METHODS_SESSION_KEY
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connection
from django.test import Client
from pytest_django.fixtures import Settings

from inventory.models import Category, Item, Location, Volunteer
from inventory.tests.helpers import sign_in_locally
from inventory_tng import redaction, telemetry

# The one this suite signs in with. Not a secret and never one: the account is
# made and destroyed inside a test database.
ADMINISTRATOR_PASSWORD = "not-a-real-password"

# The generated schema, committed so it can be read without running anything.
# See DEVELOPERS.md#the-api-schema.
SCHEMA_PATH = Path(settings.BASE_DIR).parent / "openapi.yaml"

# Where the fixtures below pretend a collector is. Nothing listens on it: the
# exporters are substituted, and the address exists so that `start` sees an
# endpoint configured and does its real work.
COLLECTOR = "http://localhost:4318"


@pytest.fixture(autouse=True)
def _unstarted(monkeypatch: pytest.MonkeyPatch) -> None:
    """`start` remembers what it has done, in module state that outlives a test.

    Autouse across the whole suite rather than in the two modules that start an
    SDK: what it guards against is a test finding the SDK already started by an
    earlier one, and that is not a property of the module asking.
    """
    for flag in ("_started", "_instrumented_django"):
        monkeypatch.setattr(telemetry, flag, False)


@pytest.fixture(autouse=True)
def _redacting_again_afterwards() -> Iterator[None]:
    """`redaction.settle` writes a process global, like the console layout.

    Anything that turns personal-data recording on -- a test, or a call to
    `logs.from_environment` with it set -- leaves it on for every test that
    runs afterwards, and the suite passes anyway because the assertions that
    would notice happen to run first. That is an order dependency waiting for
    somebody to install pytest-randomly.
    """
    yield
    redaction.settle(False)


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

    Almost every API test needs one. Overriding the built-in ``client``
    fixture rather than adding a name means a test reads as ordinary unless it
    deliberately calls ``logout()``.
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
    because ``RecentlyAuthenticated`` puts a question to a destructive
    operation that ``force_login`` leaves no answer to. A test that wants the other case -- a session
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


@pytest.fixture
def substituted(monkeypatch: pytest.MonkeyPatch) -> Any:
    """`start`'s exporters replaced, and nothing else.

    A real one keeps a thread that outlives the test and retries against a
    collector that is not there. Everything else -- the provider, the sampler,
    both instrumentations, the order they run in -- is what a deployment gets,
    which is the point: a fixture that rebuilt that by hand would keep passing
    after `start` changed.
    """
    from opentelemetry import trace
    from opentelemetry.metrics import _internal as metrics_internal
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", COLLECTOR)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter", lambda **kwargs: exporter
    )
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.metric_exporter.OTLPMetricExporter", lambda **kwargs: None
    )
    monkeypatch.setattr("opentelemetry.sdk.trace.export.BatchSpanProcessor", SimpleSpanProcessor)
    monkeypatch.setattr(
        "opentelemetry.sdk.metrics.export.PeriodicExportingMetricReader", lambda *a, **k: InMemoryMetricReader()
    )
    # `get_tracer_provider` hands back the proxy WITHOUT assigning it, so
    # putting its return value back afterwards installs a provider that
    # delegates to itself and every later `get_tracer` raises RecursionError.
    # The globals are read and written directly for that reason.
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(trace, "_TRACER_PROVIDER_SET_ONCE", trace.Once())
    monkeypatch.setattr(metrics_internal, "_METER_PROVIDER", None)
    monkeypatch.setattr(metrics_internal, "_METER_PROVIDER_SET_ONCE", trace.Once())
    return exporter


def started(monkeypatch: pytest.MonkeyPatch, substituted: Any) -> Iterator[Any]:
    """The real `start`, exporting into memory.

    Uninstrumenting afterwards is not optional: both instrumentations are
    global and would follow this test into the next one. Written once and
    delegated to by the two fixtures below, because that teardown is exactly
    the half nobody wants two copies of.
    """
    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

    monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", "1.0")
    assert telemetry.start() is True

    # The driver is patched at `connect`, and Django holds a connection open
    # from the previous test, so the next query has to open an instrumented
    # one. In a worker that is simply the order things happen in.
    connection.close()
    try:
        yield substituted
    finally:
        PsycopgInstrumentor().uninstrument()
        DjangoInstrumentor().uninstrument()
        connection.close()


@pytest.fixture
def recorded(substituted: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """An SDK exporting into memory, redacting as a deployment would."""
    yield from started(monkeypatch, substituted)


@pytest.fixture
def recorded_openly(monkeypatch: pytest.MonkeyPatch, substituted: Any) -> Iterator[Any]:
    """The same, with `TELEMETRY_PERSONAL_DATA=recorded`.

    Its own fixture rather than a parameter, because the toggle is read while
    `start` runs and a test that set it afterwards would be asserting against
    an SDK built before it.
    """
    monkeypatch.setenv("TELEMETRY_PERSONAL_DATA", "recorded")
    yield from started(monkeypatch, substituted)
