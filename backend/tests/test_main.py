from pathlib import Path

import pytest

from inventory_tng import __main__, local_env
from inventory_tng.settings import SettingsError


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, object]:
    """Stub uvicorn.run and point .env at a file that does not exist."""
    calls: dict[str, object] = {}
    monkeypatch.setenv("DATABASE_URL", "postgresql://db/x")

    def run(app: str, **kwargs: object) -> None:
        calls.update(app=app, **kwargs)

    monkeypatch.setattr(__main__.uvicorn, "run", run)
    monkeypatch.setattr(local_env, "ENV_FILE", tmp_path / ".env")
    return calls


@pytest.mark.usefixtures("restore_logging")
def test_uvicorn_leaves_logging_to_us(runs: dict[str, object]) -> None:
    __main__.main([])
    assert runs["app"] == "inventory_tng.server:create_server"
    assert runs["factory"] is True
    assert runs["log_config"] is None
    assert runs["access_log"] is False
    assert (runs["host"], runs["port"], runs["workers"]) == ("127.0.0.1", 8000, 1)
    assert runs["reload"] is False


@pytest.mark.usefixtures("restore_logging")
def test_reload_watches_only_the_package(runs: dict[str, object]) -> None:
    __main__.main(["--reload", "--port", "9000"])
    assert runs["reload"] is True
    assert runs["reload_dirs"] == [str(Path(__main__.__file__).parent)]
    assert runs["port"] == 9000


@pytest.mark.usefixtures("restore_logging")
def test_a_bad_setting_stops_it_before_uvicorn_starts(
    runs: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNMASK_SECRETS", "true")
    with pytest.raises(SettingsError, match="UNMASK_SECRETS"):
        __main__.main([])
    assert runs == {}
