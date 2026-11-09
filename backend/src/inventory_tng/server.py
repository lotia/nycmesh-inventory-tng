"""The factory uvicorn loads: `python -m inventory_tng` runs it (__main__.py).

The one place a server process is set up (docs/decisions/0007): logging, then
telemetry. Under `--workers` or `--reload` each process runs it for itself.
"""

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime

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
    masker = configure_logging(settings)
    database = make_url(settings.database_url)
    telemetry_on = enabled()
    app = create_app(settings)
    # Spans leaving unredacted outside development: said in the starting line.
    traces_off = False
    if telemetry_on:
        # Here and not in create_app(): providers belong to the process
        # (docs/decisions/0007).
        telemetry = Telemetry.create(
            traces=settings.traces,
            development=settings.development,
            secrets=masker.secrets,
        )
        telemetry.make_global()
        telemetry.attach_logs(masker)
        telemetry.instrument(app, app.state.db)
        flush_when_stopped(app, telemetry.shutdown)
        traces_off = (
            telemetry.tracer_provider is not None
            and settings.traces.off_outside_development(
                development=settings.development, now=datetime.now(UTC)
            )
        )
    # After telemetry, so the line reaches OTLP too. A warning while secrets
    # are shown, or logs or traces leave unredacted outside development,
    # which is how a dashboard tells; the masker warns hourly after.
    logs_off = masker.off_outside_development()
    quiet = masker.secrets and not traces_off and not logs_off
    (log.info if quiet else log.warning)(
        "starting",
        log_level=settings.logging.level,
        log_format=settings.logging.format,
        log_health_checks=settings.logging.health_checks,
        log_redaction=masker.redact,
        log_redaction_off_until=(
            settings.logging.redaction.off_until if logs_off else None
        ),
        log_unmasked=not masker.secrets,
        trace_redaction_off_until=settings.traces.off_until if traces_off else None,
        # from_environ always loads them; TotpKeys.load cannot log, as it
        # runs before logging is set up.
        sealing_key_versions=(
            sorted(settings.totp_keys.keys) if settings.totp_keys else []
        ),
        database_host=database.host,
        database_port=database.port,
        database_name=database.database,
        telemetry=telemetry_on,
    )
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
