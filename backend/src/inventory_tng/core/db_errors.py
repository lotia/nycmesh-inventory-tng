"""What a database error means: a rule broken, a row gone, or the database
unable to answer just now, which is a 503."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from opentelemetry import metrics
from pydantic import BaseModel
from sqlalchemy import exc
from sqlalchemy.orm import Session

if TYPE_CHECKING:  # db_sessions imports this module.
    from inventory_tng.core.db_sessions import PoolName

log = structlog.get_logger(__name__)


class PoolTimeout(exc.TimeoutError):
    """No connection from one of the app's pools, or no place for a
    request's session, within db_sessions.Database.timeout: which
    (pool_timeouts)."""

    def __init__(self, pool: PoolName) -> None:
        super().__init__(f"no connection from the {pool} pool in time")
        self.pool = pool


class NoSession(PoolTimeout):
    """No place for the request's session within
    db_sessions.Database.timeout."""

    def __init__(self) -> None:
        super().__init__("requests")


class RuleBroken(Exception):
    """A change the app's rules refuse (the catalogue's, the ledger's, the
    site settings'), by the rule's name, as web.forms.RULES puts into
    words. Raised before anything is written."""

    def __init__(self, rule: str) -> None:
        super().__init__(rule)
        self.rule = rule


class Gone(Exception):
    """The row a change is for is no longer as the page found it, such as
    retired meanwhile by another request: there is nothing here to change,
    and a page answers not found (pages.ErrorPages)."""


def enforce(broken: str | None, *, when: bool = True) -> None:
    """Raise RuleBroken for `broken`, a rule's name, if `when`: as a rule
    function returns it, or named for a rule of one condition, as in
    enforce("category_has_items", when=has_items)."""
    if broken is not None and when:
        log.debug("rule broken", rule=broken)
        raise RuleBroken(broken)


@dataclass(frozen=True)
class Broken:
    """The rule a change broke, by the name attempt() gives it, and the
    unique index, if it broke one and the database said which."""

    rule: str
    index: str | None = None


def _attempted[T](
    session: Session, change: Callable[[], T]
) -> tuple[T | None, Broken | None]:
    """attempt(), saying the unique index broken too."""
    try:
        with session.begin_nested():
            done = change()
            session.flush()
    except BaseException as error:
        if isinstance(error, RuleBroken):
            return None, Broken(error.rule)
        if isinstance(error, exc.IntegrityError):
            sqlstate = getattr(error.orig, "sqlstate", None)
            constraint = describe_error(error).get("constraint")
            if sqlstate == UNIQUE_VIOLATION:
                return None, Broken("unique", constraint)
            if sqlstate == CHECK_VIOLATION and constraint:
                return None, Broken(constraint)
        # Such as Gone, or any other failure: raised.
        raise
    return done, None


def attempt[T](
    session: Session, change: Callable[[], T]
) -> tuple[T | None, str | None]:
    """Run `change` in a savepoint, with nothing of it kept if it breaks a
    rule: what it returned, and None; or None, and "unique" if it broke a
    unique index (as each table's index compares, such as an item's name
    ignoring case, spaces and punctuation, retired items' too), the name of
    the check constraint it broke, or the rule a domain function refused it
    by (RuleBroken), so a page can say why in its own words. Any other
    failure is raised. Locks taken in `change` go with the savepoint,
    however it failed (db_locking._savepoint_locks_released)."""
    done, broken = _attempted(session, change)
    return done, broken.rule if broken else None


def refusal_naming_index[T](
    session: Session, change: Callable[[], T], *expected: str
) -> tuple[T | None, Broken | None]:
    """refusal(), saying which unique index a "unique" refusal broke, for a
    page whose words differ by index."""
    done, broken = _attempted(session, change)
    if broken is not None and broken.rule not in expected:
        raise ValueError(f"refused by {broken.rule}, not {', '.join(expected)}")
    return done, broken


def refusal[T](
    session: Session, change: Callable[[], T], *expected: str
) -> tuple[T | None, str | None]:
    """attempt(), for a change only the `expected` rules may refuse. Any
    other rule broken is a bug, and raised."""
    done, broken = refusal_naming_index(session, change, *expected)
    return done, broken.rule if broken else None


def unique(session: Session, change: Callable[[], object]) -> bool:
    """attempt(), for a change only a unique index can refuse: False if it
    did."""
    return refusal(session, change, "unique")[1] is None


UNIQUE_VIOLATION = "23505"
CHECK_VIOLATION = "23514"


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


LOCK_NOT_AVAILABLE = "55P03"  # A lock waited for longer than LOCK_TIMEOUT.

# OperationalErrors that trying again can cure. Any other is a bug, and
# stays a 500: retrying it forever would hide it.
RETRYABLE_SQLSTATES = (
    "08",  # The connection failed or was lost.
    "40001",  # Serialization failure.
    "40P01",  # Deadlock.
    LOCK_NOT_AVAILABLE,
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


pool_timeouts = metrics.get_meter(__name__).create_counter(
    "db.client.connection.timeouts",
    unit="{timeout}",
    description="Requests that found no free connection in time, by pool",
)


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
        if isinstance(error, exc.TimeoutError):
            # Too many requests at once for the request pool (NoSession), or
            # the limits pool busy. The request pool's own connections are
            # never waited for, as no more requests hold one than it has;
            # the side pool's last seen is skipped
            # (db_sessions.record_last_seen).
            pool = error.pool if isinstance(error, PoolTimeout) else "side"
            pool_timeouts.add(1, {"db.client.connection.pool.name": pool})
        log.warning("database unavailable", **describe_error(error))
        body = UnavailableReply(reason="database_unavailable")
        return JSONResponse(body.model_dump(), status_code=503)
