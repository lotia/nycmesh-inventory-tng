"""Each test run gets a database of its own, created on the compose PostgreSQL
server and dropped when the run ends. Each test's writes are rolled back."""

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from inventory_tng.app import create_app
from inventory_tng.db import get_session, make_engine
from inventory_tng.settings import Settings

PYPROJECT = Path(__file__).parents[1] / "pyproject.toml"

# The port compose uses, from the shell or the .env compose reads, so the tests
# find the database however they are run.
PORT = (
    os.environ.get("INVENTORY_DB_PORT")
    or dotenv_values(Path(__file__).parents[2] / ".env").get("INVENTORY_DB_PORT")
    or "5433"
)
SERVER_URL = Settings(
    database_url=os.environ.get("DATABASE_URL")
    or f"postgresql://inventory:inventory@localhost:{PORT}/inventory"
).database_url


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    name = f"test_{uuid.uuid4().hex[:12]}"
    server = make_engine(SERVER_URL).execution_options(isolation_level="AUTOCOMMIT")
    try:
        with server.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
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
def client(database_url: str, session: Session) -> Iterator[TestClient]:
    app = create_app(Settings(database_url=database_url))
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield client
