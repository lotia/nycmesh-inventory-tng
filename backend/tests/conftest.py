"""Each test run gets a database of its own, created on the compose PostgreSQL
server and dropped when the run ends. Each test's writes are rolled back."""

import contextlib
import dataclasses
import functools
import logging
import os
from collections.abc import Callable, Generator, Iterator
from pathlib import Path

import argon2.profiles
import pytest
import structlog
from alembic.config import Config
from argon2 import PasswordHasher
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, TextClause, delete, event, exc, make_url, text
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import Session

from inventory_tng import accounts, devices, local_env
from inventory_tng.app import create_app
from inventory_tng.database_role import refusals
from inventory_tng.db import make_engine, run_outside_transaction
from inventory_tng.history import LEDGER
from inventory_tng.logs import configure_structlog
from inventory_tng.mail import get_mailer
from inventory_tng.models import RateLimit
from inventory_tng.settings import FrontendConfig, LogConfig, Settings

from databases import (
    NO_DATABASE,
    Unreachable,
    alembic_config_for,
    create_database,
    drop_database,
    migrated_as_owner,
    migrated_engine,
    new_database,
)
from helpers.apps import on_session
from helpers.concurrency import LOCK_TIMEOUT, Committed, delete_made, newest_ids
from helpers.devices import new_device
from helpers.mail import SITE_MAIL, SentMail
from helpers.rows import setting_up
from helpers.sign_in import code, new_admin, sign_in_with

# structlog's lines go through the standard library, where caplog sees them.
configure_structlog()
log = structlog.get_logger(__name__)


# The app's settings a developer may export, which would change what tests
# see. DATABASE_URL and MIGRATION_DATABASE_URL are replaced, below.
FROM_THE_SHELL = (
    "APP_ENV",
    "UNMASK_SECRETS",
    "TRACE_REDACTION",
    "TRACE_REDACTION_OFF_UNTIL",
    "REDACTION_OFF_MAX_HOURS",
    "VITE_DEV_SERVER",
    "FRONTEND_DIST",
    "SETUP_TOKEN",
    "RECOVERY_TOKEN",
    "SITE_URL",
)


@pytest.fixture(scope="session", autouse=True)
def no_settings_from_the_shell(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[None]:
    """A developer's OTEL_*, LOG_* and app settings must not reach the tests,
    nor this clone's frontend/dist: apps find no scan page build unless a
    test gives one (test_bundle), as in CI's backend job, which has none.
    Session-scoped, so it runs before every module-scoped fixture too."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "inventory_tng.settings.CLONE_DIST",
            tmp_path_factory.getbasetemp() / "no-build",
        )
        for name in list(os.environ):
            if name.startswith(("OTEL_", "LOG_", "MAIL_")) or name in FROM_THE_SHELL:
                monkeypatch.delenv(name)
        # Code that reads DATABASE_URL or MIGRATION_DATABASE_URL itself, such
        # as a command's main(), finds no database at all rather than the
        # developer's, even after load_local_env() reads .env: a test that
        # needs one takes database_url, the run's own.
        for name in ("DATABASE_URL", "MIGRATION_DATABASE_URL"):
            monkeypatch.setenv(name, NO_DATABASE)
        yield


@pytest.fixture(scope="session", autouse=True)
def cheap_password_hashing() -> Iterator[None]:
    """argon2-cffi's cheapest parameters, so the tests that sign in do not
    spend most of the suite hashing. Still the library, only its settings
    (AGENTS.md rule 3)."""
    cheap = PasswordHasher.from_parameters(argon2.profiles.CHEAPEST)
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(accounts, "_hasher", cheap)
        monkeypatch.setattr(accounts, "_DUMMY_HASH", cheap.hash("not a password"))
        yield


@pytest.fixture(autouse=True)
def no_local_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A command a test runs loads no `.env` into the environment, where it
    would outlast the test and change what later ones see."""
    monkeypatch.setattr(local_env, "ENV_FILE", tmp_path / "no.env")


@pytest.fixture
def restore_logging() -> Iterator[None]:
    """For tests that call configure_logging(), which takes over the root and
    named loggers: put every logger back, so later tests keep caplog."""
    manager = logging.Logger.manager
    loggers = [logging.getLogger()]
    loggers += [
        lg for lg in manager.loggerDict.values() if isinstance(lg, logging.Logger)
    ]
    saved = [
        (lg, lg.handlers[:], lg.level, lg.propagate, lg.disabled) for lg in loggers
    ]
    yield
    for lg, handlers, level, propagate, disabled in saved:
        lg.handlers[:] = handlers
        lg.level, lg.propagate, lg.disabled = level, propagate, disabled
    configure_structlog()


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """The run's database, as the app logs in to it: inventory_app, which
    cannot change the ledger (DEPLOYMENT.md, Database roles)."""
    try:
        url = create_database()
    except Unreachable as error:
        pytest.exit(str(error), returncode=2)
    try:
        yield url
    finally:
        drop_database(url)


@pytest.fixture(scope="session")
def owner_engine(database_url: str) -> Iterator[Engine]:
    """The run's database as its owner, who migrated it."""
    with migrated_as_owner(database_url) as engine:
        yield engine


@pytest.fixture(scope="session")
def engine(database_url: str, owner_engine: Engine) -> Iterator[Engine]:
    """The run's database as the app, once migrated. Its login is checked
    here, once, as the app checks it at startup: apps the tests make skip
    that (make_app), and test_database_role.py tests the check itself."""
    del owner_engine
    engine = make_engine(database_url)
    with engine.connect() as connection:
        assert refusals(connection) == {}
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def migrated_template() -> Iterator[str]:
    """The name of a database with the schema and nothing else, which
    nothing stays connected to, so own_database can copy it."""
    with new_database() as url:
        with migrated_engine(url):
            pass
        yield make_url(url).database or ""


@pytest.fixture
def own_database(migrated_template: str) -> Iterator[str]:
    """The URL of a migrated database for this test alone, dropped after:
    for a test that commits what is never deleted, such as the ledger, or
    that needs no rows but its own."""
    with new_database(template=migrated_template) as url:
        yield url


@pytest.fixture
def own_engine(own_database: str) -> Iterator[Engine]:
    """An engine on own_database, disposed after."""
    engine = make_engine(own_database)
    yield engine
    engine.dispose()


@pytest.fixture
def alembic_config(owner_engine: Engine) -> Iterator[Config]:
    """Migrations run inside a transaction that is rolled back after the test,
    so a failed downgrade cannot leave later tests without a schema."""
    with owner_engine.connect() as connection:
        transaction = connection.begin()
        yield alembic_config_for(connection)
        transaction.rollback()


def _rolled_back(engine: Engine) -> Iterator[Session]:
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(
            bind=connection, join_transaction_mode="create_savepoint"
        ) as session:
            yield setting_up(session)
        transaction.rollback()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """A session whose work is rolled back after the test, even if the code
    under test commits."""
    yield from _rolled_back(engine)


@pytest.fixture
def owner_session(owner_engine: Engine) -> Iterator[Session]:
    """As `session`, logged in as the owner: for what only the owner may do,
    such as TRUNCATE."""
    yield from _rolled_back(owner_engine)


@functools.cache
def _fingerprint_query(engine: Engine) -> TextClause:
    """One query for every table's fingerprint (PostgreSQL's md5 of all its
    rows, in order), built once per run, as the schema doesn't change
    between tests. Every table in it, alembic_version too, not only the
    models'. Without a row's edit_version (models.Versioned), which a row
    changed and put back still moves on."""
    with engine.connect() as connection:
        tables = connection.scalars(
            text(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"
            )
        ).all()
    return text(
        " UNION ALL ".join(
            f"SELECT '{name}', md5(coalesce(string_agg((to_jsonb(t) - 'edit_version')"
            f"::text, ',' ORDER BY (to_jsonb(t) - 'edit_version')::text), ''))"
            f' FROM "{name}" t'
            for name in tables
        )
    )


def _fingerprints(engine: Engine) -> dict[str, str]:
    """Every table's fingerprint: any row added, changed or deleted changes
    its table's."""
    with engine.connect() as connection:
        return dict(connection.execute(_fingerprint_query(engine)).all())


# On each module: the tables a test's own check already failed it for, so
# the module's check doesn't blame the same rows on another test. A second
# leak into one of these tables in that module shows once the first is fixed.
_REPORTED = pytest.StashKey[set[str]]()


@contextlib.contextmanager
def _unchanged(engine: Engine, what: str, module: pytest.Module) -> Generator[None]:
    """Fails `what` if the run's database differs after the block, in any
    table not already reported in `module`."""
    before = _fingerprints(engine)
    yield
    after = _fingerprints(engine)
    reported = module.stash.setdefault(_REPORTED, set())
    changed = sorted(
        name for name in before if before[name] != after[name] and name not in reported
    )
    reported.update(changed)
    if changed:
        pytest.fail(f"{what} left rows behind in {changed}", pytrace=False)


@functools.cache
def _modules_using_the_database(session: pytest.Session) -> frozenset[str]:
    """The modules with a test that takes database_url, found once a run."""
    return frozenset(
        item.module.__name__
        for item in session.items
        if isinstance(item, pytest.Function) and "database_url" in item.fixturenames
    )


@pytest.fixture(autouse=True)
def leaves_the_database_as_it_was(request: pytest.FixtureRequest) -> Iterator[None]:
    """Each test that uses the database leaves it as it found it, so no test
    depends on another, or on their order. One that commits rows nothing
    cleans up fails here, itself, rather than a later test that happens to
    count them. Autouse, so set up before the test's other fixtures and torn
    down after them (the session's rollback, committed's cleanup). A test
    that takes database_url gets the run's database migrated (owner_engine),
    as the check needs."""
    if "database_url" not in request.fixturenames:
        yield
        return
    module = request.node.getparent(pytest.Module)
    assert module is not None  # Every test here is in a module.
    owner = request.getfixturevalue("owner_engine")
    # Attempts counted (rate_limits.py) commit whatever the test does, in
    # transactions of their own: each test leaves none for the next.
    with _unchanged(owner, "the test", module):
        yield
        _no_rate_limits(owner)


def _no_rate_limits(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(delete(RateLimit))


@pytest.fixture(scope="module", autouse=True)
def module_leaves_the_database_as_it_was(
    request: pytest.FixtureRequest,
) -> Iterator[None]:
    """As leaves_the_database_as_it_was, around a whole module: what its
    module-scoped fixtures commit, as they are set up or torn down, which no
    one test's check sees."""
    if request.module.__name__ not in _modules_using_the_database(request.session):
        yield
        return
    engine = request.getfixturevalue("owner_engine")
    with _unchanged(engine, "the module", request.node):
        yield


# Names these sessions' connections, so cleanup can end any left waiting.
COMMITTED_APP = "inventory-tng-committed-test"


def _wait_briefly(connection: DBAPIConnection, _record: object) -> None:
    """At most LOCK_TIMEOUT for a lock, so a deadlock or a missing wait
    fails rather than hangs; and named, for cleanup."""
    run_outside_transaction(
        connection,
        f"SET lock_timeout = '{LOCK_TIMEOUT}'",
        f"SET application_name = '{COMMITTED_APP}'",
    )


@contextlib.contextmanager
def _committed_engine(url: str) -> Generator[Committed]:
    """Committed sessions on `url`, whose connections wait as _wait_briefly
    says. After, any still waiting is ended, so its thread finishes before
    its session is closed; then they all are."""
    own = make_engine(url)
    event.listen(own, "connect", _wait_briefly)
    committed = Committed(own)
    yield committed
    with own.connect() as connection:
        connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                " WHERE application_name = :app AND datname = current_database()"
                " AND pid <> pg_backend_pid()"
            ),
            {"app": COMMITTED_APP},
        )
    for worker in committed.workers:
        worker.join(timeout=10)
    # A session is not to be used from two threads: leave them all.
    if any(worker.is_alive() for worker in committed.workers):
        raise RuntimeError("a worker never finished; its rows are left behind")
    for session in committed.sessions:
        with contextlib.suppress(exc.DBAPIError):
            session.close()
    own.dispose()


@pytest.fixture
def _clients() -> Iterator[list[TestClient]]:
    """Every client make_client makes, closed after the test."""
    clients: list[TestClient] = []
    yield clients
    for client in clients:
        client.__exit__(None, None, None)


# Both take _clients, so they end any request a worker still has in flight
# before its client closes, which would wait for it forever (8i4.118).


@pytest.fixture
def own_committed(own_database: str, _clients: list[TestClient]) -> Iterator[Committed]:
    """committed's sessions, on own_database: for two requests at once
    where one writes the ledger, which is never deleted. The database goes
    with the test, so nothing is cleaned up but the connections."""
    del _clients
    with _committed_engine(own_database) as committed:
        yield committed


@pytest.fixture
def committed(
    database_url: str, owner_engine: Engine, _clients: list[TestClient]
) -> Iterator[Committed]:
    """Two requests at once need commits other connections see, which the
    `session` fixture's one transaction is not (_committed_engine). After
    the test, every row they made (any table, any connection, history
    included) is deleted, as the owner, waiting as briefly for a lock as
    they do (delete_made). Rows they changed but did not make,
    such as a settings row, are not put back: a test that changes one
    restores it itself. A committed test must not write the ledger, which
    is append-only: not even a test deletes from it."""
    del _clients
    with owner_engine.connect() as connection:
        before = newest_ids(connection)
    with _committed_engine(database_url) as committed:
        yield committed
    with owner_engine.connect() as connection:
        after = newest_ids(connection)
    if grew := sorted(t for t in LEDGER if after[t] > before[t]):
        raise RuntimeError(
            f"a committed test wrote {grew}, whose rows are never deleted"
            " (the ledger is append-only): its rows are left behind"
        )
    delete_made(owner_engine, before)


@pytest.fixture
def make_app(database_url: str, session: Session) -> Callable[..., FastAPI]:
    """Apps on the test database, whose requests use the test's session, or
    the one given `on`."""

    def make(
        logging: LogConfig | None = None,
        frontend: FrontendConfig | None = None,
        *,
        development: bool = False,
        setup_token: str | None = None,
        recovery_token: str | None = None,
        mailer: SentMail | None = None,
        own_sessions: bool = False,
        on: Session | None = None,
    ) -> FastAPI:
        settings = Settings(
            database_url=database_url,
            development=development,
            setup_token=setup_token,
            recovery_token=recovery_token,
            logging=logging or LogConfig(),
            frontend=frontend or FrontendConfig(),
        )
        if mailer is not None:
            settings = dataclasses.replace(settings, mail=SITE_MAIL)
        # Its login is checked once, by `engine`.
        app = create_app(settings, check_login=False)
        app.dependency_overrides[get_mailer] = lambda: mailer
        # Each request with a session of its own, as in production, on the
        # database as committed (the `committed` fixture cleans up).
        if own_sessions:
            event.listen(app.state.engine, "connect", _wait_briefly)
            return app
        app.dependency_overrides |= on_session(on or session)
        return app

    return make


@pytest.fixture
def admin_client(
    make_client: Callable[..., TestClient], session: Session
) -> TestClient:
    """A client signed in as an administrator, Ada, with the confirmation
    window open."""
    client = make_client()
    _, key = new_admin(session)
    assert sign_in_with(client, code=code(key)).status_code == 303
    return client


@pytest.fixture
def make_client(
    make_app: Callable[..., FastAPI], _clients: list[TestClient]
) -> Callable[..., TestClient]:
    """Clients over https, as production is, for the Secure cookies, not
    following redirects, which are what is tested. `address` is where the
    request seems to come from, for rate limits by address."""

    def make(
        app: FastAPI | None = None, *, address: str = "192.0.2.1", **app_kwargs: object
    ) -> TestClient:
        client = TestClient(
            app or make_app(**app_kwargs),
            base_url="https://testserver",
            follow_redirects=False,
            client=(address, 50000),
        )
        _clients.append(client.__enter__())
        return client

    return make


@pytest.fixture
def sent_mail() -> SentMail:
    return SentMail()


@pytest.fixture
def client(make_app: Callable[..., FastAPI]) -> Iterator[TestClient]:
    """Over https, as production is: the app's cookies are Secure, and would
    not come back over plain http."""
    with TestClient(make_app(), base_url="https://testserver") as client:
        yield client


@pytest.fixture
def registered_client(client: TestClient, session: Session) -> TestClient:
    """The app's client, as a registered device."""
    _, token = new_device(session)
    client.cookies.set(devices.COOKIE, token)
    return client


# In a constant, because tracebacks show the code line that raised, and code
# lines are not masked.
QUOTING_QUERY = "SELECT 'jane@example.org'::int"


@pytest.fixture
def database_error(engine: Engine) -> Iterator[exc.DBAPIError]:
    """A real database error whose message quotes a value, as PostgreSQL's
    do: `invalid input syntax for type integer: "jane@example.org"`."""
    with engine.connect() as connection:
        with pytest.raises(exc.DBAPIError) as raised:
            connection.execute(text(QUOTING_QUERY))
        yield raised.value
