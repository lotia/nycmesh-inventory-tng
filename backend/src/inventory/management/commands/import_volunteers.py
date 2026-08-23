"""Mint a `Volunteer` for everybody the staged export names.

The third step of the import, and like `mint_items` before it, it reads what
`stage_sheet` left rather than a workbook -- so a rule that has changed is
re-run by anybody with the database and nobody needs a copy of the export.
What it mints, what it deliberately leaves to an administrator, and what happens
to the submissions that reach nobody are all in `_people.py`.
"""

from typing import Any

from django.core.management.base import BaseCommand

from inventory.management.commands import _people, _report, _staging, _telemetry


class Command(BaseCommand):
    help = "Give every volunteer the staged export names a row, flagging the ones it cannot tell apart."

    def handle(self, *args: Any, **options: Any) -> None:
        with _telemetry.running("import_volunteers") as counted:
            minted = _people.mint(_staging.staged_sheet())
            section = _people.section(minted)
            counted.update(_telemetry.figures(section))
        for line in _report.render(*section):
            self.stdout.write(line)
