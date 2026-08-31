"""What a caller with no account may ask, and how often.

`AnonymousReadThrottle` carries the argument and `inventory-tng-81f7.1` is the
issue. The sizing arithmetic is in .env.sample, where the person changing the
number is reading. None of it is repeated here.
"""

import pytest
from django.test import Client
from django.urls import reverse
from pytest_django.fixtures import Settings
from rest_framework.views import APIView

from inventory.models import Volunteer
from inventory.tests import helpers
from inventory.throttling import AnonymousReadThrottle
from inventory_tng import access

pytestmark = pytest.mark.django_db

#: Small enough that a test can exhaust it in a loop without pretending to be
#: a fast typist. The rate this application SHIPS with is asserted separately.
TINY = "3/min"


@pytest.fixture
def stranger(client: Client, settings: Settings) -> Client:
    """Signed out, and reaching an application that answers strangers.

    conftest.py's `client` is signed in; test_volunteer_access.py says why
    that matters and what forgetting it costs.
    """
    settings.VOLUNTEER_ACCESS = access.OPEN
    settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["anonymous-read"] = TINY
    client.logout()
    return client


def test_a_stranger_reading_too_often_is_refused(stranger: Client, volunteer: Volunteer) -> None:
    """Three through, the fourth refused, which is the limit doing its job."""
    answers = [stranger.get(reverse("volunteers")).status_code for _ in range(4)]

    assert answers[:3] == [200, 200, 200], f"the limit refused a caller before the rate was spent: {answers}"
    assert answers[3] == 429, (
        f"a caller with no account read the pick-list {len(answers)} times without being counted, so the "
        f"membership question costs nothing to ask ten thousand times; answers were {answers}"
    )


def test_a_signed_in_caller_is_not_counted(client: Client, settings: Settings, volunteer: Volunteer) -> None:
    """An administrator reading all day is not what this limit is about.

    `AnonymousReadThrottle` says why counting them would spoil the number.
    """
    settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["anonymous-read"] = TINY

    answers = [client.get(reverse("volunteers")).status_code for _ in range(6)]

    assert answers == [200] * 6, f"a signed-in caller was counted against the anonymous read limit: {answers}"


def test_an_append_is_not_counted_against_reading(stranger: Client) -> None:
    """The two limits are separate buckets, which is the whole of `scope`.

    A volunteer who has spent their reading allowance searching for their own
    name must still be able to submit; the append has its own limit and its
    own argument.
    """
    for _ in range(4):
        stranger.get(reverse("volunteers"))

    made = stranger.post(reverse("volunteers"), {"display_name": "Robin Alvarez"}, content_type="application/json")

    assert made.status_code != 429, (
        "an exhausted read allowance refused an append, so a volunteer who searched for their own name "
        "cannot record what they took"
    )


def test_the_shipped_rate_clears_somebody_typing() -> None:
    """The number itself, held against what the client actually does.

    Neither pick-list debounces, so a keystroke is a request. A limit that
    looks generous in the abstract can still refuse a volunteer halfway
    through a name, and that failure would arrive as an unexplained error in
    front of a shelf rather than as anything anybody could debug.

    Twelve characters is `Sean Delaney`. Ten such searches in a minute is
    faster than a person picks names off a list.
    """
    import importlib

    shipped = importlib.import_module("inventory_tng.settings").env.scheme["ANONYMOUS_READ_RATE"][1]
    allowed, _, period = shipped.partition("/")

    assert period == "min", f"the shipped rate is per {period!r}, and the arithmetic below assumes a minute"
    assert int(allowed) >= 12 * 10, (
        f"the shipped rate of {shipped} allows fewer than ten twelve-character searches a minute, and a "
        "keystroke is a request: this refuses a volunteer typing rather than the oracle it is for"
    )


# ---------------------------------------------------------------------------
# That nothing opens a read without one
# ---------------------------------------------------------------------------

#: Reading DATA, which is narrower than `SAFE_METHODS`. `OPTIONS` is in that
#: set and answers with which methods a view allows -- protocol furniture, not
#: a row out of the database, and nothing the membership question can be asked
#: through. Counting it would put three POST-only endpoints on the list below
#: for no reason anybody could act on.
READS = frozenset({"GET", "HEAD"})

#: Views that answer a stranger's GET and carry no read limit, each with the
#: reason. The list is short on purpose: an entry here is an endpoint somebody
#: may ask for as often as they like.
UNCOUNTED = {
    "HealthCheckView": "a probe; throttling it turns a busy minute into a restart",
    "LivenessCheckView": "the other probe, for the same reason",
    "ApiRootView": "the index, and the CSRF cookie a browser needs before it can do anything",
    "CurrentUserView": "read on every load; refusing it blanks the application rather than slowing it",
    "DebugTraceVerifyView": "the signed token it is handed is the credential, and debugging.py bounds its own use",
    "SpectacularAPIView": "describes this surface rather than being part of it",
    "SpectacularSwaggerSplitView": "renders that description for a person",
}


def test_every_read_a_stranger_can_make_is_counted_or_argued(settings: Settings) -> None:
    """The drift guard, and it exists because this drifted once already.

    `inventory-tng-gnhl` opened five reads and the suite noticed nothing about
    any of them, because every audit then asked whether what was open had been
    ARGUED. None asked whether it had been counted. This is that question, and
    it is asked of admission rather than of a list of view names so that the
    next endpoint to open is covered by having opened.
    """
    settings.VOLUNTEER_ACCESS = access.OPEN

    uncounted = set()
    for route, view in helpers.drf_routes():
        readable = [method for method in helpers.offered(view) if method in READS]
        if not any(helpers.admits_anonymously(route, method) for method in readable):
            continue
        if not any(issubclass(throttle, AnonymousReadThrottle) for throttle in _throttles(view)):
            uncounted.add(view.__name__)

    unargued = sorted(uncounted - set(UNCOUNTED))
    assert not unargued, (
        f"{unargued} answer a stranger's read and count it against nothing, so it may be asked as often "
        "as somebody likes: give each ANONYMOUS_READ_THROTTLES, or argue it above"
    )
    stale = sorted(set(UNCOUNTED) - uncounted)
    assert not stale, f"{stale} no longer need the exemption above; delete the line rather than keep it"


def _throttles(view: type[APIView]) -> list[type]:
    """What a view would apply, however it says so.

    `throttle_classes` on the class where it names its own, and DRF's default
    otherwise -- which this project deliberately leaves empty, so the
    distinction matters: an endpoint that names none has none.
    """
    return list(getattr(view, "throttle_classes", []))
