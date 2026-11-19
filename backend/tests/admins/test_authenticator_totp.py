"""An authenticator's TOTP codes (admins/authenticator_totp.py)."""

from datetime import UTC, datetime, timedelta

from inventory_tng.admins import authenticator_totp

from helpers.sign_in import code

NOW = datetime(2026, 11, 2, 12, tzinfo=UTC)


def test_a_code_a_step_either_side_is_accepted() -> None:
    key = authenticator_totp.new_totp_key()
    for offset in (-30, 30):
        typed = code(key, NOW + timedelta(seconds=offset))
        assert authenticator_totp.accepted_step(key, typed, None, NOW) is not None
    stale = code(key, NOW - timedelta(seconds=90))
    if stale != code(key, NOW):
        assert authenticator_totp.accepted_step(key, stale, None, NOW) is None
