"""The rate limits in PostgreSQL (rate_limits.py), through the `limits`
library's own moving window, as the app uses them: shared by every
instance, atomic under attempts at once, counted whatever the request does
after, and forgotten once their window passes (inventory-tng-8i4.124)."""

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from limits import RateLimitItemPerMinute
from sqlalchemy import Engine, exc, func, select
from sqlalchemy.orm import Session

from inventory_tng.db import make_engine
from inventory_tng.models import RateLimit
from inventory_tng.rate_limits import PostgresStorage, limiter, refund

TEN = RateLimitItemPerMinute(10)


@pytest.fixture
def app_session(engine: Engine) -> Iterator[Session]:
    """A session of the app's, as a request has, on the run's database."""
    with Session(engine) as session:
        yield session


@pytest.fixture
def other_instance(database_url: str) -> Iterator[Engine]:
    """Another instance of the app on the same database: an engine, and so
    a pool, of its own."""
    engine = make_engine(database_url)
    yield engine
    engine.dispose()


def test_every_instance_shares_the_counts(
    app_session: Session, other_instance: Engine
) -> None:
    for _ in range(10):
        assert limiter(app_session).hit(TEN, "sign_in", "192.0.2.1")
    with Session(other_instance) as there:
        assert not limiter(there).test(TEN, "sign_in", "192.0.2.1")
        assert not limiter(there).hit(TEN, "sign_in", "192.0.2.1")
        # Another key, or another limit's name, has counts of its own.
        assert limiter(there).hit(TEN, "sign_in", "192.0.2.2")
        assert limiter(there).hit(TEN, "forgot", "192.0.2.1")


def test_attempts_at_once_allow_exactly_the_limit(
    engine: Engine, other_instance: Engine
) -> None:
    def attempt(number: int) -> bool:
        with Session(engine if number % 2 else other_instance) as session:
            return limiter(session).hit(TEN, "code", "1")

    with ThreadPoolExecutor(max_workers=10) as pool:
        allowed = list(pool.map(attempt, range(25)))
    assert allowed.count(True) == 10


def test_an_attempt_counts_though_its_request_rolls_back(session: Session) -> None:
    """The test's session is rolled back after it; the count is not."""
    for _ in range(10):
        limiter(session).hit(TEN, "code", "2")
    session.rollback()
    assert not limiter(session).test(TEN, "code", "2")


def test_attempts_leave_the_window_and_their_rows_go(app_session: Session) -> None:
    now = app_session.scalar(select(func.now()))
    assert now is not None
    then = now - timedelta(seconds=61)
    busy, gone = TEN.key_for("reset", "192.0.2.3"), TEN.key_for("reset", "192.0.2.4")
    # Ten attempts, nine of them a minute ago; and a key whose window passed.
    app_session.add_all(
        [
            RateLimit(key=busy, hits=[then] * 9 + [now], expires_at=now),
            RateLimit(key=gone, hits=[then], expires_at=then),
        ]
    )
    app_session.commit()
    # Nine have left its window, so nine more may come, and no more.
    for _ in range(9):
        assert limiter(app_session).hit(TEN, "reset", "192.0.2.3")
    assert not limiter(app_session).hit(TEN, "reset", "192.0.2.3")
    # Counting deleted the row whose window had passed.
    assert app_session.get(RateLimit, gone) is None


def test_the_window_says_how_many_and_since_when(
    engine: Engine, app_session: Session
) -> None:
    storage = PostgresStorage(engine)
    session = app_session
    start, count = storage.get_moving_window("nothing yet", 10, 60)
    assert count == 0
    assert start > 0
    limiter(session).hit(TEN, "forgot", "192.0.2.5", cost=3)
    stats = limiter(session).get_window_stats(TEN, "forgot", "192.0.2.5")
    assert stats.remaining == 7
    # More than the limit at once is never allowed.
    assert not limiter(session).hit(TEN, "forgot", "192.0.2.6", cost=11)


def test_clearing_and_resetting(engine: Engine, app_session: Session) -> None:
    storage = PostgresStorage(engine)
    session = app_session
    for _ in range(10):
        limiter(session).hit(TEN, "setup", "192.0.2.7")
    limiter(session).clear(TEN, "setup", "192.0.2.7")
    assert limiter(session).test(TEN, "setup", "192.0.2.7")
    limiter(session).hit(TEN, "setup", "192.0.2.7")
    assert storage.reset() >= 1
    assert session.scalars(select(RateLimit)).all() == []
    assert storage.check()


def test_a_database_out_of_reach_fails_the_check() -> None:
    gone = make_engine("postgresql://nobody@127.0.0.1:1/none")
    assert not PostgresStorage(gone).check()
    assert PostgresStorage(gone).base_exceptions is exc.SQLAlchemyError
    gone.dispose()


@pytest.mark.parametrize(
    "use",
    [
        lambda storage: storage.incr("key", 60),
        lambda storage: storage.get("key"),
        lambda storage: storage.get_expiry("key"),
    ],
    ids=["incr", "get", "get_expiry"],
)
def test_the_fixed_window_is_not_offered(
    engine: Engine, use: Callable[[PostgresStorage], object]
) -> None:
    with pytest.raises(NotImplementedError):
        use(PostgresStorage(engine))


def test_a_refund_takes_back_one_attempt(app_session: Session) -> None:
    for _ in range(3):
        limiter(app_session).hit(TEN, "register", "192.0.2.9")
    refund(app_session, TEN, "register", "192.0.2.9")
    stats = limiter(app_session).get_window_stats(TEN, "register", "192.0.2.9")
    assert stats.remaining == 8
    # A key never counted has nothing to take back.
    refund(app_session, TEN, "register", "192.0.2.10")
    assert limiter(app_session).test(TEN, "register", "192.0.2.10")
