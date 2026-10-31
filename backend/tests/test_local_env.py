import os
from pathlib import Path

import pytest

from inventory_tng import local_env


def test_the_shell_wins_over_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = tmp_path / ".env"
    env.write_text("INVENTORY_TEST_SHELL=file\nINVENTORY_TEST_FILE=file\n")
    monkeypatch.setattr(local_env, "ENV_FILE", env)
    monkeypatch.setenv("INVENTORY_TEST_SHELL", "shell")
    try:
        local_env.load_local_env()
        assert os.environ["INVENTORY_TEST_SHELL"] == "shell"
        assert os.environ["INVENTORY_TEST_FILE"] == "file"
    finally:
        os.environ.pop("INVENTORY_TEST_FILE", None)


def test_values_are_read_without_touching_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = tmp_path / ".env"
    env.write_text("INVENTORY_TEST_ONLY_READ=1\nEMPTY=\n")
    monkeypatch.setattr(local_env, "ENV_FILE", env)
    assert local_env.local_env_values() == {"INVENTORY_TEST_ONLY_READ": "1"}
    assert "INVENTORY_TEST_ONLY_READ" not in os.environ


def test_no_file_is_fine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(local_env, "ENV_FILE", tmp_path / "missing")
    local_env.load_local_env()
    assert local_env.local_env_values() == {}


def test_references_take_the_shells_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "INVENTORY_DB_PORT=5433\n"
        "DATABASE_URL=postgresql://u:p@localhost:${INVENTORY_DB_PORT}/x\n"
    )
    monkeypatch.setattr(local_env, "ENV_FILE", env)
    monkeypatch.setenv("INVENTORY_DB_PORT", "5434")
    assert local_env.local_env_values()["DATABASE_URL"].endswith(":5434/x")
