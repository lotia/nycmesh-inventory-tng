"""Builds the application. Every router is included here, by hand.

Nothing process-wide is set up here, logging included (docs/decisions/0007).
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import Depends, FastAPI

from inventory_tng.admins import (
    account_routes,
    admins_routes,
    home_routes,
    reset_routes,
    sign_in_routes,
)
from inventory_tng.admins.guards import admin_signed_in, install_admin_answers
from inventory_tng.admins.paths import HOME
from inventory_tng.admins.sealed_totp_keys import TotpKeys, unreadable_authenticators
from inventory_tng.catalogue import (
    categories_routes,
    items_routes,
    reading_rules_routes,
)
from inventory_tng.core import database_role, db_errors, health
from inventory_tng.core.cross_site import CrossSiteRefusal
from inventory_tng.core.db_sessions import Database, make_database
from inventory_tng.core.mail import SmtpMailer
from inventory_tng.core.phrases import BRAND
from inventory_tng.core.request_logging import RequestLogging
from inventory_tng.core.settings import Settings
from inventory_tng.devices import devices_routes, registration_api
from inventory_tng.devices.guards import install_device_refused
from inventory_tng.labels import labels_routes
from inventory_tng.places import locations_routes
from inventory_tng.scan import (
    batch_api,
    catalogue_api,
    page_builds,
    page_telemetry_api,
)
from inventory_tng.site_settings import site_settings_routes
from inventory_tng.stock import (
    count_routes,
    counts_routes,
    length_checks_routes,
    over_recorded_routes,
    stock_routes,
    used_up_routes,
)
from inventory_tng.volunteers import volunteers_routes
from inventory_tng.web import frontend_bundle, pages, security_headers
from inventory_tng.web.numbers_in_words import NumbersInWords

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
    check of its login (core/database_role.py) and of the authenticators' keys.
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
        title=BRAND,
        lifespan=lifespan,
        openapi_url="/api/schema",
        # security_headers.docs, in development only, under a policy of its
        # own.
        docs_url=None,
        redoc_url=None,
        responses=db_errors.UNAVAILABLE,
    )
    app.state.db = database
    app.state.settings = settings
    # mail.get_mailer hands it to routes.
    app.state.mailer = SmtpMailer(settings.mail) if settings.mail else None
    errors = pages.ErrorPages(api=API, scan=frontend_bundle.SCAN, admin=HOME)
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
    # Numbers in words in the request's decimal separator, on every page,
    # the one RequestLogging shows for a failure included.
    app.add_middleware(NumbersInWords)
    # Outermost, so every answer has them, failures and refusals included.
    # Vite's dev server: development only (Settings).
    app.add_middleware(
        security_headers.SecurityHeaders,
        dev_server=settings.frontend.dev_server,
        docs=settings.development,
    )
    app.include_router(health.router, prefix=API)
    app.include_router(registration_api.router, prefix=API)
    app.include_router(batch_api.router, prefix=API)
    app.include_router(catalogue_api.router, prefix=API)
    app.include_router(page_telemetry_api.router, prefix=API)
    app.include_router(security_headers.router)
    if settings.development:
        app.include_router(security_headers.docs_router)
    app.include_router(frontend_bundle.router)
    app.include_router(sign_in_routes.router)
    # Every administrator page but those one signed out or not yet set up
    # with TOTP must reach (sign_in_routes, reset_routes). A route here is
    # guarded whether or not it takes AdminSignedIn, resolved once a request.
    # A loop, not one parent APIRouter: making a router here would make
    # app.py a module of routes to tests/codebase/test_import_directions.py,
    # which may not import other features' routes.
    for admin_pages in (
        account_routes,
        home_routes,
        items_routes,
        categories_routes,
        locations_routes,
        labels_routes,
        volunteers_routes,
        stock_routes,
        used_up_routes,
        length_checks_routes,
        over_recorded_routes,
        reading_rules_routes,
        counts_routes,
        count_routes,
        devices_routes,
        admins_routes,
        site_settings_routes,
    ):
        app.include_router(admin_pages.router, dependencies=[Depends(admin_signed_in)])
    app.include_router(reset_routes.router)
    frontend_bundle.install(app, settings)
    db_errors.install(app)
    registration_api.install(app)
    install_device_refused(app)
    batch_api.install(app)
    page_builds.install(app)
    page_telemetry_api.install(app)
    install_admin_answers(app)
    return app


def check_totp_keys(database: Database, keys: TotpKeys) -> None:
    """Says at startup if TOTP_KEYS_DIR cannot open any authenticator's key,
    its version gone or its file replaced: those take no code until their
    administrators enrol again (DEPLOYMENT.md, TOTP keys)."""
    with database.session() as session:
        unreadable = unreadable_authenticators(session, keys)
    if unreadable:
        log.error("authenticators unreadable with TOTP_KEYS_DIR", count=unreadable)
