"""Tests for the integration-test seed command.

The Playwright tests depend on this scene existing and on being able to run
twice in a row, so the two properties worth pinning are that it creates what
they expect and that running it again changes nothing.
"""

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from inventory.models import Item, Location, Volunteer

pytestmark = pytest.mark.django_db


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


@override_settings(DEBUG=False)
def test_it_refuses_to_run_outside_development() -> None:
    """It creates a login whose password is written down in this repository."""
    with pytest.raises(CommandError):
        call_command("seed_integration_data")

    assert not User.objects.filter(username="integration").exists()
