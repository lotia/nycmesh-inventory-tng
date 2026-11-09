"""Small databases (db.Database), for tests of what happens when a pool
runs out: a few connections, and short waits."""

from collections.abc import Generator
from contextlib import contextmanager

from inventory_tng.db import Database, make_database


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
