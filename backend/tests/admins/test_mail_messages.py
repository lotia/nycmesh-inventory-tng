"""What each mail to administrators says (docs/decisions/0012)."""

import pytest

from inventory_tng.admins import mail_messages
from inventory_tng.models.site_settings import TOTP_POLICIES, SignInSettings


@pytest.mark.parametrize("change", [0, 1, 45])
def test_the_sign_in_settings_mail_counts_each_setting_in_its_own_plural(
    change: int,
) -> None:
    settings = SignInSettings(
        totp_policy="required",
        session_days=1,
        confirm_hours=2,
        confirm_idle_minutes=1,
        account_change_minutes=change,
    )
    body = mail_messages.sign_in_settings_changed(settings)("ada@example.org", 1).body
    assert f"{mail_messages.TOTP_POLICY_WORDS['required']};" in body
    assert f"{mail_messages.days(1)};" in body
    assert f"{mail_messages.hours(2)}, or {mail_messages.minutes(1)} unused" in body
    # A code each time, or within so many minutes.
    said = body.split("\n\n")[0]
    assert said.endswith(f"{mail_messages.minutes(change)}.") == bool(change)
    assert body.endswith(str(mail_messages.NOT_AGREED))


def test_every_totp_policy_has_words_for_the_settings_mail() -> None:
    assert set(mail_messages.TOTP_POLICY_WORDS) == set(TOTP_POLICIES)
