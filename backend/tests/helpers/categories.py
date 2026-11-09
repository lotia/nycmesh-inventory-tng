"""What a test needs to run, from its fixtures (conftest.py marks each
test; CONTRIBUTING.md, Running the checks)."""

from collections.abc import Iterable

# The fixtures that make a database on the server: a test whose fixtures
# reach either needs PostgreSQL.
DATABASE_ROOTS = frozenset({"database_url", "migrated_template"})


# What a test is about that implies it needs the database (pyproject.toml's
# markers): one marked so by hand but not using it is a mistake.
NEED_THE_DATABASE = frozenset({"concurrency", "end_to_end"})


def needs_database(fixturenames: Iterable[str]) -> bool:
    return not DATABASE_ROOTS.isdisjoint(fixturenames)


def marked_against_its_needs(kinds: Iterable[str], fixturenames: Iterable[str]) -> bool:
    """Marked by hand as a kind that needs the database, but not using it."""
    return not NEED_THE_DATABASE.isdisjoint(kinds) and not needs_database(fixturenames)
