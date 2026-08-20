"""Seed the fixed scene the Playwright integration tests expect.

The integration tests drive a real browser against a real server, so they
cannot create their rows through a test fixture the way the unit tests do.
This command gives them a known starting point and is safe to run repeatedly:
the volunteer, item and location are fetched or created rather than
duplicated, and the login is replaced outright so that its password is the one
below whatever an earlier run or a developer left behind.

It creates a login whose password AND whose second factor are written down in
this repository, so it runs only on a development server that was also told,
in the command itself, that this is what was wanted. Both conditions are
checked below.

The second factor is not a concession to the test suite -- it is the point.
Decision 0013 requires one on the local password path, and the browser suite
signs in that way because an OAuth round trip to Google or Slack cannot be
completed from CI. Publishing the TOTP secret lets the suite compute a real
code, so what it exercises is the second factor rather than an exemption from
it.
"""

import json
from typing import Any

from allauth.mfa.recovery_codes.internal.auth import RecoveryCodes
from allauth.mfa.totp.internal.auth import TOTP
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from inventory.models import Category, Item, Location, Volunteer

USERNAME = "integration"
PASSWORD = "integration-only-not-a-real-password"
# Base32, because that is what an authenticator app is handed and what the
# suite feeds to pyotp. Fixed rather than generated so that a developer can
# also scan it into a phone once and keep signing in by hand.
TOTP_SECRET = "INTEGRATIONTESTSECRETNOTAREALKEY"
VOLUNTEER = "Integration Tester"

ACKNOWLEDGEMENT = "--i-know-this-creates-a-published-login"
ACKNOWLEDGEMENT_DEST = ACKNOWLEDGEMENT.removeprefix("--").replace("-", "_")


class Command(BaseCommand):
    help = "Create the fixed login, volunteer, item and location the integration tests use."

    def add_arguments(self, parser: CommandParser) -> None:
        # A flag rather than an environment variable, and the choice is the
        # whole point of the guard. An environment variable would repeat the
        # mistake it replaces: DEBUG is ambient, so one Helm value switches the
        # lock off for every invocation in that cluster from then on, and
        # nobody running the command afterwards has to know it did. A flag is
        # spent per invocation, cannot be turned on in advance by
        # configuration, and shows up in the command that ran. Being
        # non-interactive costs nothing either way -- Playwright's global setup
        # passes it as an ordinary argument.
        parser.add_argument(
            ACKNOWLEDGEMENT,
            action="store_true",
            help="Required. Confirms you meant to create a login whose password is published in this repository.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        # Two conditions saying two different things, and neither implies the
        # other. DEBUG says this is a development server. The flag says you
        # meant to put a published credential on it. DEBUG on its own was the
        # bug: it is a Helm value, so a cluster can legitimately run with it
        # true, and this command ships inside the backend image.
        if not settings.DEBUG:
            raise CommandError("Refusing to seed: DEBUG is off, so this is not a development server.")
        if not options[ACKNOWLEDGEMENT_DEST]:
            raise CommandError(
                f"Refusing to seed: this creates the login {USERNAME!r}, whose password and second factor are both "
                f"written down in this repository. Pass {ACKNOWLEDGEMENT} if that is what you want.",
            )

        # One transaction, because the login is deleted before it is recreated.
        # A failure in between would leave the suite with no login at all, and
        # it would then fail at the login form minutes later rather than here.
        with transaction.atomic():
            User.objects.filter(username=USERNAME).delete()
            # A superuser, so the suite can exercise the administrative half
            # of the interface as well as the volunteer half.
            login = User.objects.create_superuser(username=USERNAME, password=PASSWORD)
            # Both authenticators, because both are what decision 0013 point 3
            # requires of a local password. Created here rather than through
            # allauth's activation page: the suite needs the secret to be the
            # one written above, and the page generates its own.
            TOTP.activate(login, TOTP_SECRET)
            RecoveryCodes.activate(login)

            # Not get_or_create: display names are not unique (see Volunteer),
            # so a second row carrying this one would make it raise. It must
            # also come back selectable, because this runs against a
            # developer's own database where the record may since have been
            # merged or deactivated, and the batch endpoint refuses a retired
            # actor.
            volunteer = Volunteer.objects.selectable().filter(display_name=VOLUNTEER).first()
            if volunteer is None:
                volunteer = Volunteer.objects.create(display_name=VOLUNTEER)

            # parent=None is part of the lookup, not decoration: a category or
            # location name is unique only *within* its parent, so matching on
            # the name alone would raise MultipleObjectsReturned against a
            # developer's database that happens to nest one of these names
            # under something.
            category, _ = Category.objects.get_or_create(name="Radios", parent=None)
            item, _ = Item.objects.get_or_create(name="LiteBeam", defaults={"category": category})
            warehouse, _ = Location.objects.get_or_create(
                name="131 Broome",
                parent=None,
                defaults={"kind": Location.Kind.WAREHOUSE},
            )
            # Reactivated rather than stepped over, unlike the volunteer above:
            # an item name is unique outright and a location name is unique
            # within its parent, so a retired row carrying this name IS this
            # row and no second one can be made. Left retired it would be
            # published as a scene the read API refuses to offer, and the first
            # test to read a pick-list would fail for a reason that is not a
            # bug.
            for row in (item, warehouse):
                if not row.active:
                    row.active = True
                    row.save(update_fields=["active"])

            scene = {
                "username": USERNAME,
                "password": PASSWORD,
                # The suite computes a code from this at the moment it signs
                # in; a code seeded here would have expired by then.
                "totp_secret": TOTP_SECRET,
                "volunteer": volunteer.pk,
                # The name as well as the id: the pick-list is paginated, so a
                # test that wants this volunteer back out of it has to search
                # for them, and the name belongs here rather than copied into
                # the browser suite.
                "volunteer_name": volunteer.display_name,
                "item": item.pk,
                # The name as well, for the same reason the volunteer's is
                # here: the browser suite asserts on what a volunteer reads on
                # screen, and a name it kept its own copy of would survive a
                # rename here as a decode timeout with nothing to say why.
                "item_name": item.name,
                "location": warehouse.pk,
            }

        # Printed after the commit, so nothing describes a scene that rolled
        # back. JSON on stdout; frontend/integration/global-setup.ts
        # republishes it.
        self.stdout.write(json.dumps(scene))
