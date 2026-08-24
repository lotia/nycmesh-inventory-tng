"""Run every settled classifier over an exported workbook and print what it found.

This is what makes the figures in `docs/briefs/sheet-classifiers.md`
reproducible rather than asserted. A figure there is the output of a rule the
importer has to apply to every historical row anyway, so quoting one that no
code produces is how the brief came to carry several nobody could reproduce.

The workbook is not in the repository and cannot be, for the reason the
brief's "How to re-run" gives. So the sections below are tested against a
workbook the test builds, and the real numbers come from a contributor running
this against their own copy. How to do that is in the brief too.
"""

from collections.abc import Callable
from typing import Any

from django.core.management.base import CommandParser

from inventory.management.commands import _telemetry, _workbook
from inventory.sheet import Report, batches, corrections, items, jobs, locations, people, returns, workbook
from inventory.sheet.workbook import Sheet

# What a section is, and why a line is a label and a number, is stated once
# with the type in inventory/sheet/. What is here is the registry: which
# sections there are and in what order, which is the brief's order and not one
# a discovery mechanism could work out.
Section = Callable[[Sheet], Report]

SECTIONS: list[Section] = [
    workbook.section,
    items.section,
    corrections.section,
    locations.section,
    jobs.section,
    batches.section,
    people.section,
    returns.section,
]


class Command(_telemetry.ReportingCommand):
    blank_after_each_section = True

    help = "Print the breakdown each sheet classifier produces over an exported workbook."

    def add_arguments(self, parser: CommandParser) -> None:
        _workbook.add_argument(parser)

    def run(self, **options: Any) -> list[Report]:
        sheet = _workbook.sheet_at(options["workbook"])
        return [section(sheet) for section in SECTIONS]
