"""Tests for the integration-test seed command.

The Playwright tests depend on this scene existing and on being able to run
twice in a row, so the two properties worth pinning are that it creates what
they expect and that running it again changes nothing. The rest of the file is
about the login it creates: what has to be true before it is created, and what
is left behind if creating it fails half way.
"""

import io
import json
from typing import Any, NoReturn
from unittest.mock import ANY

import pyotp
import pytest
from allauth.mfa.models import Authenticator
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, override_settings
from django.urls import reverse

from inventory.management.commands.seed_integration_data import ACKNOWLEDGEMENT
from inventory.models import Category, Item, Location, Volunteer
from inventory.tests.helpers import applied
from inventory_tng.logs import logging_config

pytestmark = pytest.mark.django_db


def run() -> str:
    """Run the command as the integration suite does, returning its stdout."""
    out = io.StringIO()
    call_command("seed_integration_data", ACKNOWLEDGEMENT, stdout=out)
    return out.getvalue()


def seed() -> dict[str, Any]:
    """Run the command and read back the scene it published on stdout."""
    return json.loads(run())


@override_settings(DEBUG=True)
def test_nothing_else_reaches_the_stream_the_scene_is_published_on() -> None:
    """`global-setup.ts` calls `JSON.parse` on the whole of this command's
    standard output, and in a deployment the log stream is that same file.

    So a single record written while this runs is not a stray line -- it is the
    browser suite failing in global setup with a `SyntaxError` naming column 3,
    which says nothing about where it came from. That is what happened when the
    retroactive sweep put every command under `_telemetry.running`; the command
    says why it is the one exception.
    """
    written = io.StringIO()

    with applied(logging_config("INFO", "console")) as logged:
        call_command("seed_integration_data", ACKNOWLEDGEMENT, stdout=written)

    assert json.loads(written.getvalue())["username"] == "integration"
    assert logged.getvalue() == "", f"this would have been parsed as part of the scene: {logged.getvalue()!r}"


@override_settings(DEBUG=True)
def test_it_creates_the_scene_and_running_it_again_changes_nothing() -> None:
    """The suite reseeds on every run, including against a database an earlier
    run already touched, so creating and re-creating are one property.
    """
    run()
    run()

    assert User.objects.filter(username="integration", is_superuser=True).count() == 1
    assert Volunteer.objects.filter(display_name="Integration Tester").count() == 1
    assert Item.objects.filter(name="LiteBeam").count() == 1
    assert Location.objects.filter(name="131 Broome").count() == 1


@override_settings(DEBUG=True)
def test_the_seeded_volunteer_is_one_the_api_will_accept() -> None:
    """A retired row still carries the name, and its pk would fail every write
    the browser suite makes.
    """
    retired = Volunteer.objects.create(display_name="Integration Tester", active=False)

    seeded = seed()

    assert seeded["volunteer"] != retired.pk
    assert Volunteer.objects.selectable().filter(pk=seeded["volunteer"]).exists()


@override_settings(DEBUG=True)
def test_every_published_name_belongs_to_the_row_published_beside_it() -> None:
    """The browser suite asserts on what a volunteer reads on screen, so the
    names come from here rather than being written down a second time there. A
    name that drifted from its own row would reach that suite as a timeout with
    nothing to say why.
    """
    scene = seed()

    assert scene["item_name"] == Item.objects.get(pk=scene["item"]).name
    assert scene["volunteer_name"] == Volunteer.objects.get(pk=scene["volunteer"]).display_name


@override_settings(DEBUG=True)
def test_a_duplicate_name_does_not_stop_the_seed() -> None:
    """More than one row can carry this name; the lookup must not assume one."""
    Volunteer.objects.create(display_name="Integration Tester")
    Volunteer.objects.create(display_name="Integration Tester")

    assert Volunteer.objects.selectable().filter(pk=seed()["volunteer"]).exists()


@override_settings(DEBUG=False)
def test_it_refuses_to_run_where_this_is_not_a_development_server() -> None:
    """Acknowledging it is not enough: the flag says you meant to, not that the
    server you are pointed at is one to mean it about.
    """
    with pytest.raises(CommandError):
        call_command("seed_integration_data", ACKNOWLEDGEMENT)

    assert not User.objects.filter(username="integration").exists()


@override_settings(DEBUG=True)
def test_it_refuses_to_run_unacknowledged() -> None:
    """DEBUG is a Helm value, so a cluster can legitimately have it on while
    this command sits in its backend image. It cannot be the only lock.
    """
    with pytest.raises(CommandError):
        call_command("seed_integration_data")

    assert not User.objects.filter(username="integration").exists()


@override_settings(DEBUG=True)
def test_a_failed_seed_leaves_the_previous_login_alone() -> None:
    """The login is deleted before it is recreated. If what follows can fail
    unprotected, the suite's next run has nothing to log in with and reports it
    as a broken login form rather than a broken seed.
    """
    run()
    before = User.objects.get(username="integration")

    def explode(**kwargs: Any) -> NoReturn:
        raise RuntimeError("boom")

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(Item.objects, "get_or_create", explode)
        with pytest.raises(RuntimeError):
            run()

    after = User.objects.get(username="integration")
    assert after.pk == before.pk
    assert after.password == before.password


@override_settings(DEBUG=True)
def test_a_retired_item_or_location_is_revived_rather_than_published_retired() -> None:
    """An item name is unique outright, so a retired row IS the row.

    The command's comment beside the reactivation says what leaving it retired
    would publish.
    """
    category = Category.objects.create(name="Radios")
    retired_item = Item.objects.create(name="LiteBeam", category=category, active=False)
    retired_place = Location.objects.create(
        name="131 Broome",
        kind=Location.Kind.WAREHOUSE,
        active=False,
    )

    scene = seed()

    assert scene["item"] == retired_item.pk
    assert scene["location"] == retired_place.pk
    assert Item.objects.get(pk=scene["item"]).active is True
    assert Location.objects.get(pk=scene["location"]).active is True


@override_settings(DEBUG=True)
def test_the_seeded_login_can_complete_the_second_factor_it_publishes() -> None:
    """The published secret has to be a working one, not a plausible string.

    Decision 0013 point 3 puts a second factor in front of the local password
    path, and the browser suite takes that path because it is the only one it
    can complete. So this scene is only a usable scene if the code computed
    from what it prints actually gets a session -- which is the whole of the
    integration suite's ability to sign in, asserted here where the failure
    says why rather than minutes later at a login form.
    """
    scene = seed()

    client = Client()
    client.post(reverse("account_login"), {"login": scene["username"], "password": scene["password"]})
    client.post(reverse("mfa_authenticate"), {"code": pyotp.TOTP(scene["totp_secret"]).now()})

    assert client.get(reverse("me")).json() == {
        "authenticated": True,
        "username": scene["username"],
        "administrator": True,
        "recently_authenticated": True,
        "device": "none",
        "capabilities": ANY,
    }


@override_settings(DEBUG=True)
def test_the_seeded_login_has_recovery_codes_too() -> None:
    """Point 3 asks for both, and a login with only one of them is a login
    that cannot be recovered when the phone holding the other is lost.
    """
    run()

    login = User.objects.get(username="integration")
    assert Authenticator.objects.filter(user=login, type=Authenticator.Type.TOTP).exists()
    assert Authenticator.objects.filter(user=login, type=Authenticator.Type.RECOVERY_CODES).exists()


@override_settings(DEBUG=True)
def test_reseeding_replaces_the_second_factor_along_with_the_password() -> None:
    """The login is replaced outright so its credentials are the published ones.

    Its authenticators have to go with it: a stale key left behind by an
    earlier run would be a second one the suite has no secret for, and
    allauth would happily accept a code from either.
    """
    run()
    run()

    login = User.objects.get(username="integration")
    assert Authenticator.objects.filter(user=login, type=Authenticator.Type.TOTP).count() == 1
