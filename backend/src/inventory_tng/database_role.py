"""The check the app makes of its database login as it starts
(DEPLOYMENT.md, Database roles)."""

import structlog
from sqlalchemy import Connection, Engine, column, exc, func, select, table

from inventory_tng.db import describe_error

log = structlog.get_logger(__name__)

LEDGER = ("ledger_transaction", "movement")
# What would let the login change the ledger or empty it.
FORBIDDEN = ("UPDATE", "DELETE", "TRUNCATE")
# What it needs to record anything: a login without these is not the app's.
NEEDED = ("SELECT", "INSERT")

_PG_TABLES = table(
    "pg_tables",
    column("schemaname"),
    column("tablename"),
    column("tableowner"),
    schema="pg_catalog",
)


class RefusedLogin(Exception):
    """The app's database login could change the ledger, cannot record, or
    owns a table."""


def refusals(connection: Connection) -> dict[str, list[str]]:
    """What is wrong with the login `connection` uses, by kind: "may"
    (privileges on the ledger it must not have, as "UPDATE movement"),
    "lacks" (ones it needs), "owns" (tables). Empty if nothing is."""
    pairs = [(name, privilege) for name in LEDGER for privilege in FORBIDDEN + NEEDED]
    held = connection.execute(
        select(*(func.has_table_privilege(name, p) for name, p in pairs))
    ).one()
    found: dict[str, list[str]] = {"may": [], "lacks": []}
    for (name, privilege), has in zip(pairs, held, strict=True):
        if has and privilege in FORBIDDEN:
            found["may"].append(f"{privilege} {name}")
        elif not has and privilege in NEEDED:
            found["lacks"].append(f"{privilege} {name}")
    found["owns"] = list(
        connection.scalars(
            select(_PG_TABLES.c.tablename)
            .where(
                _PG_TABLES.c.tableowner == func.current_user(),
                _PG_TABLES.c.schemaname.not_in(["pg_catalog", "information_schema"]),
            )
            .order_by(_PG_TABLES.c.tablename)
        )
    )
    return {kind: names for kind, names in found.items() if names}


def check(engine: Engine) -> None:
    """Raise RefusedLogin, after logging why, unless the engine's login is
    one the app may run as. A database it cannot reach, or one not yet
    migrated, is refused too: it could not tell."""
    try:
        with engine.connect() as connection:
            found = refusals(connection)
    except exc.SQLAlchemyError as error:
        log.error("database login not checked", **describe_error(error))
        raise RefusedLogin("the database login could not be checked") from error
    if found:
        log.error("database login refused", **found)
        raise RefusedLogin(f"the database login is refused: {found}")
    log.debug("database login checked")
