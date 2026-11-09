"""Tests are categorised by what they need (database, worked out by
conftest.py from their fixtures; clone, by their module), apart from what
they are about, marked by hand (concurrency, end_to_end, helper).
CONTRIBUTING.md, Running the checks."""

import pytest

from helpers.categories import marked_against_its_needs, needs_database


@pytest.mark.parametrize(
    ("fixtures", "needs"),
    [
        ({"database_url"}, True),
        # A database of the test's own, copied from the migrated template.
        ({"own_database", "migrated_template"}, True),
        ({"session", "engine", "database_url", "owner_engine"}, True),
        ({"monkeypatch", "tmp_path"}, False),
        (set(), False),
    ],
)
def test_a_test_needs_the_database_when_its_fixtures_reach_one(
    fixtures: set[str], needs: bool
) -> None:
    assert needs_database(fixtures) is needs


@pytest.mark.parametrize(
    ("kinds", "fixtures", "wrong"),
    [
        ({"concurrency"}, {"committed", "database_url"}, False),
        ({"end_to_end"}, {"own_database", "migrated_template"}, False),
        ({"concurrency"}, {"monkeypatch"}, True),
        ({"end_to_end"}, set(), True),
        ({"helper"}, set(), False),
    ],
)
def test_requests_at_once_and_through_the_whole_app_need_the_database(
    kinds: set[str], fixtures: set[str], wrong: bool
) -> None:
    """conftest refuses to run a collection holding a test marked so but
    needing no database: what it is about implies what it needs."""
    assert marked_against_its_needs(kinds, fixtures) is wrong
