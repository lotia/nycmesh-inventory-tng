"""Run every settled classifier over an exported workbook and print what it found.

This is what makes the figures in `docs/briefs/sheet-classifiers.md`
reproducible rather than asserted. A figure there is the output of a rule the
importer has to apply to every historical row anyway, so quoting one that no
code produces is how the brief came to carry several nobody could reproduce.

The workbook carries volunteer names and email addresses and is not ours to
publish, so nothing in CI can read it: the sections below are tested against a
workbook the test builds, and the real numbers come from a contributor running
this against their own copy. How to do that is in the brief.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from inventory.sheet import corrections, items, jobs, locations, workbook
from inventory.sheet.workbook import NotTheWorkbook, Sheet

# A section is a rule's own report: a heading, and labelled integers beneath
# it. Integers rather than free text is what lets one place align them and
# keeps six classifiers from each inventing their own layout. Most of what a
# rule reports is a partition, but not all of it -- a distinct count and a
# largest-of are neither -- so the contract is a label and a number and no
# more. Each classifier adds a section as it lands, and that is what keeps the
# brief's figures and the importer's behaviour coming from the same code.
Section = Callable[[Sheet], tuple[str, list[tuple[str, int]]]]


def population(sheet: Sheet) -> tuple[str, list[tuple[str, int]]]:
    """Which rows count, before any rule has an opinion about what they say."""
    return "Population", [
        (f"rows on {workbook.SUBMISSIONS_TAB}", sheet.rows_read),
        ("  carrying a direction", len(sheet.submissions)),
        (f"    {workbook.CHECKING_OUT}", sheet.check_outs),
        (f"    {workbook.CHECKING_IN}", sheet.check_ins),
        ("  carrying neither", sheet.without_direction),
        ("catalogued items", len(sheet.catalogue)),
    ]


def render(heading: str, counted: list[tuple[str, int]]) -> list[str]:
    """One section as the lines the brief quotes.

    A function rather than four lines inside `handle`, because the test that
    keeps the brief's blocks honest has to produce exactly this layout. Built
    into the test instead, it would agree with itself while the command
    changed underneath both -- which is the drift it exists to catch.

    Widths come from the section rather than from a constant, so a rule whose
    labels are longer than today's still lines up and nobody has to come back
    and widen a number here.
    """
    width = max(len(label) for label, _ in counted)
    return [heading, *(f"  {label:<{width}}  {count:>6}" for label, count in counted)]


SECTIONS: list[Section] = [
    population,
    items.section,
    corrections.section,
    locations.section,
    jobs.section,
]


class Command(BaseCommand):
    help = "Print the breakdown each sheet classifier produces over an exported workbook."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "workbook",
            type=Path,
            help="Path to the exported workbook, which the brief asks you to keep in ignored/.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        path: Path = options["workbook"]
        # Checked here rather than left to openpyxl, whose own message for a
        # missing file names a zip archive and reads as a corrupt workbook
        # rather than a path typed wrong.
        if not path.is_file():
            raise CommandError(f"No workbook at {path}.")
        try:
            sheet = workbook.read(path)
        except NotTheWorkbook as wrong:
            raise CommandError(str(wrong)) from wrong
        for section in SECTIONS:
            for line in render(*section(sheet)):
                self.stdout.write(line)
            self.stdout.write("")
