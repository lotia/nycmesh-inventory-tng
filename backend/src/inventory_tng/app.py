"""Builds the application. Every router is included here, by hand.

Nothing process-wide is set up here, logging included (docs/decisions/0007).
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from inventory_tng import (
    account,
    admin,
    bundle,
    catalogue,
    database_role,
    db,
    devices,
    health,
    ledger,
    pages,
    resets,
    sign_in,
)
from inventory_tng.cross_site import CrossSiteRefusal
from inventory_tng.db import make_engine
from inventory_tng.mail import SmtpMailer
from inventory_tng.request_logging import RequestLogging
from inventory_tng.settings import Settings

log = structlog.get_logger(__name__)

API = "/api"


def create_app(
    settings: Settings | None = None, *, check_login: bool = True
) -> FastAPI:
    """`check_login=False` skips the check of the database login at startup
    (database_role.py): for tests, which check the login they use once."""
    settings = settings or Settings.from_environ()
    engine = make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        # Before serving anything: never as a login that could change the
        # ledger.
        if check_login:
            try:
                database_role.check(engine)
            except database_role.RefusedLogin:
                engine.dispose()
                raise
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
        responses=db.UNAVAILABLE,
    )
    app.state.engine = engine
    app.state.settings = settings
    # mail.get_mailer hands it to routes.
    app.state.mailer = SmtpMailer(settings.mail) if settings.mail else None
    errors = pages.ErrorPages(api=API, scan=bundle.SCAN, admin=sign_in.HOME)
    errors.install(app)
    app.add_middleware(CrossSiteRefusal)
    app.add_middleware(
        RequestLogging,
        failed=errors.failed,
        client_address=settings.logging.client_address,
        # LOG_HEALTH_CHECKS=false quietens successful health checks.
        quiet_paths=frozenset()
        if settings.logging.health_checks
        else frozenset({API + health.PATH}),
    )
    app.include_router(health.router, prefix=API)
    app.include_router(devices.router, prefix=API)
    app.include_router(ledger.router, prefix=API)
    app.include_router(catalogue.router, prefix=API)
    app.include_router(bundle.router)
    app.include_router(sign_in.router)
    app.include_router(account.router)
    app.include_router(admin.router)
    app.include_router(resets.router)
    bundle.install(app, settings)
    db.install(app)
    devices.install(app)
    ledger.install(app)
    sign_in.install(app)
    return app
