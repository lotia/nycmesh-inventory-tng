"""Post the transactions and movements the staged rows describe, and say what that came to.

The fourth step of the import and the last that writes: `_ledger.py` says
what it posts and why, and what it declines to. Like the steps before it, it
reads the tables `stage_sheet` filled and never a workbook, so it takes no
path and a database nobody has staged into posts nothing rather than failing.

Safe to run again: the second run finds every act already recorded and adds
none of them. How it knows, what it does with a row it cannot post, and every
other judgement it makes are in `_ledger.py`.

It does refuse to run at all in two states, both of them ones where posting
would write part of the export into a table nobody can edit. Which two, and
why that is not the same answer as declining a row, is argued there as well.
"""

from typing import Any

from django.core.management.base import BaseCommand

from inventory.management.commands import _ledger, _report, _staging


class Command(BaseCommand):
    help = "Post a StockTransaction per batch of staged rows, with a StockMovement for each row in it."

    def handle(self, *args: Any, **options: Any) -> None:
        posted = _ledger.post(_staging.staged_sheet())
        for line in _report.render(*_ledger.section(posted)):
            self.stdout.write(line)
