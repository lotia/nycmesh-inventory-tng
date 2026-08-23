"""That a record written in production reaches somewhere a person can read it.

The regression these hold is not hypothetical. Django's shipped configuration
routes every record to one of two handlers that a deployment cannot have, so
until `inventory_tng.logs` existed an unhandled exception produced a 500 with
its traceback written nowhere at all. The whole of that failure is invisible
from inside the application, which is why it survived a release.

`inventory/tests/test_hosts.py` says why a pure function is held on its own
terms; the same argument applies to `log_level`. What cannot be held that way
is whether Django's own machinery actually reaches the handler, so the first
test drives a real request through a real exception.
"""

import copy
import io
import logging
import logging.config
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.test import Client
from django.urls import path
from pytest_django.fixtures import Settings

from inventory_tng.logs import log_level, logging_config


def explode(request: HttpRequest) -> HttpResponse:
    """A view whose only job is to fail the way a real one eventually will."""
    raise RuntimeError("the failure a deployment has to be able to see")


# `override_settings(ROOT_URLCONF=__name__)` reads these, so the raising view is
# reachable without adding a route the application would then have to carry.
urlpatterns = [path("explode/", explode)]


@contextmanager
def readable_stream() -> Iterator[io.StringIO]:
    """Apply the real configuration, with its one handler pointed at a buffer.

    The buffer stands in for standard output because pytest has already
    replaced that by the time a test runs. Substituting the destination and
    nothing else keeps every other part of the configuration under test --
    which handler, at which level, attached to which loggers. That the
    destination itself is right is a separate assertion below, so the
    substitution cannot hide a handler writing to the wrong place.
    """
    buffer = io.StringIO()
    substituted = copy.deepcopy(settings.LOGGING)
    substituted["handlers"]["stdout"]["stream"] = buffer
    logging.config.dictConfig(substituted)
    try:
        yield buffer
    finally:
        logging.config.dictConfig(settings.LOGGING)


def test_an_unhandled_exception_reaches_a_handler_with_debug_false(settings: Settings) -> None:
    """The bug, end to end: a view raises and the traceback has to be readable.

    `raise_request_exception=False` lets Django handle the exception the way it
    would in a deployment instead of re-raising it into the test, which is the
    only arrangement in which the logging is exercised at all.
    """
    settings.DEBUG = False
    settings.ROOT_URLCONF = __name__

    with readable_stream() as stream:
        response = Client(raise_request_exception=False).get("/explode/")

    assert response.status_code == 500
    written = stream.getvalue()
    assert "RuntimeError" in written
    assert "the failure a deployment has to be able to see" in written
    # Not merely the exception's own text: the stack is the part that says
    # which line raised, and it is what an `AdminEmailHandler` with no
    # recipients was swallowing.
    assert "Traceback (most recent call last)" in written
    assert "django.request" in written


def test_a_refused_host_reaches_a_handler() -> None:
    """`django.security.*` is the other family that had nowhere to go.

    A rejected `Host` header is answered with a bare 400 and no body, so the
    log line is the only account of why. That silence is what made
    inventory-tng-adj present as a crash loop against a healthy database.
    """
    with readable_stream() as stream:
        logging.getLogger("django.security.DisallowedHost").error("Invalid HTTP_HOST header: '10.42.0.17'")

    assert "10.42.0.17" in stream.getvalue()


def test_this_applications_own_loggers_reach_the_same_handler() -> None:
    """`inventory.*` is not under `django`, so the root logger has to carry it.

    Nothing logs yet -- inventory-tng-nb8.9 is where the application starts --
    but the arrangement has to be waiting when it does, or the first
    `getLogger` call ships into a deployment that discards it.
    """
    with readable_stream() as stream:
        logging.getLogger("inventory.ledger").warning("an append was refused")

    assert "an append was refused" in stream.getvalue()
    assert "inventory.ledger" in stream.getvalue()


def test_the_handler_writes_to_standard_output() -> None:
    """Held separately because every test above substitutes that destination.

    Standard output is the whole point of the change: it is what a container
    runtime collects and what `kubectl logs` reads.
    """
    assert settings.LOGGING["handlers"]["stdout"]["stream"] == "ext://sys.stdout"


def test_nothing_is_filtered_by_whether_debug_is_on() -> None:
    """The precise defect. Django's own handlers each carry one of these."""
    handlers = settings.LOGGING["handlers"].values()
    assert not any(handler.get("filters") for handler in handlers)


def test_a_lower_level_admits_less() -> None:
    """The level reaches both the root logger and Django's own."""
    config = logging_config("WARNING")

    assert config["root"]["level"] == "WARNING"
    assert config["loggers"]["django"]["level"] == "WARNING"


def test_django_records_are_handled_once() -> None:
    """`django` has a handler of its own, so it must not propagate as well.

    Otherwise every warning Django emits is printed twice, which is the usual
    result of naming a logger in `dictConfig` without thinking about the root.
    """
    assert logging_config("INFO")["loggers"]["django"]["propagate"] is False


@pytest.mark.parametrize("requested", ["debug", " DEBUG ", "Debug"])
def test_a_level_is_read_regardless_of_how_it_was_typed(requested: str) -> None:
    """It arrives from a `.env` file or a ConfigMap, where both happen."""
    assert log_level(requested) == "DEBUG"


def test_a_level_python_does_not_know_stops_the_process() -> None:
    """Rather than falling back and leaving the developer to wonder."""
    with pytest.raises(ValueError, match="VERBOSE"):
        log_level("VERBOSE")


def test_the_refusal_says_what_would_have_worked() -> None:
    """A message naming the mistake and not the alternatives is half a message."""
    with pytest.raises(ValueError, match="INFO"):
        log_level("VERBOSE")
