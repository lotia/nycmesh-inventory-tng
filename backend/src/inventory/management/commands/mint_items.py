"""Mint the catalogue and its identifiers from the staged rows, and say what that came to.

The second step of the import, and it never opens a workbook: it reads the
tables `manage.py stage_sheet` filled, for the reason
`inventory/management/commands/_staging.py` gives. So it takes no path, and
running it against a database nobody has staged into mints nothing rather than
failing.

Safe to run again. What a re-run does to an identifier somebody has since
pointed at a different item, and what happens to a string the rule can make
nothing of, are both in `_identifiers.py`.
"""

from typing import Any

from inventory.management.commands import _identifiers, _staging, _telemetry
from inventory.sheet import Report


class Command(_telemetry.ReportingCommand):
    help = "Mint an Item per catalogued name and an ItemIdentifier per string that names one."

    def run(self, **options: Any) -> list[Report]:
        return [_identifiers.section(_identifiers.mint(_staging.staged_sheet()))]
