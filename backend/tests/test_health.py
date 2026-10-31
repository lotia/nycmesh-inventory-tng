from fastapi.testclient import TestClient
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
    assert response.json() == {"status": "database unreachable"}
    (warning,) = [entry for entry in logs if entry["log_level"] == "warning"]
    assert warning["event"] == "database unreachable"
    assert warning["error"] == "OperationalError"
