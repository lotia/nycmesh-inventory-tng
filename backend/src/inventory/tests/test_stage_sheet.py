"""Tests for the staging tables and the command that fills them.

The workbook builder comes from ``workbooks`` rather than being rebuilt here,
because two builders would be free to disagree about the column order the
reader is asserted against.

Every test here writes a workbook and stages it, rather than constructing
staged rows directly: what is being claimed is that a row survives the round
trip from a spreadsheet cell to a database column and back, and rows typed
straight into the table would skip the half of that where things go wrong.
"""

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.management import call_command

from inventory.management.commands import _staging
from inventory.management.commands._report import render
from inventory.management.commands.profile_sheet import SECTIONS
from inventory.sheet import workbook
from inventory.sheet.workbook import Sheet
from inventory.staging import StagedCatalogueRow, StagedSubmissionRow
from inventory.tests.sheets import AT
from inventory.tests.workbooks import build, cells

pytestmark = pytest.mark.django_db


def export(tmp_path: Path, named: str, rows: list[tuple], catalogue: tuple[str, ...] = ("LiteBeam",)) -> Path:
    """A workbook in a directory of its own.

    Two of them, so that a re-run can be given a genuinely different export
    rather than the same file with the rows swapped underneath it.
    """
    directory = tmp_path / named
    directory.mkdir()
    return build(directory, rows, catalogue)


def reports(sheet: Sheet) -> list[list[str]]:
    """Every rule's section, as `profile_sheet` would print it."""
    return [render(*section(sheet)) for section in SECTIONS]


def test_every_non_blank_row_stages_and_the_population_rule_is_recorded(tmp_path: Path) -> None:
    """Including the furniture. A table holding only the 3,439 rows that count
    could not answer why the others do not, which is the question anybody
    checking an import against the spreadsheet arrives with.
    """
    path = build(
        tmp_path,
        [
            cells(),
            ("ADD NEW PRODUCTS HERE TO PREVENT ERRORS--->", "", "", "", "LiteBeam", "", ""),
            cells(workbook.CHECKING_IN),
            (None, None, None, None, None, None, None),
        ],
    )

    staged = _staging.stage(workbook.read(path))

    assert (staged.rows, staged.taken, staged.removed) == (3, 2, 0)
    assert list(StagedSubmissionRow.objects.values_list("row", "taken")) == [(2, True), (3, False), (4, True)]


def test_a_direction_that_is_not_one_is_staged_with_what_it_actually_said(tmp_path: Path) -> None:
    """`taken` is not "the direction column is empty". A row spelling the
    direction some other way is dropped by the population rule and is the one
    a reader most needs to see, so the column keeps what was written.
    """
    path = build(tmp_path, [cells(direction="checking out")])

    _staging.stage(workbook.read(path))

    row = StagedSubmissionRow.objects.get(row=2)
    assert (row.direction, row.taken) == ("checking out", False)


def test_the_row_is_kept_as_the_sheet_held_it_keyed_by_column_letter(tmp_path: Path) -> None:
    """Letters because that is what the spreadsheet shows the person checking."""
    path = build(tmp_path, [cells(note="mesh room")])

    _staging.stage(workbook.read(path))

    assert StagedSubmissionRow.objects.get(row=2).source == {
        "A": str(AT),
        "B": "ada@example.net",
        "C": "Ada",
        "D": workbook.CHECKING_OUT,
        "E": "LiteBeam",
        "F": 1.0,
        "G": "mesh room",
    }


def test_a_row_stopping_short_stages_the_cells_it_has_and_no_others(tmp_path: Path) -> None:
    """openpyxl hands back only the cells the sheet filled, and inventing the
    rest would be this table asserting something the export does not say.
    """
    path = build(tmp_path, [(AT, "a@example.net", "Ada", workbook.CHECKING_OUT, "LiteBeam", 1.0)])

    _staging.stage(workbook.read(path))

    assert list(StagedSubmissionRow.objects.get(row=2).source) == ["A", "B", "C", "D", "E", "F"]


def test_the_catalogue_tab_stages_too(tmp_path: Path) -> None:
    """A rule resolving an item string needs the catalogue, so a staged sheet
    that held only submissions could not re-run one.
    """
    path = build(tmp_path, [cells()], catalogue=("LiteBeam", "", "OmniTikPOE"))

    sheet = workbook.read(path)
    staged = _staging.stage(sheet)

    assert staged.catalogue == 3
    # Every non-blank row of that tab too, and the middle one carries a URL
    # and no name -- which is a row of the export and not a catalogued item.
    assert list(StagedCatalogueRow.objects.values_list("row", "name")) == [(2, "LiteBeam"), (3, ""), (4, "OmniTikPOE")]
    assert sheet.catalogue == ("LiteBeam", "OmniTikPOE")


def test_a_row_with_no_usable_timestamp_stages_without_one(tmp_path: Path) -> None:
    """A hand-typed row can carry anything in that column, and the staged
    value has to come back as the nothing the reader made of it.
    """
    path = build(tmp_path, [cells(at="not a date")])

    _staging.stage(workbook.read(path))

    assert StagedSubmissionRow.objects.get(row=2).at == ""
    assert _staging.staged_sheet().submissions[0].at is None


def test_a_second_run_over_the_same_export_leaves_the_same_rows(tmp_path: Path) -> None:
    path = build(tmp_path, [cells(), cells(workbook.CHECKING_IN)])
    sheet = workbook.read(path)

    _staging.stage(sheet)
    again = _staging.stage(sheet)

    assert (again.rows, again.removed) == (2, 0)
    assert StagedSubmissionRow.objects.count() == 2


def test_a_second_run_writes_over_a_row_the_export_has_changed(tmp_path: Path) -> None:
    """The upsert is keyed on the row number, so a corrected cell arrives as a
    correction rather than as a second row saying something else.
    """
    _staging.stage(workbook.read(export(tmp_path, "before", [cells(item="LiteBeam")])))

    _staging.stage(workbook.read(export(tmp_path, "after", [cells(item="OmniTikPOE")])))

    assert StagedSubmissionRow.objects.get(row=2).item == "OmniTikPOE"


def test_a_row_the_export_no_longer_holds_is_removed(tmp_path: Path) -> None:
    """Sheets renumbers everything below a deleted row, so keeping the old one
    would leave two rows claiming to be the same cell reference.
    """
    _staging.stage(workbook.read(export(tmp_path, "before", [cells(), cells(workbook.CHECKING_IN)])))

    again = _staging.stage(workbook.read(export(tmp_path, "after", [cells()])))

    assert again.removed == 1
    assert list(StagedSubmissionRow.objects.values_list("row", flat=True)) == [2]


def test_a_catalogue_row_the_export_no_longer_holds_is_removed_too(tmp_path: Path) -> None:
    _staging.stage(workbook.read(export(tmp_path, "before", [cells()], ("LiteBeam", "OmniTikPOE"))))

    again = _staging.stage(workbook.read(export(tmp_path, "after", [cells()], ("LiteBeam",))))

    assert again.removed == 1
    assert list(StagedCatalogueRow.objects.values_list("name", flat=True)) == ["LiteBeam"]


def test_the_staged_rows_answer_every_rule_the_way_the_workbook_does(tmp_path: Path) -> None:
    """The claim the tables exist to make: a rule that changes is re-run over
    these rows, by people who do not have the workbook and cannot be given it.
    """
    path = build(
        tmp_path,
        [
            cells(note="NN217 install"),
            cells(workbook.CHECKING_IN, name="Ada B", note="correcting my earlier entry"),
            cells(item="tp link", quantity=None, note="left in the mesh room"),
            cells(email="", name="ada", item="NYCM-ER-LBEG2"),
            ("testing form", "", "", "", "", "", ""),
        ],
        catalogue=("LiteBeam", "OmniTikPOE", "Tp-Link"),
    )
    sheet = workbook.read(path)

    _staging.stage(sheet)

    restaged = _staging.staged_sheet()
    assert restaged.submissions == sheet.submissions
    assert restaged.catalogue == sheet.catalogue
    assert reports(restaged) == reports(sheet)


def test_the_raw_row_comes_back_in_column_order(tmp_path: Path) -> None:
    """`jsonb` does not keep an object's keys in the order they were written."""
    path = build(tmp_path, [cells(note="mesh room")])

    _staging.stage(workbook.read(path))

    assert _staging.staged_sheet().rows[0].cells == (
        str(AT),
        "ada@example.net",
        "Ada",
        workbook.CHECKING_OUT,
        "LiteBeam",
        1.0,
        "mesh room",
    )


def test_the_command_says_what_it_staged(tmp_path: Path) -> None:
    path = build(tmp_path, [cells(), ("testing form", "", "", "", "", "", "")])
    out = io.StringIO()

    call_command("stage_sheet", str(path), stdout=out)

    printed = [" ".join(line.split()) for line in out.getvalue().splitlines()]
    assert printed == [
        "Staged",
        "catalogue rows 1",
        "submission rows 2",
        "the population rule takes 1",
        "rows the export no longer holds 0",
    ]


# Run in a process of its own, because this one imported the reader before the
# first test ran and its own `sys.modules` can therefore prove nothing.
BOOT = """
import sys, django
django.setup()
print(",".join(sorted(name for name in sys.modules if name in ("openpyxl", "inventory.sheet"))))
"""


def test_booting_the_app_does_not_import_the_workbook_reader() -> None:
    """`inventory/models.py` imports the staging tables so Django registers
    them, and that happens in every process the application runs -- so
    whatever they import is paid for by every gunicorn worker serving requests
    that will never see a spreadsheet. The cost is boot time; the reason it is
    worth a test is that `inventory/sheet/` is kept clear of the database and
    this is the same rule from the other side. `_staging.py` is where the two
    meet, and it is imported when a command runs.
    """
    source = Path(__file__).resolve().parent.parent.parent

    booted = subprocess.run(
        [sys.executable, "-c", BOOT],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "DJANGO_SETTINGS_MODULE": "inventory_tng.settings", "PYTHONPATH": str(source)},
    )

    assert booted.stdout.strip() == "", f"imported at boot: {booted.stdout.strip()}"
