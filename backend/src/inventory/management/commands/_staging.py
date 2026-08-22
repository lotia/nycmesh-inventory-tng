"""Filling the staging tables from a workbook, and handing their rows back as one.

Underscored for the reason `_workbook.py` gives: this is what `stage_sheet`
does, and what the commands after it re-read, rather than a command of its
own. Here rather than beside the tables in
`inventory/staging.py` because that module is imported at boot and this reads
spreadsheets; the reasoning is stated there, next to the import it protects.

## Why the rows are kept at all

`inventory/sheet/` reads the workbook and its six rules interpret what it
says. Both of those change -- a rule is a judgement, and every judgement in
that package is one somebody may argue with. Re-reading the workbook to apply
a changed rule needs the workbook, which is not in the repository and is not
ours to publish; re-reading the staged rows needs a database anybody on the
project already has. Nothing here interprets anything: it writes down what the
reader read, and hands it back in the shape the reader hands it over in.

## Why a re-run may delete

`stage` makes the tables equal the export, so a row the export no longer holds
is removed rather than kept. Sheets renumbers every row below a deleted one,
so a staged row that outlived its source would go on claiming a cell reference
that now belongs to different content -- two answers to "what does row 812
say?", of which the older one is wrong and neither is labelled.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.db import transaction

from inventory.sheet import Report, workbook
from inventory.sheet.workbook import CatalogueRow, Sheet, Submission, SubmissionRow
from inventory.staging import StagedCatalogueRow, StagedRow, StagedSubmissionRow

# What `source` may hold. openpyxl hands back the types a cell can have, and
# only these four go into JSON as themselves; a date, a time or a duration is
# rendered with `str`, which `datetime.fromisoformat` reads back.
JSON_SCALARS = (bool, int, float, str, type(None))


def _jsonable(cell: Any) -> Any:
    return cell if isinstance(cell, JSON_SCALARS) else str(cell)


def _source(cells: tuple[Any, ...]) -> dict[str, Any]:
    """The row as JSON, under the column letters the reader keys it by."""
    return {letter: _jsonable(cell) for letter, cell in workbook.by_column(cells).items()}


@dataclass(frozen=True)
class Staged:
    """What one run of `stage` did, for the command to print."""

    catalogue: int
    rows: int
    taken: int
    removed: int


def section(staged: Staged) -> Report:
    """This step's part of a run's report, beside the counts it is built from.

    Here rather than in the command for the reason `workbook.section` gives
    about its own placement: `import_sheet` prints the four steps one after
    another and must not have its own opinion about what any of them came to.

    Both totals, and the population inside one of them, because the figure
    worth checking against the brief is the population and the figure worth
    checking against the spreadsheet is the total.
    """
    return "Staged", [
        ("catalogue rows", staged.catalogue),
        ("submission rows", staged.rows),
        ("  the population rule takes", staged.taken),
        ("rows the export no longer holds", staged.removed),
    ]


def _replace(model: type[StagedRow], staged: Sequence[StagedRow]) -> int:
    """Make `model`'s table hold exactly `staged`, and say how many it dropped.

    An upsert rather than a wipe and a refill: the row number is the key, so a
    second run over the same export rewrites the same rows, and anything that
    later points at one of them keeps pointing at it.
    """
    removed, _ = model.objects.exclude(row__in=[one.row for one in staged]).delete()
    model.objects.bulk_create(
        staged,
        update_conflicts=True,
        update_fields=[field.name for field in model._meta.concrete_fields if not field.primary_key],
        unique_fields=["row"],
    )
    return removed


@transaction.atomic
def stage(sheet: Sheet) -> Staged:
    """Write every non-blank row of `sheet` down, and report what that came to.

    In one transaction because a half-staged table is a table that answers
    questions wrongly rather than refusing to answer them.
    """
    catalogue = [
        StagedCatalogueRow(row=one.number, source=_source(one.cells), name=one.name) for one in sheet.catalogue_rows
    ]
    rows = [
        StagedSubmissionRow(
            row=one.read.row,
            source=_source(one.cells),
            taken=one.taken,
            at=one.read.at.isoformat() if one.read.at else "",
            email=one.read.email,
            name=one.read.name,
            direction=one.read.direction,
            item=one.read.item,
            quantity=one.read.quantity,
            note=one.read.note,
        )
        for one in sheet.rows
    ]
    removed = _replace(StagedCatalogueRow, catalogue) + _replace(StagedSubmissionRow, rows)
    return Staged(
        catalogue=len(catalogue),
        rows=len(rows),
        taken=sum(1 for one in rows if one.taken),
        removed=removed,
    )


def staged_sheet() -> Sheet:
    """The staged rows, as the reader would have handed them over.

    A `Sheet` rather than querysets, so that every rule runs over this without
    knowing where it came from -- which is the whole claim being made, and is
    testable by putting the two side by side.

    The cells come back as JSON holds them, so a timestamp is the string it
    was written as rather than a `datetime`. No rule reads the cells; the
    reading beside them is what they read, and it round-trips.
    """
    return Sheet(
        catalogue_rows=tuple(
            CatalogueRow(number=one.row, cells=workbook.in_column_order(one.source), name=one.name)
            for one in StagedCatalogueRow.objects.all()
        ),
        rows=tuple(
            SubmissionRow(
                cells=workbook.in_column_order(one.source),
                read=Submission(
                    row=one.row,
                    at=datetime.fromisoformat(one.at) if one.at else None,
                    email=one.email,
                    name=one.name,
                    direction=one.direction,
                    item=one.item,
                    quantity=one.quantity,
                    note=one.note,
                ),
            )
            for one in StagedSubmissionRow.objects.all()
        ),
    )
