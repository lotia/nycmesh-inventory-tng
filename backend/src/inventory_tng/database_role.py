"""The check the app makes of its database login as it starts
(DEPLOYMENT.md, Database roles)."""

import structlog
from sqlalchemy import Connection, Engine, column, exc, func, select, table

from inventory_tng.db import describe_error
from inventory_tng.history import HISTORY_TABLE

log = structlog.get_logger(__name__)

LEDGER = ("ledger_transaction", "movement")
# Tables the app only adds to: the ledger, and history (history.py).
APPEND_ONLY = (*LEDGER, HISTORY_TABLE)
# The one-row settings tables, which every page reads: the app changes
# their row, made by their migrations, but never deletes it.
KEPT = ("scan_settings", "sign_in_settings", "site_settings")
# Privileges, by table.
type Privileges = dict[str, tuple[str, ...]]
# What the login must not hold: what would change or empty a table it only
# adds to, or empty a settings table.
FORBIDDEN: Privileges = {
    name: ("UPDATE", "DELETE", "TRUNCATE") for name in APPEND_ONLY
} | {name: ("DELETE", "TRUNCATE") for name in KEPT}
# What it needs to record anything: a login without these is not the app's.
NEEDED: Privileges = {name: ("SELECT", "INSERT") for name in APPEND_ONLY}


def named(privileges: Privileges) -> list[str]:
    """Each privilege on each table, as "UPDATE movement"."""
    return [f"{p} {name}" for name, held in privileges.items() for p in held]


_PG_TABLES = table(
    "pg_tables",
    column("schemaname"),
    column("tablename"),
    column("tableowner"),
    schema="pg_catalog",
)


class RefusedLogin(Exception):
    """The app's database login could change the ledger or history, empty a
    settings table, cannot record, or owns a table."""


def refusals(connection: Connection) -> dict[str, list[str]]:
    """What is wrong with the login `connection` uses, by kind: "may"
    (FORBIDDEN privileges it has, as "UPDATE movement"), "lacks" (NEEDED
    ones it has not), "owns" (tables). Empty if nothing is."""
    pairs = [
        (name, privilege)
        for privileges in (FORBIDDEN, NEEDED)
        for name, held in privileges.items()
        for privilege in held
    ]
    held = connection.execute(
        select(*(func.has_table_privilege(name, p) for name, p in pairs))
    ).one()
    may, needed = named(FORBIDDEN), named(NEEDED)
    has_forbidden, has_needed = held[: len(may)], held[len(may) :]
    found = {
        "may": [n for n, has in zip(may, has_forbidden, strict=True) if has],
        "lacks": [n for n, has in zip(needed, has_needed, strict=True) if not has],
    }
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
