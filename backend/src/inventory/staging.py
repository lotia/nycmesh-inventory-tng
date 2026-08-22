"""The tables the exported workbook's rows are kept in, exactly as they arrived.

## Why these are not in `inventory/models.py`

That module implements the entity model of
[docs/data-model.md](../../../docs/data-model.md), and nothing here is an
entity of it: these tables describe a spreadsheet, keyed by the row
number that spreadsheet shows, and they stop
meaning anything the day the import is finished. They are also the opposite of
the half of that module they would sit below -- the ledger is append-only and a
trigger enforces it, whereas a staged row exists to be written over by the next
run. Putting a rewritable table into the file organised around not rewriting
things is how the next reader adds a trigger to it. `inventory/models.py`
imports this module so that Django registers the models; that import is the
only tie between them.

## Why nothing here reads a workbook

That import happens at boot, in every process the application runs, so
whatever this module imports is imported into all of them. `inventory/sheet/`
opens spreadsheets and its own header says nothing in it touches the database;
this is the same rule seen from the other side, and it is what keeps the model
registry from reaching into the reader. So there are fields here and nothing
else. Filling these tables from a workbook, and handing their rows back as one,
is `inventory/management/commands/_staging.py`, which is imported when a
command runs and at no other time.
"""

from django.db import models


class StagedRow(models.Model):
    """What both tabs' rows have: where the row was, and what was in it."""

    row = models.PositiveIntegerField(
        primary_key=True,
        help_text="The row number the spreadsheet shows, counting the header as row 1.",
    )
    source = models.JSONField(help_text="The row's cells, keyed by column letter, before anything read them.")
    # Rewritten by every run, so a table nobody has staged into for a month
    # says so rather than looking like the export it no longer matches.
    staged_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["row"]


class StagedCatalogueRow(StagedRow):
    """A row of the catalogue tab, staged.

    Here because the item rule resolves a string against the catalogue, so a
    rule re-run from these tables and a rule run from the workbook would
    otherwise be answering against different lists.
    """

    name = models.TextField(blank=True, help_text="The catalogued name, empty where the row names nothing.")

    def __str__(self) -> str:
        return f"{self.row}: {self.name or '(no name)'}"


class StagedSubmissionRow(StagedRow):
    """A row of the submissions tab, staged, with what the reader read.

    The columns are `Submission`'s fields, filled for every row and not only
    for the ones the population rule takes, because a row it rejected is
    exactly the row somebody will come back to ask about. `taken` is that
    rule's verdict, and is not the same question as whether `direction` is
    empty: a direction column holding something that is not one of the two
    spellings is a row this table can explain and a count cannot.
    """

    taken = models.BooleanField(help_text="Whether the population rule counts this row as a submission.")
    # Text, not a DateTimeField, and empty rather than NULL where the row
    # carries no usable timestamp. Every timestamp in the export is naive and
    # `Submission.at` says why nothing may assume a zone for it; a
    # `timestamptz` column cannot hold one without assuming one, and the
    # assumption would be made here, by a table whose whole purpose is to
    # assume nothing. Deciding the zone is the importer's, and is recorded
    # where it is decided.
    at = models.CharField(max_length=64, blank=True)
    email = models.TextField(blank=True)
    name = models.TextField(blank=True)
    direction = models.TextField(blank=True)
    item = models.TextField(blank=True)
    # Stored as the float it arrived as, for the reason `Submission.quantity`
    # gives. Narrowing it is a decision, and the importer's to make.
    quantity = models.FloatField(null=True, blank=True)
    note = models.TextField(blank=True)

    def __str__(self) -> str:
        return f"{self.row}: {self.direction or '(no direction)'} {self.item}".strip()
