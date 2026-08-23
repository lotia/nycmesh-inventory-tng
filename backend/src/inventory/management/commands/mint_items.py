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

from django.core.management.base import BaseCommand

from inventory.management.commands import _identifiers, _report, _staging, _telemetry


class Command(BaseCommand):
    help = "Mint an Item per catalogued name and an ItemIdentifier per string that names one."

    def handle(self, *args: Any, **options: Any) -> None:
        with _telemetry.running("mint_items") as counted:
            minted = _identifiers.mint(_staging.staged_sheet())
            section = _identifiers.section(minted)
            counted.update(_telemetry.figures(section))
        for line in _report.render(*section):
            self.stdout.write(line)
