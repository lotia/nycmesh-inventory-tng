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

from typing import Any

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from inventory.models import Category, Item, Location, Volunteer

USERNAME = "integration"
PASSWORD = "integration-only-not-a-real-password"


class Command(BaseCommand):
    help = "Create the fixed login, volunteer, item and location the integration tests use."

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG:
            raise CommandError("Refusing to seed: this creates a login with a published password.")

        User.objects.filter(username=USERNAME).delete()
        # A superuser because the admin's login form is the only way into a
        # session that exists today; the app's own is inventory-tng-0pj.
        User.objects.create_superuser(username=USERNAME, password=PASSWORD)

        volunteer, _ = Volunteer.objects.get_or_create(display_name="Integration Tester")
        category, _ = Category.objects.get_or_create(name="Radios")
        item, _ = Item.objects.get_or_create(name="LiteBeam", defaults={"category": category})
        warehouse, _ = Location.objects.get_or_create(
            name="131 Broome",
            defaults={"kind": Location.Kind.WAREHOUSE},
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded: user={USERNAME} volunteer={volunteer.pk} item={item.pk} location={warehouse.pk}",
            ),
        )
