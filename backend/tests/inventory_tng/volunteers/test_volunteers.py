"""A volunteer's rules of more than one condition (volunteers/volunteers.py),
as functions of plain values: every way each can be broken, with no
database. That each domain function calls its rules, under the catalogue
lock, is models/test_models.py's and across/test_requests_at_once.py's to
show, on PostgreSQL."""

import pytest

from inventory_tng.volunteers import volunteers


@pytest.mark.parametrize(
    ("merged_in", "custody", "broken"),
    [
        (False, False, None),
        (True, False, "volunteer_merge_target"),
        (False, True, "location_custody_volunteer"),
        # Those merged in are named first, as the page says to move them.
        (True, True, "volunteer_merge_target"),
    ],
)
def test_a_volunteer_leaves_once_nothing_points_at_them(
    merged_in: bool, custody: bool, broken: str | None
) -> None:
    assert (
        volunteers.leaving(others_merged_in=merged_in, holds_custody=custody) == broken
    )
