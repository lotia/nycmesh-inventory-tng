"""The path an operator typed, turned into a workbook, for the commands that take one.

**A module in this package whose name begins with an underscore is not a
command.** Django's discovery passes over it, which is what lets shared code
live beside the commands that use it rather than away from them.

This is shared so that the two commands reading an export report
a path typed wrong and a file that is not an export in the same words -- and
so that the second one written did not have to rediscover why either message
exists.
"""

from pathlib import Path

from django.core.management.base import CommandError, CommandParser

from inventory.sheet import workbook
from inventory.sheet.workbook import NotTheWorkbook, Sheet


def add_argument(parser: CommandParser) -> None:
    parser.add_argument(
        "workbook",
        type=Path,
        help="Path to the exported workbook, which the brief asks you to keep in ignored/.",
    )


def sheet_at(path: Path) -> Sheet:
    """Read the workbook at `path`, or fail with something an operator can act on."""
    # Checked here rather than left to openpyxl, whose own message for a
    # missing file names a zip archive and reads as a corrupt workbook rather
    # than a path typed wrong.
    if not path.is_file():
        raise CommandError(f"No workbook at {path}.")
    try:
        return workbook.read(path)
    except NotTheWorkbook as wrong:
        raise CommandError(str(wrong)) from wrong
