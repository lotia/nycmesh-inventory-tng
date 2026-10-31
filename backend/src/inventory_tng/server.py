"""What uvicorn and gunicorn load: `uvicorn --factory inventory_tng.server:create_server`.

The one place a server process is set up (docs/decisions/0007). Under
gunicorn it runs once in each worker, after the fork, which is why gunicorn's
`preload_app` must stay off.
"""

import structlog
from fastapi import FastAPI
from sqlalchemy import make_url

from inventory_tng.app import create_app
from inventory_tng.logs import configure_logging
from inventory_tng.settings import Settings

log = structlog.get_logger(__name__)


def create_server() -> FastAPI:
    settings = Settings.from_environ()
    configure_logging(settings.logging)
    database = make_url(settings.database_url)
    log.info(
        "starting",
        log_level=settings.logging.level,
        log_format=settings.logging.format,
        database_host=database.host,
        database_port=database.port,
        database_name=database.database,
    )
    return create_app(settings)
