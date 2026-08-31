"""Whether a second factor is required, held to being an operator's answer.

Decision 0013 point 3's amendment turned a rule into a default. The risk in
that change is not that the switch fails to turn the requirement off -- that
part is one `if` and would be noticed immediately. It is the two quieter ways
the amendment could be undone while every obvious test still passed:

- **Off becomes gone.** If turning the requirement off also removed the
  machinery, then turning it back on later would stop being a values change,
  and the whole "adoption first, then the nudge" argument would be a sentence
  in a document with nothing behind it. `test_off_does_not_mean_gone` and its
  neighbour are that half.
- **It quietly takes the admin step-up with it.** Both middleware classes are
  named for asking something extra, and wiring the setting to both is the
  obvious mistake. They answer different threats;
  `RequireSecondLookInTheAdmin` says why where it lives, and the test here is
  what would fail if somebody wired them together.

The plain cases -- refused when on, admitted when off -- are here too, because
a module that only tested the subtle things would pass with the feature
removed.
"""

import importlib

import pyotp
import pytest
from allauth.mfa.models import Authenticator
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from environ import Env
from pytest_django.fixtures import Settings

from inventory.tests.charts import rendered
from inventory.tests.conftest import ADMINISTRATOR_PASSWORD as PASSWORD
from inventory.tests.helpers import activate_totp, start_local_sign_in, wind_back
from inventory_tng import second_factor

SETTING = "REQUIRE_SECOND_FACTOR"


# ---------------------------------------------------------------------------
# The requirement itself
# ---------------------------------------------------------------------------


def test_the_default_is_the_careful_one() -> None:
    """A deployment that says nothing gets the requirement.

    Decision 0021 point 11's pattern: the safe value in the code, the intended
    one in every shipped file, so the case nobody thought about is the one that
    asks.

    Asked of the DECLARED default rather than of `settings.REQUIRE_SECOND_FACTOR`,
    and that is not pedantry. This is the one setting whose shipped local value
    deliberately disagrees with its code default, and settings.py reads a
    developer's own `.env` when there is one -- so anybody who copied
    `.env.sample`, which is everybody the instructions reach, would fail an
    assertion about the loaded value while the code was entirely correct. A
    test that fails on a contributor's laptop and passes in CI teaches people
    to ignore it.
    """
    declared = importlib.import_module("inventory_tng.settings").env.scheme[SETTING]

    assert declared == (bool, True), (
        "the code default no longer requires a second factor, so a deployment that configures nothing "
        f"silently accepts password-only accounts -- the opposite of the amendment's point 1. It declares {declared}"
    )


@pytest.mark.django_db
def test_a_password_alone_is_refused_when_it_is_required(administrator: User, settings: Settings) -> None:
    settings.REQUIRE_SECOND_FACTOR = True

    refused = start_local_sign_in(administrator, PASSWORD).get(reverse("items"))

    assert refused.status_code == 403
    assert "second factor" in refused.json()["detail"]


@pytest.mark.django_db
def test_a_password_alone_is_enough_when_it_is_not(administrator: User, settings: Settings) -> None:
    """The operator's answer, honoured. No environment gate reaches this."""
    settings.REQUIRE_SECOND_FACTOR = False

    admitted = start_local_sign_in(administrator, PASSWORD).get(reverse("items"))

    assert admitted.status_code == 200, (
        "an operator turned the requirement off and the middleware refused anyway, so the setting is "
        "declared rather than honoured"
    )


# ---------------------------------------------------------------------------
# Off does not mean gone, which is what makes turning it on later a nudge
# ---------------------------------------------------------------------------


def test_the_machinery_stays_installed_whatever_the_answer_is(settings: Settings) -> None:
    """`MFA_SUPPORTED_TYPES` is deliberately not conditional on the setting."""
    settings.REQUIRE_SECOND_FACTOR = False

    assert "totp" in settings.MFA_SUPPORTED_TYPES
    assert "allauth.mfa" in settings.INSTALLED_APPS


@pytest.mark.django_db
def test_somebody_may_still_enrol_where_nobody_has_to(administrator: User, settings: Settings) -> None:
    """A volunteer who wants a second factor gets one on a deployment without.

    Driven through the activation page rather than the model, the way
    test_sign_in.py does it, so what is proved is that the flow a person meets
    still works rather than that the table can hold a row.
    """
    settings.REQUIRE_SECOND_FACTOR = False
    client = start_local_sign_in(administrator, PASSWORD)

    page = client.get(reverse("mfa_activate_totp"))
    secret = page.context["form"].secret
    client.post(reverse("mfa_activate_totp"), {"code": pyotp.TOTP(secret).now()})

    assert page.status_code == 200, "the enrolment page is unreachable where the requirement is off"
    assert Authenticator.objects.filter(user=administrator, type=Authenticator.Type.TOTP).exists(), (
        "enrolment does not complete where the requirement is off, so turning the requirement on later "
        "is a coordinated enrolment day rather than a values change"
    )


@pytest.mark.django_db
def test_a_second_factor_already_set_up_is_still_asked_for(administrator: User, settings: Settings) -> None:
    """Optional for the account that has none; unchanged for the account that has one.

    This is what stops "not required" from meaning "not used". allauth's own
    authenticate stage owns this, and the assertion is that nothing here
    disabled it: a password alone must not finish a sign-in for somebody who
    enrolled.
    """
    settings.REQUIRE_SECOND_FACTOR = False
    activate_totp(administrator)

    client = Client()
    accepted = client.post(reverse("account_login"), {"login": administrator.get_username(), "password": PASSWORD})

    # The password was right AND the sign-in is not finished: allauth held it
    # at its own challenge. Asserted rather than inferred from the refusal
    # below, because "not signed in" is equally true of a rejected password, a
    # throttled attempt and a 404 -- each of which would satisfy that refusal
    # while proving nothing at all about the second factor.
    assert accepted.status_code == 302
    assert accepted["Location"] == reverse("mfa_authenticate"), (
        f"a correct password did not reach the second-factor challenge; it went to {accepted['Location']}"
    )
    assert client.get(reverse("items")).status_code != 200, (
        "an account that enrolled a second factor was signed in on the password alone, so turning the "
        "requirement off weakened the accounts that had already met it"
    )


# ---------------------------------------------------------------------------
# What the setting must NOT reach
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_admin_step_up_is_not_wired_to_this(administrator: User, settings: Settings) -> None:
    """Decision 0014 point 5 is a separate control and stays on.

    It answers a different threat, which decision 0013's amendment names and
    `RequireSecondLookInTheAdmin` argues where it lives. An operator saying
    enrolment is not compulsory is not saying the admin should take a write
    from a session that has not proved itself recently -- so a change that
    gates that class on this setting is a security regression wearing a
    feature's clothes, and this is what fails.

    The session is a password-only one, wound back the way conftest's `stale`
    does it, because the whole point is the account this setting is about:
    signed in with a password, no second factor, on a deployment that does not
    ask for one.
    """
    settings.REQUIRE_SECOND_FACTOR = False
    client = wind_back(start_local_sign_in(administrator, PASSWORD))

    written = client.post(f"{reverse('admin:index')}auth/user/add/", {})

    assert written.status_code == 302
    assert reverse("account_reauthenticate") in written["Location"], (
        "an administrative write went through without a recent sign-in because the second-factor setting "
        "was off; the two controls have been wired together and decision 0014 point 5 is gone"
    )


# ---------------------------------------------------------------------------
# The announcement, which is the whole of the nudge
# ---------------------------------------------------------------------------


def test_it_says_so_when_the_requirement_is_off() -> None:
    said = second_factor.announcement(required=False)

    assert SETTING in said, (
        "the line printed when the requirement is off does not name the setting, so an operator reading "
        "it cannot tell what to change"
    )


def test_it_says_nothing_when_the_requirement_is_on() -> None:
    """Decision 0021 point 5 is about adaptation, not about narrating normality.

    A process that announces its ordinary configuration teaches its reader to
    skip the first lines, and then the line that matters goes past unread.
    """
    assert second_factor.announcement(required=True) == ""


# ---------------------------------------------------------------------------
# Reaching Django, and reaching the pod
# ---------------------------------------------------------------------------


def test_what_the_environment_says_is_what_the_setting_becomes() -> None:
    """Declared is not the same as read.

    A schema entry with nothing consuming it would leave the requirement on
    for every deployment while every behavioural test above still passed,
    because those set the Django setting directly. Only re-reading the module
    with the variable in the environment tells the two apart; test_trusted_origins.py
    says more about why it is done this way.
    """
    module = importlib.import_module("inventory_tng.settings")

    try:
        with pytest.MonkeyPatch.context() as patched:
            patched.setenv(SETTING, "false")
            read = importlib.reload(module).REQUIRE_SECOND_FACTOR
    finally:
        importlib.reload(module)

    assert read is False, (
        f"{SETTING} is declared but nothing consumes it, so an operator who sets it is quietly ignored "
        f"and the requirement stays on; the module read {read!r}"
    )


def test_what_an_operator_sets_is_what_django_would_read() -> None:
    """The chart's rendering and Django's parsing, held to one answer.

    Helm quotes a boolean into a string and Django parses it back, and the two
    have to agree on what `false` means. A chart that rendered `False` or `0`
    would still look correct in a diff and would still be read as true by
    something, which is the failure worth a test rather than a glance.
    """
    supplied = rendered(**{"django.requireSecondFactor": "false"})

    read = Env.parse_value(supplied[SETTING]["value"], bool)

    assert read is False, (
        f"the chart renders {SETTING} in a shape Django does not read back as off, so an operator who "
        f"turned the requirement off still has it on; it read {read!r}"
    )
