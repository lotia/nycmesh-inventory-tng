"""Measuring what the app does, for the measurements (`mise run
measure-catalogue`, `mise run measure-labels`): the plans of the
statements it sends, and how long a request takes."""

import statistics
import time
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, event

# Requests timed, after one to warm up.
TIMES = 20


def plans(
    engine: Engine, wanted: Callable[[str], bool], ask: Callable[[], object]
) -> list[str]:
    """The `wanted` statements that `ask` sends on `engine`, each with its
    plan as run: EXPLAIN (ANALYZE, BUFFERS), with the parameters it was
    sent."""
    sent: list[tuple[str, dict[str, Any]]] = []

    def keep(
        _connection: object,
        _cursor: object,
        statement: str,
        parameters: dict[str, Any],
        *_: object,
    ) -> None:
        if wanted(statement):
            sent.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", keep)
    try:
        ask()
    finally:
        event.remove(engine, "before_cursor_execute", keep)
    found = []
    with engine.connect() as connection:
        for statement, parameters in sent:
            rows = connection.exec_driver_sql(
                f"EXPLAIN (ANALYZE, BUFFERS) {statement}", parameters
            )
            found.append(f"{statement}\n" + "\n".join(row[0] for row in rows))
    return found


def timed(
    client: TestClient, path: str, params: dict[str, Any]
) -> tuple[list[float], Response]:
    """Milliseconds each of TIMES GETs of `path` took, after one to warm
    up, and the last reply."""
    assert client.get(path, params=params).status_code == 200
    took = []
    for _ in range(TIMES):
        start = time.perf_counter()
        reply = client.get(path, params=params)
        took.append((time.perf_counter() - start) * 1000)
        assert reply.status_code == 200
    return took, reply


def summary(took: list[float]) -> str:
    return f"median {statistics.median(took):.0f} ms, slowest {max(took):.0f} ms"
