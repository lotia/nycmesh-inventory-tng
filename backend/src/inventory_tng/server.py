"""The factory uvicorn loads: `python -m inventory_tng` runs it (__main__.py).

The one place a server process is set up (docs/decisions/0007): logging, then
telemetry. Under `--workers` or `--reload` each process runs it for itself.
"""

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager

import anyio
import structlog
from fastapi import FastAPI
from sqlalchemy import make_url

from inventory_tng.app import create_app
from inventory_tng.logs import configure_logging
from inventory_tng.settings import Settings
from inventory_tng.telemetry import Telemetry, enabled

log = structlog.get_logger(__name__)

SHUTDOWN_SECONDS = 10


def create_server() -> FastAPI:
    settings = Settings.from_environ()
    configure_logging(settings.logging)
    database = make_url(settings.database_url)
    telemetry_on = enabled()
    log.info(
        "starting",
        log_level=settings.logging.level,
        log_format=settings.logging.format,
        log_health_checks=settings.logging.health_checks,
        database_host=database.host,
        database_port=database.port,
        database_name=database.database,
        telemetry=telemetry_on,
    )
    app = create_app(settings)
    if telemetry_on:
        # Here and not in create_app(): providers belong to the process
        # (docs/decisions/0007).
        telemetry = Telemetry.create()
        telemetry.make_global()
        telemetry.attach_logs()
        telemetry.instrument(app, app.state.engine)
        flush_when_stopped(app, telemetry.shutdown)
    return app


def flush_when_stopped(app: FastAPI, shutdown: Callable[[], None]) -> None:
    """Run `shutdown` when the app's lifespan ends. Exit hooks are no good:
    uvicorn re-raises the stop signal after a graceful shutdown, so the
    process ends without running them, and the last batch would be lost."""
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        try:
            async with inner(app):
                yield
        finally:
            # Off the event loop, and bounded, so an unreachable collector
            # cannot hold the pod past Kubernetes' 30 s grace period.
            with anyio.move_on_after(SHUTDOWN_SECONDS) as deadline:
                await anyio.to_thread.run_sync(shutdown, abandon_on_cancel=True)
            if deadline.cancelled_caught:
                log.warning("telemetry not flushed in time", seconds=SHUTDOWN_SECONDS)

    app.router.lifespan_context = lifespan
