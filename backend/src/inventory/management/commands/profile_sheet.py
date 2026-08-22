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

from inventory.sheet import Report, batches, corrections, items, jobs, locations, people, workbook
from inventory.sheet.workbook import NotTheWorkbook, Sheet

# What a section is, and why a line is a label and a number, is stated once
# with the type in inventory/sheet/. What is here is the registry: which
# sections there are and in what order, which is the brief's order and not one
# a discovery mechanism could work out.
Section = Callable[[Sheet], Report]


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
    workbook.section,
    items.section,
    corrections.section,
    locations.section,
    jobs.section,
    batches.section,
    people.section,
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
