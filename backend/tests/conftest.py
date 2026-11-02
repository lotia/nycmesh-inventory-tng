"""Each test run gets a database of its own, created on the compose PostgreSQL
server and dropped when the run ends. Each test's writes are rolled back."""

import dataclasses
import logging
import os
import re
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import nullcontext
from datetime import timedelta
from pathlib import Path

import pytest
import structlog
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, exc, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from inventory_tng import devices
from inventory_tng.app import create_app
from inventory_tng.db import get_session, get_session_factory, make_engine
from inventory_tng.local_env import local_env_values
from inventory_tng.logs import configure_structlog
from inventory_tng.mail import Message, get_mailer
from inventory_tng.rate_limits import limiter
from inventory_tng.settings import FrontendConfig, LogConfig, MailConfig, Settings

# structlog's lines go through the standard library, where caplog sees them.
configure_structlog()
log = structlog.get_logger(__name__)


# The app's settings a developer may export, which would change what tests
# see. DATABASE_URL is replaced, below.
FROM_THE_SHELL = (
    "APP_ENV",
    "UNMASK_SECRETS",
    "VITE_DEV_SERVER",
    "FRONTEND_DIST",
    "SETUP_TOKEN",
    "SITE_URL",
)


@pytest.fixture(scope="session", autouse=True)
def no_settings_from_the_shell() -> Iterator[None]:
    """A developer's OTEL_*, LOG_* and app settings must not reach the tests.
    Session-scoped, so it runs before every module-scoped fixture too."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        for name in list(os.environ):
            if name.startswith(("OTEL_", "LOG_", "MAIL_")) or name in FROM_THE_SHELL:
                monkeypatch.delenv(name)
        # Code that reads DATABASE_URL itself, such as a command's main(),
        # finds no database at all rather than the developer's: a test that
        # needs one takes database_url, the run's own.
        monkeypatch.setenv("DATABASE_URL", NO_DATABASE)
        yield


@pytest.fixture(autouse=True)
def fresh_rate_limits() -> None:
    """Every test starts with no attempts counted against it."""
    limiter.storage.reset()


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


PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"

# The database compose starts: from the shell, else the repository's .env
# (CONTRIBUTING.md, Local settings), else compose's default port.
LOCAL = local_env_values()
PORT = os.environ.get("INVENTORY_DB_PORT") or LOCAL.get("INVENTORY_DB_PORT") or "5433"
SERVER_URL = Settings(
    database_url=os.environ.get("DATABASE_URL")
    or LOCAL.get("DATABASE_URL")
    or f"postgresql://inventory:inventory@localhost:{PORT}/inventory"
).database_url


# What DATABASE_URL is during the tests: nothing listens on port 1.
NO_DATABASE = "postgresql://nobody@127.0.0.1:1/use_the_database_url_fixture"

# Each run's database is named for when it began, so a run that never got to
# drop its own (killed, or crashed) is dropped by a later one.
TEST_DATABASE = re.compile(r"test_(\d+)_[0-9a-f]{8}")
LEFT_BEHIND = timedelta(hours=1)


def drop_left_behind(server: Engine, now: float) -> list[str]:
    """Test databases from runs that began over an hour ago and that nothing
    is connected to: never a run still going."""
    with server.connect() as connection:
        idle = connection.scalars(
            text(
                "SELECT datname FROM pg_database WHERE datname LIKE 'test\\_%' "
                "AND datname NOT IN (SELECT datname FROM pg_stat_activity "
                "WHERE datname IS NOT NULL)"
            )
        ).all()
        dropped = []
        for name in idle:
            match = TEST_DATABASE.fullmatch(name)
            if match and now - int(match.group(1)) > LEFT_BEHIND.total_seconds():
                connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
                dropped.append(name)
    if dropped:
        log.info("dropped test databases left behind", databases=dropped)
    return dropped


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    name = f"test_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    server = make_engine(SERVER_URL).execution_options(isolation_level="AUTOCOMMIT")
    try:
        drop_left_behind(server, time.time())
        with server.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        log.info("created test database", database=name)
    except OperationalError as error:
        pytest.exit(
            f"Cannot reach PostgreSQL at {make_url(SERVER_URL)}. Start it with "
            f"`docker compose up -d --wait db` (or `podman compose`).\n{error}",
            returncode=2,
        )
    try:
        url = make_url(SERVER_URL).set(database=name)
        yield url.render_as_string(hide_password=False)
    finally:
        with server.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        log.info("dropped test database", database=name)
        server.dispose()


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    engine = make_engine(database_url)
    with engine.begin() as connection:
        command.upgrade(_alembic_config(connection), "head")
    yield engine
    engine.dispose()


@pytest.fixture
def alembic_config(engine: Engine) -> Iterator[Config]:
    """Migrations run inside a transaction that is rolled back after the test,
    so a failed downgrade cannot leave later tests without a schema."""
    with engine.connect() as connection:
        transaction = connection.begin()
        yield _alembic_config(connection)
        transaction.rollback()


def _alembic_config(connection: Connection) -> Config:
    config = Config(toml_file=PYPROJECT)
    config.attributes["connection"] = connection
    return config


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """A session whose work is rolled back after the test, even if the code
    under test commits."""
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(
            bind=connection, join_transaction_mode="create_savepoint"
        ) as session:
            yield session
        transaction.rollback()


@pytest.fixture
def make_app(
    database_url: str, session: Session, tmp_path: Path
) -> Callable[..., FastAPI]:
    """Apps on the test database, whose requests use the test's session. Their
    bundle is an empty directory unless a test gives one, so no test depends
    on whether this clone has a build."""

    def make(
        logging: LogConfig | None = None,
        frontend: FrontendConfig | None = None,
        *,
        development: bool = False,
        setup_token: str | None = None,
        mailer: SentMail | None = None,
    ) -> FastAPI:
        settings = Settings(
            database_url=database_url,
            development=development,
            setup_token=setup_token,
            logging=logging or LogConfig(),
            frontend=frontend or FrontendConfig(dist=tmp_path / "no-build"),
        )
        if mailer is not None:
            settings = dataclasses.replace(settings, mail=SITE_MAIL)
        app = create_app(settings)
        app.dependency_overrides[get_session] = lambda: session
        # Work after the reply uses the test's session too, so it is rolled
        # back with the rest.
        app.dependency_overrides[get_session_factory] = lambda: (
            lambda: nullcontext(session)
        )
        app.dependency_overrides[get_mailer] = lambda: mailer
        return app

    return make


# Mail as a site sets it up, for apps given a mailer. Links start here.
SITE_MAIL = MailConfig(
    host="smtp.example.org",
    site_url="https://inventory.example.org",
    sender="inventory@example.org",
)


class SentMail(list[Message]):
    """A stand-in mailer: what would have gone out."""

    def send(self, message: Message) -> None:
        self.append(message)


class FailingMail:
    """A stand-in mailer whose every send fails with `error`."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def send(self, message: Message) -> None:
        raise self.error


@pytest.fixture
def make_client(
    make_app: Callable[..., FastAPI],
) -> Iterator[Callable[..., TestClient]]:
    """Clients over https, as production is, for the Secure cookies, not
    following redirects, which are what is tested. `address` is where the
    request seems to come from, for rate limits by address."""
    clients: list[TestClient] = []

    def make(
        app: FastAPI | None = None, *, address: str = "192.0.2.1", **app_kwargs: object
    ) -> TestClient:
        client = TestClient(
            app or make_app(**app_kwargs),
            base_url="https://testserver",
            follow_redirects=False,
            client=(address, 50000),
        )
        clients.append(client.__enter__())
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


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
    from test_devices import new_device

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
