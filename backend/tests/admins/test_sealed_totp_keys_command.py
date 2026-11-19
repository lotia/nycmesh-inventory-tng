"""TOTP keys from the command line (admins/sealed_totp_keys_command.py;
DEPLOYMENT.md, TOTP keys)."""

from pathlib import Path

import pytest

from inventory_tng import manage
from inventory_tng.admins import sealed_totp_keys_command
from inventory_tng.admins.sealed_totp_keys import TotpKeys


def test_new_prints_a_key_that_loads(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """As DEPLOYMENT.md runs it: python -m inventory_tng.manage sealed-totp-keys new."""
    assert manage.main(["sealed-totp-keys", "new"]) == 0
    printed = capsys.readouterr().out.strip()
    (tmp_path / "1").write_text(printed)
    assert TotpKeys.load(tmp_path).newest == 1


def test_reseal_runs_as_an_operator_command(
    own_database: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Through kubectl exec, as DEPLOYMENT.md's rotation says; its own
    database, as it commits."""
    monkeypatch.setenv("DATABASE_URL", own_database)
    assert sealed_totp_keys_command.main(["reseal"]) == 0
    # After its log lines, which go to standard output too.
    assert (
        capsys.readouterr().out.splitlines()[-1] == "Resealed 0; 0 could not be read."
    )
