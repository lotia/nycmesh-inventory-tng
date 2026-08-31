"""What a caller with no account reaches, and what they still cannot.

`inventory_tng.access` carries the argument and `inventory-tng-gnhl` is the
issue. Neither is repeated here.

WHICH ENDPOINTS OPEN IS NOT ASSERTED IN THIS FILE. That is
test_capabilities.py's, which asks it of every route under both settings and
holds the answer against a list carrying an argument per entry; a second list
here would be the copy that goes stale. What is held here is that the setting
reaches the wire at all, that the default is the careful one, and that opening
the door does not also open what is behind it.
"""

import importlib

import pytest
from django.test import Client
from django.urls import reverse
from pytest_django.fixtures import Settings

from inventory.models import Item, Location, Volunteer
from inventory_tng import access

# Every test here drives the wire or reads a shipped file, and conftest.py's
# `client` builds a user, so the marker is the module's rather than repeated.
pytestmark = pytest.mark.django_db

SETTING = "VOLUNTEER_ACCESS"


@pytest.fixture
def catalogue(item: Item, warehouse: Location, volunteer: Volunteer) -> None:
    """Enough of a catalogue that an empty answer is not what a read returns.

    conftest.py's rows rather than new ones: a read that returns `[]` would
    answer 200 whatever the permission layer decided, so what is asked here has
    to have something to find.
    """


@pytest.fixture
def stranger(client: Client) -> Client:
    """The signed-out client, which is what this whole file is about.

    conftest.py overrides `client` to be SIGNED IN, because almost every test
    in this suite wants that. Every assertion here is about somebody who is
    not, so forgetting this line is the way to write a test that passes
    against the wrong caller -- which is how the first draft of this file was
    wrong.
    """
    client.logout()
    return client


@pytest.fixture
def anybody(stranger: Client, settings: Settings) -> Client:
    settings.VOLUNTEER_ACCESS = access.OPEN
    return stranger


# The reads gnhl opens, by route name, so this file names a URL rather than a
# view and cannot drift from the URLconf. Deliberately not the same shape as
# test_capabilities.py's list: that one argues WHY each is open, and this one
# only drives them.
VOLUNTEER_READS = ["items", "locations", "labels", "volunteers"]


# ---------------------------------------------------------------------------
# The default, which is what this application did before the setting existed
# ---------------------------------------------------------------------------


def test_the_default_asks_a_volunteer_to_sign_in() -> None:
    """Asked of the DECLARED default rather than the loaded one; test_second_factor.py says why."""
    declared = importlib.import_module("inventory_tng.settings").env.scheme[SETTING]

    assert declared == (str, access.SESSION), (
        "the code default now answers callers with no account, so a deployment that configures "
        f"nothing has opened its catalogue without anybody choosing to. It declares {declared}"
    )


@pytest.mark.parametrize("route", VOLUNTEER_READS)
def test_without_the_setting_every_volunteer_read_is_refused(stranger: Client, catalogue: None, route: str) -> None:
    """Today's behaviour, and the thing an upgrade must not change quietly."""
    assert stranger.get(reverse(route)).status_code == 403


# ---------------------------------------------------------------------------
# The open posture
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", VOLUNTEER_READS)
def test_the_open_posture_answers_a_caller_with_no_account(anybody: Client, catalogue: None, route: str) -> None:
    assert anybody.get(reverse(route)).status_code == 200


def test_a_scanned_code_is_resolved_without_an_account(anybody: Client, catalogue: None) -> None:
    """The resolver, which is the scan itself.

    A code nothing is printed for answers 404, and that is what makes this
    worth asserting: reaching an endpoint and being refused by it are both
    "not 200" to a careless test, and 403 is precisely what the permission
    layer this setting moves would produce.
    """
    answer = anybody.get(reverse("label-resolve", kwargs={"code": "0000000000"}))

    assert answer.status_code == 404, (
        f"the resolver answered {answer.status_code} to a caller with no account; a 403 here means "
        "the scan is still closed to the people gnhl is about"
    )


def test_a_volunteer_may_still_not_edit_the_catalogue(anybody: Client, catalogue: None) -> None:
    """The door opens; what is behind it does not.

    `VolunteerReaches` replaces `IsAuthenticated` and nothing else, so
    `StaffWrites` is untouched. This is the assertion that fails if somebody
    ever "simplifies" `VOLUNTEER_READ` to `[AllowAny]`, which reads as the same
    thing and is not.
    """
    refused = anybody.post(reverse("items"), {"name": "Invented"}, content_type="application/json")

    assert refused.status_code == 403


# ---------------------------------------------------------------------------
# What an open door does not disclose
# ---------------------------------------------------------------------------


def test_a_custody_location_does_not_name_its_holder_anonymously(anybody: Client, custody: Location) -> None:
    """`inventory-tng-81f7` question 2, answered the careful way until it is asked.

    Where a named person is holding stock is a fact about that person, and
    nobody has yet decided it may be published. The serializer's docstring says
    why this is not `PUBLIC_VOLUNTEER_DETAILS`'s to govern.
    """
    body = anybody.get(reverse("locations")).json()
    rows = body["results"] if isinstance(body, dict) else body
    held = next(row for row in rows if row["id"] == custody.pk)

    assert "held_by" not in held, (
        "an anonymous caller is told which volunteer is holding stock, which is where a named person "
        "is right now and is nobody's decision to publish yet"
    )


def test_a_signed_in_caller_still_sees_who_is_holding_it(client: Client, custody: Location) -> None:
    """The administrator reconciling stock needs exactly what they needed before."""
    body = client.get(reverse("locations")).json()
    rows = body["results"] if isinstance(body, dict) else body
    held = next(row for row in rows if row["id"] == custody.pk)

    assert held["held_by"] == custody.held_by.pk


# ---------------------------------------------------------------------------
# The setting itself
# ---------------------------------------------------------------------------


def test_a_word_this_setting_does_not_know_stops_the_application() -> None:
    """Refused rather than defaulted, and `access.chosen` says which way that cuts."""
    with pytest.raises(ValueError, match="VOLUNTEER_ACCESS='opened'"):
        access.chosen("opened")


def test_what_the_environment_says_is_what_the_setting_becomes() -> None:
    """Declared, and also READ, which the tests above cannot tell apart.

    They assign the Django setting, so a schema entry wired to nothing would
    leave every real deployment closed with all of them green.
    test_trusted_origins.py argues the reload technique.
    """
    module = importlib.import_module("inventory_tng.settings")

    try:
        with pytest.MonkeyPatch.context() as patched:
            patched.setenv(SETTING, access.OPEN)
            read = importlib.reload(module).VOLUNTEER_ACCESS
    finally:
        importlib.reload(module)

    assert read == access.OPEN, (
        f"{SETTING} is declared but nothing consumes it, so an operator who opened this for a "
        f"demonstration is quietly ignored; the module read {read!r}"
    )


def test_it_says_so_when_the_surface_is_open() -> None:
    said = access.announcement(access.OPEN)

    assert SETTING in said, "the line does not name the setting, so a reader cannot tell what to change"
    assert "rate limit" in said, (
        "the line no longer says that anonymous reads are unlimited, which is the part an operator "
        "will not already know from the setting's name"
    )


def test_it_says_nothing_when_a_volunteer_signs_in() -> None:
    """Decision 0021 point 5 is about adaptation, not about narrating normality."""
    assert access.announcement(access.SESSION) == ""


# ---------------------------------------------------------------------------
# Where the value is written down
# ---------------------------------------------------------------------------
