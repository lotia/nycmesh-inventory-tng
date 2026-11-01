"""The database: one engine per app, one session per request."""

import re
from collections.abc import Iterator
from typing import Annotated

import structlog
from fastapi import Depends, Request
from sqlalchemy import Engine, MetaData, create_engine, exc, func, select
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
    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
        hide_parameters=True,
    )


def get_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:
        yield session


DbSession = Annotated[Session, Depends(get_session)]

# Read by the history trigger (migration 0002, which writes the name out):
# who made this transaction's changes.
ACTOR_SETTING = "inventory.actor"
# Also catalogue_history's check constraint (models.py).
ACTOR_PATTERN = "(admin|volunteer):[0-9]+"
ACTOR = re.compile(ACTOR_PATTERN)


def set_actor(session: Session, actor: str) -> None:
    """Name who is making this transaction's changes: "admin:3" or
    "volunteer:7", ids and never names, so history holds no personal data
    through it. Lasts until the transaction ends."""
    if not ACTOR.fullmatch(actor):
        raise ValueError("an actor is admin:<id> or volunteer:<id>")
    session.execute(select(func.set_config(ACTOR_SETTING, actor, True)))
    log.debug("actor set", actor=actor)


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
