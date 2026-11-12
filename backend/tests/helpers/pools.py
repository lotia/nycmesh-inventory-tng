"""Small databases (db.Database), for tests of what happens when a pool
runs out: a few connections, and short waits; and every pool's connections
checked out, for conftest's keeps_its_connections."""

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.pool import ConnectionPoolEntry, Pool

from inventory_tng.db import Database, make_database

# Every pool's connections checked out and not yet given back, kept by the
# events below, which count without a query.
checked_out: set[ConnectionPoolEntry] = set()
event.listen(Pool, "checkout", lambda _, entry, __: checked_out.add(entry))
event.listen(Pool, "checkin", lambda _, entry: checked_out.discard(entry))
event.listen(Pool, "detach", lambda _, entry: checked_out.discard(entry))


@contextmanager
def small_database(
    url: str,
    *,
    pool: tuple[int, int] = (1, 0),
    side: tuple[int, int] = (1, 0),
    limits: tuple[int, int] = (1, 0),
    timeout: float = 0.1,
) -> Generator[Database]:
    """A Database of one connection per pool unless told otherwise, each
    waited for 0.1 seconds at most, closed after."""
    database = make_database(url, pool=pool, side=side, limits=limits, timeout=timeout)
    try:
        yield database
    finally:
        database.dispose()
