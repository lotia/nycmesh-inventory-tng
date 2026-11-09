"""The rate limits: registration, setup, sign-in, codes and resets each keep
their counts here, by name, with the `limits` library's moving window over
a storage of our own in PostgreSQL (the rate_limit table), so every
instance of the app shares them (inventory-tng-8i4.124).

The storage implements the library's contract for the moving window
(limits.storage.Storage and MovingWindowSupport, limits 5.8): each
attempt is one statement, atomic across instances as INSERT ... ON
CONFLICT DO UPDATE locks the key's row, and timed by the database's clock.
It runs in a transaction of its own, so an attempt counts even when the
request that made it rolls back. The fixed and sliding window methods are
not implemented, as nothing here uses them. A row past its window is
deleted after each attempt is counted, in a transaction of its own, so the
table holds only keys tried within their window, and a count never holds
locks on other keys' rows that another count could be waiting on.

refund() is ours, not the library's: it takes back the newest attempt
counted against a key, for one that turned out not to be a guess.
"""

from collections.abc import Generator
from contextlib import contextmanager

import structlog
from limits import RateLimitItem
from limits.storage import MovingWindowSupport, Storage
from limits.strategies import MovingWindowRateLimiter
from sqlalchemy import Connection, Engine, delete, exc, func, select, text
from sqlalchemy.orm import Session

from inventory_tng.db import PoolTimeout, database_of
from inventory_tng.models import RateLimit

log = structlog.get_logger(__name__)

# A key's attempts still inside a window of :expiry seconds, as of the
# statement's now().
_INSIDE = (
    "array(SELECT hit FROM unnest(rate_limit.hits) hit"
    " WHERE hit > now() - make_interval(secs => :expiry) ORDER BY hit)"
)
_ACQUIRE = text(
    "INSERT INTO rate_limit (key, hits, expires_at)"
    " VALUES (:key, array_fill(now(), ARRAY[:amount]),"
    " now() + make_interval(secs => :expiry))"
    " ON CONFLICT (key) DO UPDATE"
    f" SET hits = {_INSIDE} || EXCLUDED.hits, expires_at = EXCLUDED.expires_at"
    f" WHERE cardinality({_INSIDE}) + :amount <= :limit"
    " RETURNING true"
)
_REFUND = text(
    "UPDATE rate_limit SET hits = hits[1:cardinality(hits) - 1]"
    " WHERE key = :key AND cardinality(hits) > 0"
)
_WINDOW = text(
    "SELECT extract(epoch FROM coalesce(min(hit), now())), count(hit)"
    " FROM rate_limit, unnest(hits) hit"
    " WHERE key = :key AND hit > now() - make_interval(secs => :expiry)"
)


class PostgresStorage(Storage, MovingWindowSupport):
    # Made directly (limiter), never from a URI: no scheme to register.
    STORAGE_SCHEME = None

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self.engine = engine

    @property
    def base_exceptions(self) -> type[Exception]:
        return exc.SQLAlchemyError

    @contextmanager
    def _connection(self) -> Generator[Connection]:
        """A connection from the limits pool; PoolTimeout if none comes in
        time, so the 503 says which pool ran out (db.install)."""
        try:
            connection = self.engine.connect()
        except exc.TimeoutError:
            raise PoolTimeout("limits") from None
        with connection:
            yield connection

    def acquire_entry(self, key: str, limit: int, expiry: int, amount: int = 1) -> bool:
        if amount > limit:
            return False
        params = {"key": key, "limit": limit, "expiry": expiry, "amount": amount}
        with self._connection() as connection:
            with connection.begin():
                acquired = connection.scalar(_ACQUIRE, params) is not None
            with connection.begin():
                gone = connection.execute(
                    delete(RateLimit).where(RateLimit.expires_at < func.now())
                ).rowcount
        log.debug("attempt counted", allowed=acquired, expired_removed=gone)
        return acquired

    def get_moving_window(self, key: str, limit: int, expiry: int) -> tuple[float, int]:
        with self._connection() as connection:
            start, count = connection.execute(
                _WINDOW, {"key": key, "expiry": expiry}
            ).one()
        return float(start), count

    def clear(self, key: str) -> None:
        with self._connection() as connection, connection.begin():
            connection.execute(delete(RateLimit).where(RateLimit.key == key))

    def reset(self) -> int:
        with self._connection() as connection, connection.begin():
            return connection.execute(delete(RateLimit)).rowcount

    def check(self) -> bool:
        try:
            with self._connection() as connection:
                connection.scalar(select(1))
        except exc.SQLAlchemyError:
            return False
        return True

    def refund(self, key: str) -> None:
        """Take back the newest attempt counted against `key`. Not part of
        the library's contract."""
        with self._connection() as connection, connection.begin():
            refunded = connection.execute(_REFUND, {"key": key}).rowcount
        log.debug("attempt refunded", refunded=refunded)

    # The fixed window's: not used here (MovingWindowRateLimiter).
    def incr(self, key: str, expiry: int, amount: int = 1) -> int:
        raise NotImplementedError

    def get(self, key: str) -> int:
        raise NotImplementedError

    def get_expiry(self, key: str) -> float:
        raise NotImplementedError


def refund(session: Session, item: RateLimitItem, *identifiers: str) -> None:
    """Take back an attempt counted (hit) that turned out not to be a guess,
    such as a registration with a good code: counted first, so attempts at
    once cannot all pass the limit before any is counted, and refunded, so
    a good one never uses it up (inventory-tng-8i4.126)."""
    _storage(session).refund(item.key_for(*identifiers))


def limiter(session: Session) -> MovingWindowRateLimiter:
    """The rate limits, on the database `session` uses, in transactions of
    their own, on its limits pool (db.Database.limits): counting never waits
    on a pool the caller's own connection came from."""
    return MovingWindowRateLimiter(_storage(session))


def _storage(session: Session) -> PostgresStorage:
    return PostgresStorage(database_of(session).limits)
