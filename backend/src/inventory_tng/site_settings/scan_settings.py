"""How the scan page behaves: every write to the scan_settings table, by
the Site settings page (site_settings/routes.py) and from the command line,
the operator's fallback (site_settings/command.py).

The page reads them with the catalogue, which an open page fetches again
every ten minutes. Changes are history.
"""

from sqlalchemy.orm import Session

from inventory_tng.core.db import Seen, settings_row
from inventory_tng.models import ScanSettings


def change(session: Session, name_check_minutes: int, *, seen: Seen) -> ScanSettings:
    """The settings, changed, in the range its check constraint holds
    (NAME_CHECK_MINUTES), which the page and the command check first."""
    current = settings_row(session, ScanSettings, seen=seen)
    current.name_check_minutes = name_check_minutes
    return current


def describe(minutes: int) -> str:
    if minutes == 0:
        return "never asks"
    return f"asks after {minutes} minute{'s' if minutes != 1 else ''} unused"
