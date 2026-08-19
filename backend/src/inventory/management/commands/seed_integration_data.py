"""Seed the fixed scene the Playwright integration tests expect.

The integration tests drive a real browser against a real server, so they
cannot create their rows through a test fixture the way the unit tests do.
This command gives them a known starting point and is safe to run repeatedly:
the volunteer, item and location are fetched or created rather than
duplicated, and the login is replaced outright so that its password is the one
below whatever an earlier run or a developer left behind.

It refuses to run unless DEBUG is on, because it creates a login with a
published password.
"""

import json
from typing import Any

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from inventory.models import Category, Item, Location, Volunteer

USERNAME = "integration"
PASSWORD = "integration-only-not-a-real-password"
VOLUNTEER = "Integration Tester"


class Command(BaseCommand):
    help = "Create the fixed login, volunteer, item and location the integration tests use."

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG:
            raise CommandError("Refusing to seed: this creates a login with a published password.")

        User.objects.filter(username=USERNAME).delete()
        # A superuser because the admin's login form is the only way into a
        # session that exists today; the app's own is inventory-tng-0pj.
        User.objects.create_superuser(username=USERNAME, password=PASSWORD)

        # Not get_or_create: display names are not unique (see Volunteer), so a
        # second row carrying this one would make it raise. It must also come
        # back selectable, because this runs against a developer's own
        # database where the record may since have been merged or deactivated,
        # and the batch endpoint refuses a retired actor.
        volunteer = Volunteer.objects.selectable().filter(display_name=VOLUNTEER).first()
        if volunteer is None:
            volunteer = Volunteer.objects.create(display_name=VOLUNTEER)

        category, _ = Category.objects.get_or_create(name="Radios")
        item, _ = Item.objects.get_or_create(name="LiteBeam", defaults={"category": category})
        warehouse, _ = Location.objects.get_or_create(
            name="131 Broome",
            defaults={"kind": Location.Kind.WAREHOUSE},
        )

        # JSON on stdout; frontend/integration/global-setup.ts republishes it.
        self.stdout.write(
            json.dumps(
                {
                    "username": USERNAME,
                    "password": PASSWORD,
                    "volunteer": volunteer.pk,
                    "item": item.pk,
                    "location": warehouse.pk,
                },
            ),
        )
