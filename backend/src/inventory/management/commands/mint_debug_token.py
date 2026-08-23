"""Mint a token that lets one volunteer's requests be recorded in full.

Hand the token to whoever is meeting the failure. It expires by itself, so
there is nothing to remember to undo, and rotating DJANGO_SECRET_KEY revokes
every token there is.

What it authorises, what it deliberately does not, and how much it may cost
are `inventory_tng/debugging.py`; how to use one is docs/observability.md.
"""

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand

from inventory_tng import debugging


class Command(BaseCommand):
    help = "Mint a signed, expiring token that has one volunteer's requests traced in full."

    def handle(self, *args: Any, **options: Any) -> None:
        self.stdout.write(debugging.mint())
        self.stdout.write("")
        self.stdout.write(f"Send it as {debugging.HEADER}.")
        self.stdout.write(
            f"Good for {settings.DEBUG_TRACE_LIFETIME_SECONDS} seconds, "
            f"and at most {settings.DEBUG_TRACE_RATE} of that volunteer's requests."
        )
