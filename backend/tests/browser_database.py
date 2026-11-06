"""The database the browser tests (frontend/browser/) run against, from their
global setup:

    uv run python tests/browser_database.py create    # prints JSON
    uv run python tests/browser_database.py drop URL

`create` makes one of its own (databases.py), migrated and seeded, with a
registration code for the tests' devices, and prints what they need. One a
run leaves behind is dropped by a later run, as test databases are.
"""

import argparse
import contextlib
import json
import secrets
import sys
from datetime import timedelta

import structlog
from databases import create_database, drop_database, drop_left_behind, migrate
from sqlalchemy.orm import Session

from inventory_tng import devices, seed
from inventory_tng.db import make_engine


def main(argv: list[str]) -> int:
    # Logs are for people: stdout is for the tests.
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(sys.stderr))
    if argv[:1] == ["drop"] and len(argv) == 2:
        drop_database(argv[1])
        return 0
    if argv != ["create"]:
        print(__doc__, file=sys.stderr)
        return 2
    drop_left_behind()
    url = create_database()
    engine = make_engine(url)
    migrate(engine)
    password = secrets.token_urlsafe(16)
    with Session(engine) as session, session.begin():
        # Its words are for people: stdout is the JSON alone.
        with contextlib.redirect_stdout(sys.stderr):
            seeded = seed.run(session, argparse.Namespace(password=password))
        if seeded != 0:
            return 1
        # One device for each browser the tests run in.
        _, phrase = devices.create_code(
            session, max_uses=10, lifetime=timedelta(hours=1), label="browser tests"
        )
    engine.dispose()
    print(
        json.dumps(
            {
                "database_url": url,
                "username": seed.USERNAME,
                "password": password,
                "phrase": phrase,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
