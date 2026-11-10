"""What every operator command shares (registration_codes, scan_settings,
totp_keys; docs/decisions/0018):
the process is set up as the server sets itself up (docs/decisions/0007),
then the command runs in one transaction, committed if it returns.
"""

import argparse
import asyncio
import sys
from collections.abc import Callable

import structlog
from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from inventory_tng.db import make_database
from inventory_tng.local_env import load_local_env
from inventory_tng.logs import configure_logging
from inventory_tng.mail import Notifier, SmtpMailer
from inventory_tng.settings import Settings

log = structlog.get_logger(__name__)


def run_command(
    run: Callable[[Session, argparse.Namespace], int],
    args: argparse.Namespace,
    *,
    development_only: bool = False,
) -> int:
    load_local_env()
    settings = Settings.from_environ()
    configure_logging(settings)
    if development_only and not settings.development:
        log.warning("command refused outside development")
        print("Only with APP_ENV=development.", file=sys.stderr)
        return 1
    database = make_database(settings.database_url)
    try:
        with database.session() as session, session.begin():
            return run(session, args)
    finally:
        database.dispose()


def run_command_telling(
    run: Callable[[Session, argparse.Namespace, Notifier], int],
    args: argparse.Namespace,
) -> int:
    """run_command, for a command that mails administrators, as a route
    does (mail.Notifier): sent once its transaction has committed, as is
    anything else it adds to the Notifier's tasks, such as what to print."""
    tasks = BackgroundTasks()

    def telling(session: Session, args: argparse.Namespace) -> int:
        mail = Settings.from_environ().mail
        return run(session, args, Notifier(SmtpMailer(mail) if mail else None, tasks))

    code = run_command(telling, args)
    if code == 0:
        asyncio.run(tasks())
    return code
