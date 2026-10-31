"""Builds the application. Every router is included here, by hand.

Nothing process-wide is set up here, logging included (docs/decisions/0007).
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from inventory_tng import health
from inventory_tng.db import make_engine
from inventory_tng.request_logging import RequestLogging
from inventory_tng.settings import Settings

log = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_environ()
    engine = make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        log.info("started")
        try:
            yield
        finally:
            engine.dispose()
            log.info("stopped")

    app = FastAPI(
        title="NYC Mesh Inventory",
        lifespan=lifespan,
        openapi_url="/api/schema",
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.state.engine = engine
    app.add_middleware(RequestLogging, client_address=settings.logging.client_address)
    app.include_router(health.router, prefix="/api")
    return app
