"""Database connections under load (inventory-tng-8i4.125): no request
waits on the pool its own connection came from, no thread waits for a
connection that a request waiting for a thread holds, and a lock waited
for too long frees its connection."""

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Annotated, cast
from unittest.mock import patch

import anyio.to_thread
import httpx2
import pytest
from fastapi import Depends, Request
from limits import RateLimitItemPerMinute
from psycopg.errors import ConnectionFailure, LockNotAvailable
from pydantic import BaseModel
from sqlalchemy import Engine, exc, text
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng import db
from inventory_tng.app import create_app
from inventory_tng.db import (
    LOCK_TIMEOUT,
    Database,
    DbSession,
    PoolTimeout,
    make_database,
)

# Imported before conftest replaces it in every test.
from inventory_tng.db import pool_order_broken as logs_pool_order_broken
from inventory_tng.models import Device
from inventory_tng.rate_limits import limiter
from inventory_tng.settings import Settings

from helpers.pools import small_database


def test_more_requests_than_threads_and_connections_do_not_stall(
    database_url: str,
) -> None:
    """Four threads and two connections for forty requests at once, on
    routes whose connection is held across threads: a dependency that reads
    first, then the route, then the check of the model it returns. Before
    db.session_slot, threads waited for connections held by requests
    waiting for threads, until the pool timed out: 503s after 10 seconds."""
    app = create_app(Settings(database_url=database_url))
    app.state.db.dispose()
    app.state.db = make_database(database_url, pool=(2, 0))

    def reads_first(session: DbSession) -> Session:
        session.execute(text("SELECT 1"))
        return session

    class Reply(BaseModel):
        ok: bool

    def route(session: Annotated[Session, Depends(reads_first)]) -> Reply:
        session.execute(text("SELECT 1"))
        return Reply(ok=True)

    app.add_api_route("/api/reads-first", route)

    async def at_once(path: str) -> list[int]:
        anyio.to_thread.current_default_thread_limiter().total_tokens = 4
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(
            transport=transport, base_url="http://t"
        ) as client:
            replies = await asyncio.gather(*(client.get(path) for _ in range(40)))
        return [reply.status_code for reply in replies]

    started = time.monotonic()
    try:
        for path in ("/api/healthz", "/api/reads-first"):
            assert asyncio.run(at_once(path)) == [200] * 40, path
    finally:
        app.state.db.dispose()
    assert time.monotonic() - started < db.POOL_TIMEOUT / 2


def test_no_place_for_a_session_in_time_is_a_503(database_url: str) -> None:
    """As many requests hold a session as may: the next waits, then is
    told to try again."""
    app = create_app(Settings(database_url=database_url))
    app.state.db.dispose()
    app.state.db = make_database(database_url, pool=(1, 0), timeout=0.1)

    async def full() -> int:
        # Held by another, as by a request under way.
        await app.state.db.sessions.acquire_on_behalf_of(object())
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(
            transport=transport, base_url="http://t"
        ) as client:
            return (await client.get("/api/healthz")).status_code

    try:
        assert asyncio.run(full()) == 503
    finally:
        app.state.db.dispose()


def test_a_request_holding_its_connection_still_counts_and_is_seen(
    database_url: str,
) -> None:
    """With the request pool's one connection held, rate limits and last
    seen use the side pool rather than wait for it: before, they waited
    until the pool timed out."""
    with small_database(database_url) as database, database.session() as request:
        request.execute(text("SELECT 1"))  # Holds the one connection.
        assert limiter(request).hit(RateLimitItemPerMinute(1), "test", "held")
        db.record_last_seen(database, Device, -1, datetime.now(UTC))


def test_connections_wait_for_a_lock_for_a_while_only(database: Database) -> None:
    for pool in database.pools.values():
        with pool.connect() as connection:
            assert connection.scalar(text("SHOW lock_timeout")) == LOCK_TIMEOUT


def test_last_seen_is_skipped_while_its_row_is_locked_too_long(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a lock waited for too long: any other failure is raised."""

    def refused(error: Exception) -> Callable[..., object]:
        def execute(*_: object, **__: object) -> object:
            raise exc.OperationalError("UPDATE", None, error)

        return execute

    monkeypatch.setattr(Session, "execute", refused(LockNotAvailable()))
    with capture_logs() as logs:
        db.record_last_seen(database, Device, -1, datetime.now(UTC))
    assert [line["event"] for line in logs] == ["last seen not recorded"]
    monkeypatch.setattr(Session, "execute", refused(ConnectionFailure()))
    with pytest.raises(exc.OperationalError):
        db.record_last_seen(database, Device, -1, datetime.now(UTC))


def test_work_after_the_reply_uses_the_side_pool(database: Database) -> None:
    """It holds no place among the requests (db.session_slot), so it must
    not take the request pool's connections from those that do."""
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=database)))
    with db.get_session_factory(cast(Request, request))() as session:
        assert session.get_bind() is database.side


def test_a_session_without_its_database_says_so(engine: Engine) -> None:
    with Session(engine) as session, pytest.raises(RuntimeError, match="Database"):
        db.database_of(session)


def test_work_after_the_reply_counts_on_a_pool_it_does_not_hold(
    database_url: str,
) -> None:
    """A reset email's lookup holds a side-pool connection while it counts
    the attempt: on the limits pool, which waits on nothing, so the side
    pool never waits on itself."""
    with (
        small_database(database_url) as database,
        database.session(side=True) as after_the_reply,
    ):
        after_the_reply.execute(text("SELECT 1"))  # Holds the side pool.
        assert limiter(after_the_reply).hit(RateLimitItemPerMinute(1), "test", "after")


@pytest.mark.breaks_pool_order
def test_a_busy_limits_pool_says_so(database_url: str) -> None:
    with (
        small_database(database_url) as database,
        database.limits.connect(),
        database.session() as request,
        pytest.raises(PoolTimeout) as busy,
    ):
        limiter(request).hit(RateLimitItemPerMinute(1), "test", "busy")
    assert busy.value.pool == "limits"


def test_the_pool_order_is_watched(database_url: str) -> None:
    """A take from a pool while the thread holds one from the same pool, or
    a later one, is reported (db.pool_order_broken; conftest fails a test
    on it): here, the side pool while holding the limits pool."""
    broken: list[tuple[str, list[str]]] = []
    with (
        patch.object(db, "pool_order_broken", lambda p, h: broken.append((p, h))),
        small_database(database_url, side=(1, 0)) as database,
        database.limits.connect(),
        database.side.connect(),
    ):
        pass
    assert broken == [("side", ["limits"])]


def test_a_break_in_the_pool_order_is_logged_as_an_error() -> None:
    """In production, where no test is watching (conftest replaces it)."""
    with capture_logs() as logs:
        logs_pool_order_broken("side", ["limits"])
    assert [(line["log_level"], line["pool"], line["held"]) for line in logs] == [
        ("error", "side", ["limits"])
    ]
