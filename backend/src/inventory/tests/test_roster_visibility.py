"""What a caller with no account may read about a volunteer.

`inventory_tng.roster` carries the argument, and `inventory-tng-81f7` is the
open question behind it. Neither is repeated here.

What is held is that the default is the careful one, that a signed-in caller is
unaffected, and that the setting reaches the response at all -- tested now,
while every endpoint still requires a session, because the rule is worth
writing before the door opens rather than after.
"""

import importlib
from pathlib import Path

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.test import Client, RequestFactory
from django.urls import reverse
from environ import Env
from pytest_django.fixtures import Settings

from inventory.models import Volunteer
from inventory.serializers import VolunteerSerializer
from inventory.tests import charts
from inventory.tests.helpers import shipped
from inventory_tng import roster

SETTING = "PUBLIC_VOLUNTEER_DETAILS"
# The pair that decides what a stack actually runs with; test_trusted_origins.py
# argues it.
SHIPPED = ("compose.yaml", ".env.sample")
# The chart value behind the variable, and the two example files with what each
# is expected to have chosen. test_second_factor.py keeps the same pair for the
# other setting these files differ on.
VALUE = "django.publicVolunteerDetails"
EXAMPLES = {"onboarding.yaml": "true", "real-data.yaml": "false"}


@pytest.fixture
def someone(db: None) -> Volunteer:
    return Volunteer.objects.create(
        display_name="Sean Delaney",
        email="sean@example.invalid",
        slack_id="U04SEAN",
    )


def as_seen_by(volunteer: Volunteer, *, signed_in: bool) -> dict[str, object]:
    """The row as a caller of that kind would receive it.

    Not called `rendered`, though it is the obvious name: `charts.rendered`
    already means "what the chart puts in the pod's environment" everywhere
    in this suite, and this module asks it both questions.
    """
    request = RequestFactory().get("/api/volunteers")
    request.user = User(username="editor") if signed_in else AnonymousUser()
    return dict(VolunteerSerializer(volunteer, context={"request": request}).data)


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


def test_the_default_withholds_contact_details() -> None:
    """A deployment that configures nothing does not publish a roster.

    Asked of the DECLARED default rather than the loaded setting, for the
    reason test_second_factor.py gives: settings.py reads a developer's own
    `.env`, so an assertion about the loaded value fails on the laptop of
    anybody who copied the sample.
    """
    declared = importlib.import_module("inventory_tng.settings").env.scheme[SETTING]

    assert declared == (bool, False), (
        "the code default now publishes volunteer contact details, so a deployment that configures "
        f"nothing hands out a name-to-address list. It declares {declared}"
    )


def test_an_anonymous_caller_gets_a_name_and_no_way_to_reach_anybody(someone: Volunteer) -> None:
    row = as_seen_by(someone, signed_in=False)

    assert row["display_name"] == "Sean Delaney", "the name goes, so the picker cannot work at all"
    assert "email" not in row
    assert "slack_id" not in row


def test_a_signed_in_caller_is_unaffected(someone: Volunteer) -> None:
    """Nothing about the administrator's view changes, and that is deliberate.

    The question this setting answers is about people with no account. An
    administrator merging two Seans needs exactly what they needed before.
    """
    row = as_seen_by(someone, signed_in=True)

    assert row["email"] == "sean@example.invalid"
    assert row["slack_id"] == "U04SEAN"


def test_a_deployment_may_publish_them(someone: Volunteer, settings: Settings) -> None:
    """The demo case, and the only reason the setting exists."""
    settings.PUBLIC_VOLUNTEER_DETAILS = True

    row = as_seen_by(someone, signed_in=False)

    assert row["email"] == "sean@example.invalid", (
        "an operator turned the setting on and the response still withholds, so the setting is "
        "declared rather than honoured"
    )


def test_no_request_is_treated_as_anonymous(someone: Volunteer) -> None:
    """A serializer used outside a view cannot be asked who is calling.

    A command, a shell or a test renders volunteers with no request in context,
    and the safe reading of "I do not know" is the careful one. This is what
    fails if somebody makes the unknown case permissive.
    """
    row = dict(VolunteerSerializer(someone).data)

    assert "email" not in row


# ---------------------------------------------------------------------------
# That it is still reachable the way the application uses it
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_endpoint_still_answers_a_signed_in_caller_in_full(client: Client, someone: Volunteer) -> None:
    """Through the wire rather than the serializer, because the view supplies the context.

    A serializer that reads `self.context["request"]` is only correct if the
    view actually puts one there, and asserting on the serializer alone would
    not notice if it stopped.
    """
    answer = client.get(reverse("volunteers"))
    body = answer.json()
    rows = body["results"] if isinstance(body, dict) else body
    mine = next(row for row in rows if row["display_name"] == "Sean Delaney")

    assert mine["email"] == "sean@example.invalid"


# ---------------------------------------------------------------------------
# The announcement, and where the value is written down
# ---------------------------------------------------------------------------


def test_it_says_so_when_the_roster_is_public() -> None:
    said = roster.announcement(public=True)

    assert SETTING in said, "the line does not name the setting, so a reader cannot tell what to change"


def test_it_says_nothing_when_the_roster_is_private() -> None:
    """Decision 0021 point 5 is about adaptation, not about narrating normality."""
    assert roster.announcement(public=False) == ""


@pytest.mark.parametrize("a_file", SHIPPED)
def test_every_shipped_configuration_says_so_rather_than_implying_it(a_file: str) -> None:
    assert SETTING in shipped(Path(a_file)), (
        f"{a_file} does not mention {SETTING}, so what a fresh clone runs with is whatever the code "
        "fell back to rather than something this repository chose"
    )


def test_what_the_environment_says_is_what_the_setting_becomes() -> None:
    """Declared, and also read.

    A schema entry nothing consumes withholds from every deployment while
    every test above stays green, because those assign the Django setting
    directly and never go near the variable. Reloading the module with it set
    is the only thing that tells a wired setting from a decorative one --
    test_trusted_origins.py is where that technique is argued.
    """
    module = importlib.import_module("inventory_tng.settings")

    try:
        with pytest.MonkeyPatch.context() as patched:
            patched.setenv(SETTING, "true")
            read = importlib.reload(module).PUBLIC_VOLUNTEER_DETAILS
    finally:
        importlib.reload(module)

    assert read is True, (
        f"{SETTING} is declared but nothing consumes it, so the operator who set it for a demonstration "
        f"is quietly ignored; the module read {read!r}"
    )


@pytest.mark.parametrize(("example", "expected"), EXAMPLES.items())
def test_each_example_states_the_answer_it_is_named_for(example: str, expected: str) -> None:
    """Both starting points choose, and they choose oppositely.

    An example that dropped the line would inherit the careful answer and look
    fine; the one that would actually hurt is `onboarding.yaml` losing it and
    a demonstration quietly withholding, because then nobody is reminded the
    choice was ever made and the line gets added back somewhere worse.
    """
    text = shipped(Path("infra/helm/inventory-tng/examples") / example)

    assert f"publicVolunteerDetails: {expected}" in text, (
        f"{example} no longer says publicVolunteerDetails: {expected}, so the starting point named for "
        "this answer does not carry it"
    )


def test_the_chart_renders_it_and_withholds_by_default() -> None:
    supplied = charts.rendered()

    assert SETTING in supplied, (
        f"the chart does not put {SETTING} in the backend's environment, so a cluster has no way to make "
        "this choice without editing the chart"
    )
    assert supplied[SETTING]["value"] == "false", (
        "the chart's default now publishes the roster, so a cluster whose operator read nothing hands out "
        "every volunteer's email address"
    )


def test_the_chart_carries_the_operator_answer_rather_than_a_constant() -> None:
    """A template that hard-coded the safe value would pass the test above.

    That is the shape worth one more render: withholding by default and
    ignoring the operator looks identical to withholding by default and
    honouring them, right up to the demonstration that will not publish.
    """
    supplied = charts.rendered(**{VALUE: "true"})

    read = Env.parse_value(supplied[SETTING]["value"], bool)

    assert read is True, (
        f"the chart renders {SETTING} without reading {VALUE}, or in a shape Django does not read back "
        f"as on, so an operator's answer never reaches the application; it read {read!r}"
    )
