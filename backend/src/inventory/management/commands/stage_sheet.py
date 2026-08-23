"""Load an exported workbook into the staging tables, and say what that came to.

The first step of the import, and the only one that reads the workbook: every
step after it works from the tables this fills, for the reasons `_staging.py`
gives. Safe to run again -- that is the point of it -- and what a second run
does to a row that has since been deleted from the export is stated there
too.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from inventory.management.commands import _report, _staging, _telemetry, _workbook


class Command(BaseCommand):
    help = "Stage every non-blank row of an exported workbook, keeping each row as it was."

    def add_arguments(self, parser: CommandParser) -> None:
        _workbook.add_argument(parser)

    def handle(self, *args: Any, **options: Any) -> None:
        with _telemetry.running("stage_sheet") as counted:
            staged = _staging.stage(_workbook.sheet_at(options["workbook"]))
            section = _staging.section(staged)
            counted.update(_telemetry.figures(section))
        for line in _report.render(*section):
            self.stdout.write(line)
