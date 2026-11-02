"""The database: one engine per app, one session per request."""

import re
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Any, Literal

import structlog
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import (
    ColumnElement,
    Engine,
    MetaData,
    create_engine,
    event,
    exc,
    exists,
    func,
    select,
    update,
)
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import DeclarativeBase, Session

log = structlog.get_logger(__name__)


class Base(DeclarativeBase):
    """Every mapped class derives from this. Alembic compares its metadata
    with the database."""

    # Named constraints, so a later migration can drop or alter them by name.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def make_engine(url: str) -> Engine:
    # A database that drops packets fails a request in seconds instead of
    # holding a worker until the operating system gives up. Parameters are
    # hidden from SQLAlchemy's log lines and messages, because they can hold
    # personal data.
    engine = create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
        hide_parameters=True,
    )
    event.listen(engine, "connect", _in_utc)
    return engine


def _in_utc(connection: DBAPIConnection, _record: object) -> None:
    """Times come back in UTC whatever the server's own zone
    (docs/decisions/0013). Set as each connection opens, rather than as a
    startup option, which would replace any options DATABASE_URL gives and
    which connection poolers such as PgBouncer refuse."""
    cursor = connection.cursor()
    cursor.execute("SET TIME ZONE 'UTC'")
    cursor.close()
    connection.commit()


def get_session(request: Request) -> Iterator[Session]:
    # Rows stay loaded after a commit: a page that commits then shows them
    # does not read them all again.
    with Session(request.app.state.engine, expire_on_commit=False) as session:
        yield session


DbSession = Annotated[Session, Depends(get_session)]

# Read by the history trigger (migration 0002, which writes the name out):
# who made this transaction's changes.
ACTOR_SETTING = "inventory.actor"
# Also catalogue_history's check constraint (models.py, migration 0003).
# system:<name> is anything that is not a person, such as the command line.
CLI_ACTOR = "system:cli"
ACTOR_PATTERN = "(admin|volunteer):[0-9]+|system:[a-z_]+"
ACTOR = re.compile(ACTOR_PATTERN)


def set_actor(session: Session, actor: str) -> None:
    """Name who is making this transaction's changes: "admin:3",
    "volunteer:7", or "system:cli" for something that is not a person; ids
    and never names, so history holds no personal data through it. Lasts
    until the transaction ends."""
    if not ACTOR.fullmatch(actor):
        raise ValueError("an actor is admin:<id>, volunteer:<id> or system:<name>")
    session.execute(select(func.set_config(ACTOR_SETTING, actor, True)))
    log.debug("actor set", actor=actor)


def exists_where(session: Session, *criteria: ColumnElement[bool]) -> bool:
    """Whether any row matches, as one EXISTS query."""
    return bool(session.scalar(select(exists().where(*criteria))))


def record_last_seen(
    engine: Engine, model: type[Any], row_id: int, now: datetime
) -> None:
    """Set a row's last_seen_at in a transaction of its own, so a request
    that saves nothing still counts, and the route's own transaction is left
    alone. A row the route has not committed is not seen, which is fine."""
    with Session(engine) as session, session.begin():
        session.execute(
            update(model).where(model.id == row_id).values(last_seen_at=now)
        )


def describe_error(error: exc.SQLAlchemyError) -> dict[str, str]:
    """What may be logged about a database error. Never its message:
    PostgreSQL's text can quote values, as a unique violation names the
    duplicate."""
    orig = getattr(error, "orig", None)
    fields = {"error": type(orig or error).__name__}
    if sqlstate := getattr(orig, "sqlstate", None):
        fields["sqlstate"] = sqlstate
    if constraint := getattr(getattr(orig, "diag", None), "constraint_name", None):
        fields["constraint"] = constraint
    return fields


# OperationalErrors that trying again can cure. Any other is a bug, and
# stays a 500: retrying it forever would hide it.
RETRYABLE_SQLSTATES = (
    "08",  # The connection failed or was lost.
    "40001",  # Serialization failure.
    "40P01",  # Deadlock.
    "53300",  # Too many connections.
    "57014",  # A statement timed out.
    "57P01",  # The server is shutting down...
    "57P02",
    "57P03",  # ...or is not accepting connections yet.
)


class UnavailableReply(BaseModel):
    reason: Literal["database_unavailable"]


# Declared for every route, since any of them can meet it (app.py).
UNAVAILABLE: dict[int | str, dict[str, Any]] = {
    503: {
        "model": UnavailableReply,
        "description": "The database cannot answer; try again",
    }
}


def install(app: FastAPI) -> None:
    """A database the app cannot reach, or that cannot answer just now, is a
    503 on any route, so a client such as the scan page's outbox knows to try
    again."""

    @app.exception_handler(exc.OperationalError)
    @app.exception_handler(exc.TimeoutError)
    def unavailable(_request: Request, error: exc.SQLAlchemyError) -> JSONResponse:
        sqlstate = getattr(getattr(error, "orig", None), "sqlstate", None)
        # No sqlstate: the server never answered, as when it is down.
        if sqlstate and not sqlstate.startswith(RETRYABLE_SQLSTATES):
            raise error
        log.warning("database unavailable", **describe_error(error))
        body = UnavailableReply(reason="database_unavailable")
        return JSONResponse(body.model_dump(), status_code=503)
