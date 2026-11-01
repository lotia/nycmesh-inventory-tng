import pytest
from fastapi.testclient import TestClient
from sqlalchemy import exc
from structlog.testing import capture_logs

from inventory_tng.app import create_app
from inventory_tng.settings import Settings


def test_healthz_answers_once_the_database_does(client: TestClient) -> None:
    response = client.get("/api/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_is_503_and_warns_when_the_database_is_unreachable() -> None:
    # Port 1 refuses at once; nothing is overridden, so this is the app's own
    # engine and session.
    app = create_app(Settings(database_url="postgresql+psycopg://u:p@127.0.0.1:1/x"))
    with TestClient(app) as client, capture_logs() as logs:
        response = client.get("/api/healthz")
    assert response.status_code == 503
    assert response.json() == {"reason": "database_unavailable"}
    (warning,) = [entry for entry in logs if entry["log_level"] == "warning"]
    assert warning["event"] == "database unavailable"
    assert warning["error"] == "OperationalError"


class Refusal(Exception):
    """A psycopg error, as far as db.install() looks at one."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


@pytest.mark.parametrize(
    ("sqlstate", "status"),
    [("40P01", 503), ("57014", 503), ("08006", 503), ("54000", 500)],
)
def test_only_errors_a_retry_can_cure_are_503s(
    database_url: str, sqlstate: str, status: int
) -> None:
    app = create_app(Settings(database_url=database_url))

    def fails() -> None:
        raise exc.OperationalError("SELECT 1", None, Refusal(sqlstate))

    app.add_api_route("/api/fails", fails)
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/api/fails").status_code == status
