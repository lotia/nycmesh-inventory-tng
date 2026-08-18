"""Models and migrations must agree.

A model change committed without its migration passes lint, type checking and
most tests, then fails at deploy time when `migrate` runs against a real
database. This is a test rather than a CI step so that it fires locally too --
the same principle as the coverage threshold and the API schema gate.
"""

import pytest
from django.core.management import call_command


@pytest.mark.django_db
def test_no_missing_migrations() -> None:
    drifted = False
    try:
        call_command("makemigrations", "--check", "--dry-run", verbosity=0)
    except SystemExit:
        # makemigrations --check exits 1 when a model has drifted.
        drifted = True

    assert not drifted, (
        "A model was changed without a migration. Generate it:\n    uv run python src/manage.py makemigrations"
    )
