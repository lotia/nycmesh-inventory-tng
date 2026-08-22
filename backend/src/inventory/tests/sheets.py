"""Builders for the sheet the classifier tests ask questions of.

Here rather than in helpers.py, which holds request helpers for the API tests:
nothing in this file touches a client or the database, because a classifier is
a function over what a row says. Here rather than in conftest.py for the
reason helpers.py gives.

Everything a rule does not look at gets a default, so a test that is about
notes says only notes and a test that is about people says only people. That
is the point: a fixture carrying seven fields when the rule reads one hides
which one the rule read.
"""

from datetime import datetime

from inventory.sheet.workbook import CHECKING_OUT, CatalogueRow, Sheet, Submission, SubmissionRow

AT = datetime(2026, 8, 21, 13, 0, 0)


def submission(
    *,
    row: int = 2,
    at: datetime | None = AT,
    email: str = "ada@example.net",
    name: str = "Ada",
    direction: str = CHECKING_OUT,
    item: str = "LiteBeam",
    quantity: float | None = 1.0,
    note: str = "",
) -> Submission:
    return Submission(
        row=row,
        at=at,
        email=email,
        name=name,
        direction=direction,
        item=item,
        quantity=quantity,
        note=note,
    )


def sheet_of(
    submissions: list[Submission],
    *,
    catalogue: tuple[str, ...] = ("LiteBeam",),
    rows_read: int | None = None,
) -> Sheet:
    """A sheet holding exactly these submissions.

    `rows_read` defaults to the number of them, which says there was no sheet
    furniture: a test about a rule is not also a test of the population. Ask
    for more and the difference is made up with rows carrying no direction,
    since that is the only way the reader produces one.

    The cells behind every row are empty, because a rule reads the reading and
    never the row: only `inventory.staging` wants the cells, and its own tests
    build them from a real workbook.
    """
    rows = [SubmissionRow(cells=(), read=one) for one in submissions]
    following = max((one.row for one in submissions), default=1) + 1
    furniture = 0 if rows_read is None else rows_read - len(submissions)
    rows += [
        SubmissionRow(cells=(), read=submission(row=following + which, direction="")) for which in range(furniture)
    ]
    return Sheet(
        catalogue_rows=tuple(
            CatalogueRow(number=number, cells=(), name=name) for number, name in enumerate(catalogue, start=2)
        ),
        rows=tuple(rows),
    )


def notes(*written: str) -> Sheet:
    """A sheet of submissions differing only in what their notes say."""
    return sheet_of([submission(row=number, note=note) for number, note in enumerate(written, start=2)])
