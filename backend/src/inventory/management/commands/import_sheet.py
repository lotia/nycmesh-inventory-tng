"""Run every step of the sheet import, in order, and say what the database now holds.

The one command a contributor runs. It reads an exported workbook, stages it,
mints the catalogue and the volunteers those rows name, posts the ledger they
describe, and prints each step's own section -- so a run is reviewed from what
it printed rather than by querying afterwards. What the whole thing is for is
[data-model.md](../../../../docs/data-model.md#migrating-the-existing-sheet).

## It composes the four steps rather than replacing them

Each of them stays a command of its own, because each is separately re-runnable
and that is what the staged rows exist for -- `_staging.py` argues it. Somebody
who has already staged re-applies a changed rule by running the one step that
applies it, and needs no copy of the export to do it.

What this adds is the order, which is not negotiable: every step reads rows the
step before it wrote, and four commands run in the wrong order produce a partial
import with nothing saying so.

So it takes the path `stage_sheet` takes and nothing else. A `--dry-run` would
have to roll back the writes its own report describes, leaving an operator
holding figures about a database that does not exist. A flag for skipping a step
already run would buy back only the seconds staging takes -- the upsert writes
the same values over the same rows, and every step after it adds nothing to a
database it has already imported into. Both would be new ways to run the steps
in an order this command exists to remove.

## What a run that stops part way leaves behind

Each step is already one transaction, and these four are not wrapped in a fifth:
a step that fails leaves the steps before it standing, and running the command
again redoes them without adding anything. A transaction around all four would
hold one open across several thousand inserts to buy an all-or-nothing that
running it again gives for free.
"""

from pathlib import Path
from typing import Any

from django.core.management.base import CommandParser

from inventory.management.commands import _identifiers, _ledger, _people, _staging, _telemetry, _workbook
from inventory.sheet import Report


def written(
    staged: _staging.Staged,
    catalogue: _identifiers.Minted,
    volunteers: _people.Minted,
    ledger: _ledger.Posted,
) -> Report:
    """Everything this run changed, gathered out of the four sections above it.

    The figures are the ones those sections already carry. Gathered here
    because "would running this again do anything?" is a question the operator
    should be able to answer from one block of zeroes, rather than by picking
    six lines out of four sections and adding them up.

    The total is the sum of the shares rather than a count of its own, so the
    two cannot come to disagree. What it counts is rows that came from the
    export: the category, the location and the placeholder item the steps make
    for themselves are made by whichever run first needs one and by no run
    after it, and none of them is a row the export holds.
    """
    changed = [
        ("  staged rows the export no longer holds", staged.removed),
        ("  catalogued items", catalogue.items_added),
        ("  identifiers", catalogue.identifiers_added),
        ("  volunteers", volunteers.created),
        ("  transactions", ledger.transactions_added),
        ("  movements", ledger.movements_added),
    ]
    return "Written by this run", [("imported rows added or removed", sum(count for _, count in changed)), *changed]


class Command(_telemetry.ReportingCommand):
    blank_after_each_section = True

    help = "Stage an exported workbook, mint its catalogue and its volunteers, and post the ledger it describes."

    def add_arguments(self, parser: CommandParser) -> None:
        _workbook.add_argument(parser)

    def run(self, **options: Any) -> list[Report]:
        """The four steps and the summary, run in order, as their sections.

        What each step does, and why the staged rows are read back once rather
        than per step, is below. This was a staticmethod called from `handle`,
        split out for no reason but to make the run one expression the record
        could wrap -- which is the surgery `ReportingCommand` exists to undo.
        """
        workbook: Path = options["workbook"]
        staged = _staging.stage(_workbook.sheet_at(workbook))
        # Read back once and handed to all three of the steps that follow,
        # rather than each of them querying for itself: this is what those
        # steps see when they are run by name, and it is several thousand rows.
        sheet = _staging.staged_sheet()
        catalogue = _identifiers.mint(sheet)
        volunteers = _people.mint(sheet)
        ledger = _ledger.post(sheet)

        return [
            _staging.section(staged),
            _identifiers.section(catalogue),
            _people.section(volunteers),
            _ledger.section(ledger),
            written(staged, catalogue, volunteers, ledger),
        ]
