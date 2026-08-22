"""Writing a workbook shaped like the export, holding just the rows a test wants.

Four suites go through the reader -- the profile, the staging, the catalogue
and the whole-import ones -- and a builder living in the first of them makes
the other three import a test module to reach it. Here instead, for the reason
``reports.py`` gives about the question both report suites ask.

Here rather than in ``sheets.py`` because that file builds the *reading* and
this one builds the cells it is read from: nothing there opens a file, and the
column order is the part a reader is actually asserted against. `cells` is
named for what it returns rather than after `sheets.submission`, which is the
same row on the other side of the reader -- one name in two modules is how a
suite importing both ends up building the wrong one.
"""

from pathlib import Path
from typing import Any

from openpyxl import Workbook

from inventory.sheet import workbook
from inventory.tests import sheets

HEADERS = (
    "Timestamp",
    "Email Address",
    "First name",
    "Checking in/Checking out",
    "Item",
    "How many?",
    "Planned Use / Project / Notes",
)


def build(tmp_path: Path, submissions: list[tuple[Any, ...]], catalogue: tuple[str, ...] = ("LiteBeam",)) -> Path:
    """Write a workbook shaped like the export, holding just these rows."""
    book = Workbook()
    items = book.active
    assert items is not None
    items.title = workbook.CATALOGUE_TAB
    # Column D, with the QR link in C, because that pairing is the trap the
    # reader exists to get right and a fixture holding only D would not catch
    # a reader that read C.
    items.append(("Add new rows", "streakwave", "QR", "Name"))
    for name in catalogue:
        items.append(("", "streakwave", "docs.google.com/forms/...", name))

    responses = book.create_sheet(workbook.SUBMISSIONS_TAB)
    responses.append(HEADERS)
    for row in submissions:
        responses.append(row)

    path = tmp_path / "sheet.xlsx"
    book.save(path)
    return path


def cells(direction: str = workbook.CHECKING_OUT, **fields: Any) -> tuple[Any, ...]:
    """One row of the tab, in the column order the reader is being tested on.

    Built over `sheets.submission` rather than beside it, so that there is one
    set of defaults and one `AT`.
    """
    row = sheets.submission(direction=direction, **fields)
    return (row.at, row.email, row.name, row.direction, row.item, row.quantity, row.note)
