"""Tests for the integration-test seed command.

The Playwright tests depend on this scene existing and on being able to run
twice in a row, so the two properties worth pinning are that it creates what
they expect and that running it again changes nothing.
"""

import io
import json
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from inventory.models import Item, Location, Volunteer

pytestmark = pytest.mark.django_db


def seed() -> dict[str, Any]:
    """Run the command and read back the scene it published on stdout."""
    out = io.StringIO()
    call_command("seed_integration_data", stdout=out)
    return json.loads(out.getvalue())


@override_settings(DEBUG=True)
def test_it_creates_the_scene_and_running_it_again_changes_nothing() -> None:
    """The suite reseeds on every run, including against a database an earlier
    run already touched, so creating and re-creating are one property.
    """
    call_command("seed_integration_data")
    call_command("seed_integration_data")

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
def test_a_duplicate_name_does_not_stop_the_seed() -> None:
    """More than one row can carry this name; the lookup must not assume one."""
    Volunteer.objects.create(display_name="Integration Tester")
    Volunteer.objects.create(display_name="Integration Tester")

    assert Volunteer.objects.selectable().filter(pk=seed()["volunteer"]).exists()


@override_settings(DEBUG=False)
def test_it_refuses_to_run_outside_development() -> None:
    """It creates a login whose password is written down in this repository."""
    with pytest.raises(CommandError):
        call_command("seed_integration_data")

    assert not User.objects.filter(username="integration").exists()
