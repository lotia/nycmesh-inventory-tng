"""Authenticators' keys sealed at rest (totp_keys.py, docs/decisions/0017):
a copy of the database alone cannot make codes."""

import base64
import runpy
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng import accounts, sign_in, totp_keys
from inventory_tng.app import check_totp_keys
from inventory_tng.db import Database
from inventory_tng.models import Authenticator
from inventory_tng.settings import Settings
from inventory_tng.totp_keys import TotpKeys, TotpKeysError, Unreadable, keys_dir

from helpers.sign_in import TOTP_KEYS, code, new_admin

KEY = "JBSWY3DPEHPK3PXP"
URL = "postgresql://u:p@db/x"


def keys_in(directory: Path, **files: str) -> Path:
    for name, content in files.items():
        (directory / name).write_text(content)
    return directory


def test_a_sealed_key_opens_only_for_its_administrator() -> None:
    version, one = TOTP_KEYS.seal(KEY, 1)
    _, again = TOTP_KEYS.seal(KEY, 1)
    assert KEY.encode() not in one
    assert one != again  # A fresh nonce each time.
    assert TOTP_KEYS.open(version, one, 1) == KEY
    with pytest.raises(Unreadable):
        TOTP_KEYS.open(version, one, 2)


def test_a_key_version_that_is_gone_is_unreadable() -> None:
    version, one = TOTP_KEYS.seal(KEY, 1)
    newer = TotpKeys({version + 1: base64.b64decode(totp_keys.new_key())})
    with pytest.raises(Unreadable, match="not in TOTP_KEYS_DIR"):
        newer.open(version, one, 1)


def test_the_newest_version_seals_and_every_version_opens(tmp_path: Path) -> None:
    first = TotpKeys.load(keys_in(tmp_path, **{"1": totp_keys.new_key()}))
    version, one = first.seal(KEY, 1)
    # A mounted Secret's hidden entries, and anything not a version, are
    # passed over: a second name for a version too, so which key it is never
    # depends on the order the directory lists them in.
    not_versions = {"..data": "", "notes": "x", "02": "x", "\u00b2": "x", "0": "x"}
    both = TotpKeys.load(
        keys_in(tmp_path, **{"2": totp_keys.new_key()}, **not_versions)
    )
    assert both.newest == 2
    assert both.seal(KEY, 1)[0] == 2
    assert both.open(version, one, 1) == KEY


@pytest.mark.parametrize(
    "content", ["", "not base64!", base64.b64encode(b"short").decode()]
)
def test_a_file_that_is_not_a_key_is_refused_without_showing_it(
    tmp_path: Path, content: str
) -> None:
    with pytest.raises(TotpKeysError, match="version 1 is not a key") as refused:
        TotpKeys.load(keys_in(tmp_path, **{"1": content}))
    if content:
        assert content not in str(refused.value)


def test_a_version_the_database_cannot_hold_is_refused(tmp_path: Path) -> None:
    """authenticator.key_version is a smallint."""
    newest = str(totp_keys.MOST_VERSION + 1)
    with pytest.raises(TotpKeysError, match=f"version {newest} is past"):
        TotpKeys.load(keys_in(tmp_path, **{newest: totp_keys.new_key()}))


def test_a_missing_or_empty_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(TotpKeysError, match="cannot be read"):
        TotpKeys.load(tmp_path / "missing")
    with pytest.raises(TotpKeysError, match="holds no key"):
        TotpKeys.load(tmp_path)


def test_the_directory_is_needed_outside_development(tmp_path: Path) -> None:
    given = {"TOTP_KEYS_DIR": str(tmp_path)}
    assert keys_dir(given, development=False) == tmp_path
    assert keys_dir({}, development=True) == totp_keys.DEVELOPMENT_DIR
    with pytest.raises(TotpKeysError, match="needed outside APP_ENV=development"):
        keys_dir({}, development=False)


def test_new_prints_a_key_that_loads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """As DEPLOYMENT.md runs it: python -m inventory_tng.totp_keys new."""
    monkeypatch.setattr(sys, "argv", ["totp_keys", "new"])
    with (
        pytest.warns(RuntimeWarning, match="found in sys.modules"),
        pytest.raises(SystemExit) as done,
    ):
        runpy.run_module("inventory_tng.totp_keys", run_name="__main__")
    assert done.value.code == 0
    printed = capsys.readouterr().out.strip()
    assert TotpKeys.load(keys_in(tmp_path, **{"1": printed})).newest == 1


def test_the_database_holds_no_key_it_can_make_codes_with(session: Session) -> None:
    _, key = new_admin(session)
    assert key is not None
    row = session.execute(text("SELECT * FROM authenticator")).mappings().one()
    assert "totp_key" not in row
    assert key.encode() not in row["sealed_key"]


def test_an_unreadable_authenticator_still_lets_a_recovery_code_in(
    session: Session,
) -> None:
    """Its key version gone from TOTP_KEYS_DIR: it takes no code, and says so,
    but its administrator still gets in with a recovery code."""
    admin, key = new_admin(session)
    (recovery,) = accounts.make_recovery_codes(session, admin.id, datetime.now(UTC))[:1]
    lost = TotpKeys({9: base64.b64decode(totp_keys.new_key())})
    authenticators = accounts.locked_authenticators(session, admin.id)
    now = datetime.now(UTC)
    with capture_logs() as logs:
        assert (
            accounts.check_second_factor(
                session, lost, admin.id, authenticators, code(key), now
            )
            is None
        )
    assert {
        "event": "authenticator unreadable",
        "admin_id": admin.id,
        "authenticator_id": authenticators[0].id,
        "key_version": 1,
        "log_level": "error",
    } in logs
    assert (
        accounts.check_second_factor(
            session, lost, admin.id, authenticators, recovery, now
        )
        == "recovery"
    )
    assert accounts.unreadable_authenticators(session, lost) == 1
    assert accounts.unreadable_authenticators(session, TOTP_KEYS) == 0


def test_resealing_moves_every_readable_key_to_the_newest_version(
    session: Session,
) -> None:
    ada, ada_key = new_admin(session)
    ben, _ = new_admin(session, "ben")
    newer = TotpKeys(dict(TOTP_KEYS.keys) | {2: base64.b64decode(totp_keys.new_key())})
    # Sealed with a version this ring lacks: left as it is, and counted.
    lost = session.scalars(
        select(Authenticator).where(Authenticator.admin_id == ben.id)
    ).one()
    lost.key_version = 7
    session.flush()
    assert accounts.reseal_authenticators(session, newer) == (1, 1)
    resealed = session.scalars(
        select(Authenticator).where(Authenticator.admin_id == ada.id)
    ).one()
    assert resealed.key_version == 2
    only_newest = TotpKeys({2: newer.keys[2]})
    assert ada_key is not None
    assert resealed.sealed_key is not None
    assert only_newest.open(2, resealed.sealed_key, ada.id) == ada_key
    assert lost.key_version == 7
    # Again, nothing is left to move.
    assert accounts.reseal_authenticators(session, newer) == (0, 1)


def test_startup_says_how_many_authenticators_are_unreadable(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    with capture_logs() as logs:
        check_totp_keys(database, TOTP_KEYS)
    assert logs == []
    monkeypatch.setattr(accounts, "unreadable_authenticators", lambda *_: 2)
    with capture_logs() as logs:
        check_totp_keys(database, TOTP_KEYS)
    assert logs == [
        {
            "event": "authenticators unreadable: their key versions are not in "
            "TOTP_KEYS_DIR",
            "count": 2,
            "log_level": "error",
        }
    ]


@pytest.mark.usefixtures("restore_logging")
def test_reseal_runs_as_an_operator_command(
    own_database: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Through kubectl exec, as DEPLOYMENT.md's rotation says; its own
    database, as it commits."""
    monkeypatch.setenv("DATABASE_URL", own_database)
    assert totp_keys.main(["reseal"]) == 0
    # After its log lines, which go to standard output too.
    assert (
        capsys.readouterr().out.splitlines()[-1] == "Resealed 0; 0 could not be read."
    )


def test_an_app_without_keys_refuses_rather_than_storing_a_key_plainly() -> None:
    """Only Settings built in code can lack them."""
    app = SimpleNamespace(state=SimpleNamespace(settings=Settings(URL)))
    with pytest.raises(RuntimeError, match="no totp_keys"):
        sign_in.totp_keys(cast("Request", SimpleNamespace(app=app)))
