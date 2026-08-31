"""The first administrator, made at deploy time and safe to run again.

A cluster starts with an empty `auth_user`, so nobody can reach the admin, and
nobody can be invited because there is nobody to invite them. Django's own
`createsuperuser --noinput` solves the first half and not the second: it
FAILS when the username already exists, so a deployment that re-runs it -- a
rolled pod, a re-applied Job, an init container that restarts -- reports a
failed deploy for a system that is in exactly the right state.

This is that command made idempotent, which is the whole of what it adds.
`inventory-tng-s8dk`.

## An ordinary administrator, deliberately

The project owner's requirement, and it is satisfied by construction rather
than by care: this writes a plain `User` with `is_staff` and `is_superuser`,
indistinguishable from one made by hand afterwards. There is no
first-account concept, nothing marks it, and it can be deleted like any other
-- provided another administrator exists, which migration 0020 enforces in the
database.

So the safeguard is not "this account is protected". It is "the system always
has at least one administrator", and this command is one of the two ways to
satisfy it. Re-running this is the other way back from an empty table, which is
why it must be re-runnable.

## What it will not do

IT NEVER CHANGES AN EXISTING ACCOUNT. Not the password, not the email, not the
flags. A deployment whose Secret is rotated must not silently reset the
password of an administrator who has since changed it, and a re-run must not
re-promote somebody an administrator deliberately demoted. If the account is
there, this says so and stops.

That is the conservative half of idempotence and it is the half worth stating:
"make sure this exists" is not "make this match".

## The password

Read from the environment, like `createsuperuser --noinput` does, so it comes
from a Secret rather than a values file -- docs/deployment.md#secrets is the
rule this follows rather than a new one. It is not logged, not echoed, and not
written anywhere but the hasher.
"""

import os
from typing import Any

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from inventory.management.commands import _telemetry

#: The same variable `createsuperuser --noinput` reads, so a deployment that
#: already sets it needs no second name for the same secret.
PASSWORD = "DJANGO_SUPERUSER_PASSWORD"
USERNAME = "DJANGO_SUPERUSER_USERNAME"
EMAIL = "DJANGO_SUPERUSER_EMAIL"


class Command(BaseCommand):
    help = "Create the first administrator if there is not one already. Safe to run repeatedly."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--username", default=os.environ.get(USERNAME, ""))
        parser.add_argument("--email", default=os.environ.get(EMAIL, ""))

    def handle(self, *args: Any, **options: Any) -> None:
        # `User` directly rather than `get_user_model()`. This project does not
        # swap the user model -- `AUTH_USER_MODEL` is Django's default -- and
        # the generic call returns a manager typed without `create_superuser`,
        # so the indirection buys a cast and nothing else.
        username = (options.get("username") or "").strip()
        password = os.environ.get(PASSWORD, "")

        if not username:
            raise CommandError(
                f"No username: pass --username or set {USERNAME}. This makes the account a "
                "deployment can first sign in with, so it cannot be guessed at."
            )

        with _telemetry.running(_telemetry.named(self)) as counted, transaction.atomic():
            existing = User.objects.filter(username=username).first()
            if existing is not None:
                # Deliberately not an error. A re-run finding the account
                # already there is the successful case -- see "What it will
                # not do" above for why it is also not an update.
                counted["created"] = 0
                self.stdout.write(f"{username} is already here; nothing was changed.")
                return

            if not password:
                raise CommandError(
                    f"No password: set {PASSWORD}. It is read from the environment so it "
                    "comes from a secret rather than a command line, where it would be "
                    "visible in a process list and a shell history."
                )

            User.objects.create_superuser(
                username=username,
                email=(options.get("email") or "").strip() or None,
                password=password,
            )
            counted["created"] = 1

        self.stdout.write(
            f"Created {username}. It is an ordinary administrator and can be removed once another exists."
        )
