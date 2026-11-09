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
"""

import structlog
from limits.storage import MovingWindowSupport, Storage
from limits.strategies import MovingWindowRateLimiter
from sqlalchemy import Engine, delete, exc, func, select, text
from sqlalchemy.orm import Session

from inventory_tng.db import side_engine
from inventory_tng.models import RateLimit

log = structlog.get_logger(__name__)

# A key's attempts still inside a window of :expiry seconds, as of the
# statement's now().
_INSIDE = (
    "array(SELECT hit FROM unnest(rate_limit.hits) hit"
    " WHERE hit > now() - make_interval(secs => :expiry))"
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

    def acquire_entry(self, key: str, limit: int, expiry: int, amount: int = 1) -> bool:
        if amount > limit:
            return False
        params = {"key": key, "limit": limit, "expiry": expiry, "amount": amount}
        with self.engine.connect() as connection:
            with connection.begin():
                acquired = connection.scalar(_ACQUIRE, params) is not None
            with connection.begin():
                gone = connection.execute(
                    delete(RateLimit).where(RateLimit.expires_at < func.now())
                ).rowcount
        log.debug("attempt counted", allowed=acquired, expired_removed=gone)
        return acquired

    def get_moving_window(self, key: str, limit: int, expiry: int) -> tuple[float, int]:
        with self.engine.connect() as connection:
            start, count = connection.execute(
                _WINDOW, {"key": key, "expiry": expiry}
            ).one()
        return float(start), count

    def clear(self, key: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(delete(RateLimit).where(RateLimit.key == key))

    def reset(self) -> int:
        with self.engine.begin() as connection:
            return connection.execute(delete(RateLimit)).rowcount

    def check(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.scalar(select(1))
        except exc.SQLAlchemyError:
            return False
        return True

    # The fixed window's: not used here (MovingWindowRateLimiter).
    def incr(self, key: str, expiry: int, amount: int = 1) -> int:
        raise NotImplementedError

    def get(self, key: str) -> int:
        raise NotImplementedError

    def get_expiry(self, key: str) -> float:
        raise NotImplementedError


def limiter(session: Session) -> MovingWindowRateLimiter:
    """The rate limits, on the database `session` uses, in transactions of
    their own, on its side pool (db.side_engine): counting never waits on
    the pool the request's own connection came from."""
    return MovingWindowRateLimiter(
        PostgresStorage(side_engine(session.get_bind().engine))
    )
