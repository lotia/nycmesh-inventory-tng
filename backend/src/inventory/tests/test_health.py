"""The two probes, held apart.

What separates them is not their bodies but what each one asks of, and the
whole of inventory-tng-uq6 was that the two asked the same thing. So the
assertions here are about what a request reaches for: readiness reaches the
database, liveness reaches nothing, and a test that only fetched both would
pass against the arrangement that bug describes.

Every request here is made by a client holding no session, because that is
what a kubelet is. It is also the only way to measure: the shared `client`
fixture signs in, and a session is two queries of its own before either view
has done anything.
"""

import socket
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse


@pytest.fixture
def kubelet() -> Client:
    """A client holding no session, because no probe holds one."""
    return Client()


@pytest.mark.django_db
def test_readiness_asks_the_database_whether_it_is_there(kubelet: Client, django_assert_num_queries: Any) -> None:
    """The half of the split that must keep touching the database.

    A pod that cannot reach it should leave the Service, and this query is the
    only thing that would tell the kubelet so.
    """
    with django_assert_num_queries(1):
        response = kubelet.get(reverse("healthz"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_liveness_asks_nothing_of_the_database(kubelet: Client) -> None:
    """The bug, stated as the thing that has to stay true.

    No ``django_db`` mark, which is the assertion: pytest-django blocks the
    database outright for a test that has not asked for it, so this says "this
    view cannot reach a database" rather than "nought queries were counted on
    the default connection". A view that read a second alias, or a cache
    backed by one, would slip past the second and not past this.
    """
    response = kubelet.get(reverse("livez"))

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_liveness_opens_a_connection_to_nothing_at_all(kubelet: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """The rule is "reaches for nothing", and a query count only sees the ORM.

    Blocking the database catches the database. An object store or an HTTP
    call added to this view tomorrow would pass that and still restart every
    pod on the next blip of whatever it called -- this bug again, walking past
    its own regression test. Anything reaching outside this process has to
    open a socket, so a socket is what is barred.
    """

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the liveness probe reached for something outside this process")

    monkeypatch.setattr(socket.socket, "connect", refuse)

    assert kubelet.get(reverse("livez")).status_code == 200
