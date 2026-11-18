"""An authenticator's TOTP codes (admins/totp_codes.py)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from inventory_tng.admins import totp_codes

from helpers.sign_in import code, new_admin

NOW = datetime(2026, 11, 2, 12, tzinfo=UTC)


def test_a_code_a_step_either_side_is_accepted(session: Session) -> None:
    _, key = new_admin(session)
    assert key is not None
    for offset in (-30, 30):
        typed = code(key, NOW + timedelta(seconds=offset))
        assert totp_codes.accepted_step(key, typed, None, NOW) is not None
    stale = code(key, NOW - timedelta(seconds=90))
    if stale != code(key, NOW):
        assert totp_codes.accepted_step(key, stale, None, NOW) is None
