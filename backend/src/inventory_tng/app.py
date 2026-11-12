"""Builds the application. Every router is included here, by hand.

Nothing process-wide is set up here, logging included (docs/decisions/0007).
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from inventory_tng import (
    account,
    accounts,
    admin,
    bundle,
    catalogue,
    database_role,
    db,
    devices,
    health,
    ledger,
    page_builds,
    page_telemetry,
    pages,
    resets,
    sign_in,
)
from inventory_tng.cross_site import CrossSiteRefusal
from inventory_tng.db import Database, make_database
from inventory_tng.mail import SmtpMailer
from inventory_tng.request_logging import RequestLogging
from inventory_tng.settings import Settings
from inventory_tng.totp_keys import TotpKeys

log = structlog.get_logger(__name__)

API = "/api"


def create_app(
    settings: Settings | None = None,
    *,
    totp_keys: TotpKeys | None = None,
    startup_checks: bool = True,
) -> FastAPI:
    """`totp_keys` are what authenticators' keys are sealed with
    (app.state.totp_keys); without them, read from TOTP_KEYS_DIR as the app
    starts, not as it is built, so building one for its schema needs none.
    `startup_checks=False` skips what startup reads from the database: the
    check of its login (database_role.py) and of the authenticators' keys.
    For tests, which check the login they use once, and test those apart."""
    settings = settings or Settings.from_environ()
    database = make_database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        app.state.totp_keys = keys = totp_keys or TotpKeys.from_environ()
        # Before serving anything: never as a login that could change the
        # ledger.
        if startup_checks:
            try:
                database_role.check(database.engine)
            except database_role.RefusedLogin:
                database.dispose()
                raise
            check_totp_keys(database, keys)
        log.info("started")
        try:
            yield
        finally:
            database.dispose()
            log.info("stopped")

    app = FastAPI(
        title="NYC Mesh Inventory",
        lifespan=lifespan,
        openapi_url="/api/schema",
        docs_url="/api/docs",
        redoc_url=None,
        responses=db.UNAVAILABLE,
    )
    app.state.db = database
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
    app.include_router(page_telemetry.router, prefix=API)
    app.include_router(bundle.router)
    app.include_router(sign_in.router)
    app.include_router(account.router)
    app.include_router(admin.router)
    app.include_router(resets.router)
    bundle.install(app, settings)
    db.install(app)
    devices.install(app)
    ledger.install(app)
    page_builds.install(app)
    page_telemetry.install(app)
    sign_in.install(app)
    return app


def check_totp_keys(database: Database, keys: TotpKeys) -> None:
    """Says at startup if any authenticator's key version is missing from
    TOTP_KEYS_DIR: those take no code until their administrators enrol again
    (DEPLOYMENT.md, TOTP keys)."""
    with database.session() as session:
        unreadable = accounts.unreadable_authenticators(session, keys)
    if unreadable:
        log.error(
            "authenticators unreadable: their key versions are not in TOTP_KEYS_DIR",
            count=unreadable,
        )
