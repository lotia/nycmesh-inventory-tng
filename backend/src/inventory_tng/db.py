"""The database: one engine per app, one session per request."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import Engine, MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session


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
    # holding a worker until the operating system gives up.
    return create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5})


def get_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:
        yield session


DbSession = Annotated[Session, Depends(get_session)]
