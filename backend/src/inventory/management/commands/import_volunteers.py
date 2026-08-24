"""Mint a `Volunteer` for everybody the staged export names.

The third step of the import, and like `mint_items` before it, it reads what
`stage_sheet` left rather than a workbook -- so a rule that has changed is
re-run by anybody with the database and nobody needs a copy of the export.
What it mints, what it deliberately leaves to an administrator, and what happens
to the submissions that reach nobody are all in `_people.py`.
"""

from typing import Any

from inventory.management.commands import _people, _staging, _telemetry
from inventory.sheet import Report


class Command(_telemetry.ReportingCommand):
    help = "Give every volunteer the staged export names a row, flagging the ones it cannot tell apart."

    def run(self, **options: Any) -> list[Report]:
        return [_people.section(_people.mint(_staging.staged_sheet()))]
