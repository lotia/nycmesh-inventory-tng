"""What every operator command shares (registration_codes, scan_settings):
the process is set up as the server sets itself up (docs/decisions/0007),
then the command runs in one transaction, committed if it returns.
"""

import argparse
from collections.abc import Callable

from sqlalchemy.orm import Session

from inventory_tng.db import make_engine
from inventory_tng.local_env import load_local_env
from inventory_tng.logs import configure_logging
from inventory_tng.settings import Settings


def run_command(
    run: Callable[[Session, argparse.Namespace], int], args: argparse.Namespace
) -> int:
    load_local_env()
    settings = Settings.from_environ()
    configure_logging(settings)
    engine = make_engine(settings.database_url)
    try:
        with Session(engine) as session, session.begin():
            return run(session, args)
    finally:
        engine.dispose()
