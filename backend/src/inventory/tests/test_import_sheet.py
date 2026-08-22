"""Tests for the one command that runs the whole import.

What each step does with a row is claimed in that step's own module, so
nothing here re-asks any of it. What is claimed here is what only the
composition can be asked: that one command leaves a ledger, that its report
accounts for every submission the export holds, and that running it a second
time writes nothing and says so.

The workbook is built here as it is everywhere else in this batch, because the
real export is not ours to publish.
"""

import io
from pathlib import Path

import pytest
from django.core.management import call_command

from inventory.models import Item, StockMovement, StockTransaction, Volunteer
from inventory.tests.reports import counts_in, depths_are_allowed, shares_of
from inventory.tests.workbooks import build, cells

pytestmark = pytest.mark.django_db

# One row of each shape the report has to account for: a row that posts, a row
# whose item string reaches nothing and posts against the placeholder, a row
# naming nobody, a row with no quantity, and a piece of the tab's furniture
# that is not a submission at all.
ROWS = [
    cells(),
    cells(item="mast"),
    cells(name="testing", email=""),
    cells(quantity=0.0),
    ("ADD NEW PRODUCTS HERE TO PREVENT ERRORS--->", "", "", "", "", "", ""),
]

# The lines of the ledger's section that describe the run rather than the
# ledger, and so are the ones a second run has to read zero on.
A_RUNS_OWN_WORK = {" posted by this run", "items flagged for their quantities"}


def export(tmp_path: Path) -> Path:
    return build(tmp_path, ROWS)


def report(path: Path) -> dict[str, list[tuple[str, int]]]:
    """A run of the command, read back as the sections it printed.

    Keyed on the heading, which is also the check that no two steps print
    under one: a section that overwrote another's would leave this test
    asserting against half a report.
    """
    out = io.StringIO()

    call_command("import_sheet", str(path), stdout=out)

    found: dict[str, list[str]] = {}
    heading = ""
    for line in out.getvalue().splitlines():
        if line.startswith("  "):
            found[heading].append(line)
        elif line:
            heading = line
            assert heading not in found, f"two sections printed under {heading!r}"
            found[heading] = []
    return {heading: counts_in(lines) for heading, lines in found.items()}


def test_one_command_stages_mints_and_posts_without_any_of_them_being_run_by_name(tmp_path: Path) -> None:
    """The order is the thing being claimed: every step reads rows the step
    before it wrote, so a command that ran them in any other order would leave
    a ledger with nothing under it.
    """
    call_command("import_sheet", str(export(tmp_path)), stdout=io.StringIO())

    assert Item.objects.filter(name="LiteBeam").exists()
    assert Volunteer.objects.get().display_name == "Ada"
    assert StockTransaction.objects.count() == 1
    assert StockMovement.objects.count() == 2


def test_the_report_accounts_for_every_submission_the_export_holds(tmp_path: Path) -> None:
    """The claim the whole report is for, stated once on `_ledger.section`:
    here it is asserted, and asserted as a partition so no row can be in
    neither place.
    """
    ledger = report(export(tmp_path))["Ledger"]

    counted = dict(ledger)
    assert counted["submissions"] == sum(count for _, count in shares_of(ledger, "submissions"))
    assert counted["  reaching a movement"] == 2
    assert (counted["  naming nobody"], counted["  carrying no quantity above zero"]) == (1, 1)


def test_a_row_posted_against_the_placeholder_is_counted_inside_the_ones_that_posted(tmp_path: Path) -> None:
    """It reached a movement, so counting it beside the refusals would make
    the submissions add up to more than the export holds.
    """
    ledger = report(export(tmp_path))["Ledger"]

    assert dict(ledger)["   of those, against the placeholder item"] == 1
    assert "   of those, against the placeholder item" not in [label for label, _ in shares_of(ledger, "submissions")]


def test_the_furniture_is_in_the_staged_rows_and_not_in_the_submissions(tmp_path: Path) -> None:
    """The two totals answer different questions -- one is checked against the
    spreadsheet and the other against the brief -- and a report carrying only
    the second could not say why they differ.
    """
    printed = report(export(tmp_path))

    assert dict(printed["Staged"])["submission rows"] == 5
    assert dict(printed["Ledger"])["submissions"] == 4


def test_running_it_a_second_time_writes_nothing_and_says_so_in_one_block(tmp_path: Path) -> None:
    """Idempotence as something the operator reads rather than something they
    have to go and diff a database for.
    """
    path = export(tmp_path)
    first = report(path)

    again = report(path)

    assert first["Written by this run"] == [
        ("imported rows added or removed", 6),
        ("  staged rows the export no longer holds", 0),
        ("  catalogued items", 1),
        ("  identifiers", 1),
        ("  volunteers", 1),
        ("  transactions", 1),
        ("  movements", 2),
    ]
    assert again["Written by this run"] == [(label, 0) for label, _ in first["Written by this run"]]
    # Compared as the list the section is, rather than as a mapping: two of its
    # labels are the same string and one of them would be lost.
    assert again["Ledger"] == [(label, 0 if label in A_RUNS_OWN_WORK else count) for label, count in first["Ledger"]]


def test_the_total_it_wrote_is_the_sum_of_what_the_steps_reported(tmp_path: Path) -> None:
    """Derived rather than counted a second time, so the closing block cannot
    come to disagree with the four sections it gathers.
    """
    written = report(export(tmp_path))["Written by this run"]

    assert dict(written)["imported rows added or removed"] == sum(
        count for _, count in shares_of(written, "imported rows added or removed")
    )


def test_every_section_it_prints_keeps_the_depths_the_contract_allows(tmp_path: Path) -> None:
    for counted in report(export(tmp_path)).values():
        depths_are_allowed(counted)
