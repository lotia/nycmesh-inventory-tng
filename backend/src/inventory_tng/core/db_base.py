"""Base and the naming convention its constraints are named by. Not in
models/base.py, which imports core/history.py for its flush listeners:
history imports Base, so that would be a cycle."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


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
