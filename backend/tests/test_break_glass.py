"""The operator's break-glass (accounts.lock_down, let_in, invite_by_operator,
retire_authenticators; python -m inventory_tng.admins and totp_keys):
docs/decisions/0018."""

import asyncio
import runpy
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import accounts, activity, admins, devices, mail, sign_in, totp_keys
from inventory_tng.mail import Notifier
from inventory_tng.models import Admin, Authenticator, RegistrationCode, ResetLink

from helpers.mail import NO_MAIL, SentMail
from helpers.sign_in import (
    TOTP_KEYS,
    code,
    codes_with_left,
    new_admin,
    policy,
    sign_in_with,
)

NOW = datetime.now(UTC)
NEW_PASSWORD = "a long passphrase of their own"


def told(sent_mail: SentMail) -> tuple[Notifier, Callable[[], None]]:
    """A notifier as a command's, and what sends its mail once committed."""
    tasks = BackgroundTasks()
    return Notifier(sent_mail, tasks), lambda: asyncio.run(tasks())


def test_lockdown_retires_everyone_and_revokes_every_code(
    session: Session, sent_mail: SentMail
) -> None:
    ada, _ = new_admin(session, email="ada@example.org")
    ben, _ = new_admin(session, "ben")
    accounts.make_recovery_codes(session, ada.id, NOW)
    code_row, _ = devices.create_code(
        session, max_uses=1, lifetime=timedelta(days=1), label=None
    )
    notify, send = told(sent_mail)
    retired = accounts.lock_down(session, 2, NOW, notify)
    send()
    assert {a.id for a in retired} == {ada.id, ben.id}
    for admin in (ada, ben):
        assert admin.retired_at is not None
        assert not accounts.has_password(session, admin.id)
        assert not accounts.uses_totp(session, admin.id)
    assert accounts.recovery_codes_left(session, ada.id) == 0
    assert code_row.revoked_at is not None
    current_codes = select(RegistrationCode).where(
        RegistrationCode.revoked_at.is_(None)
    )
    assert session.scalars(current_codes).all() == []
    # Told by mail, as nobody is left to see it in the app.
    assert [(m.to, m.kind) for m in sent_mail] == [("ada@example.org", "activity")]
    assert activity.recent(session)[0].words == (
        "The operator retired every administrator and revoked every registration code"
    )


def test_lockdown_refuses_a_count_that_is_wrong(session: Session) -> None:
    ada, _ = new_admin(session)
    with pytest.raises(accounts.OperatorRefused, match="1 administrators"):
        accounts.lock_down(session, 2, NOW, NO_MAIL)
    assert ada.retired_at is None


def test_let_in_brings_one_back_with_a_link_that_resets_everything(
    session: Session, sent_mail: SentMail, make_client: Callable[..., TestClient]
) -> None:
    ada, key = new_admin(session, email="ada@example.org")
    new_admin(session, "ben", email="ben@example.org")
    accounts.lock_down(session, 2, NOW, NO_MAIL)
    notify, send = told(sent_mail)
    admin, token = accounts.let_in(session, "ADA", NOW, notify)
    send()
    assert admin.id == ada.id
    assert ada.retired_at is None
    link = session.scalars(select(ResetLink).where(ResetLink.admin_id == ada.id)).one()
    # Locked out, they have no password left: invited to choose one.
    assert (link.kind, link.made_by_id) == ("invite", None)
    assert link.expires_at - NOW == timedelta(hours=1)
    # Ben is retired, so not told; Ada is, in her own words.
    assert [(m.to, m.kind) for m in sent_mail] == [("ada@example.org", "let_in")]
    form = {"token": token, "password": NEW_PASSWORD, "password_again": NEW_PASSWORD}
    client = make_client()
    assert client.post(sign_in.RESET, data=form).status_code == 303
    # The old authenticator is gone: in with the password, then set one up.
    assert sign_in_with(client, "ada", code=code(key)).status_code == 400
    assert sign_in_with(client, "ada", NEW_PASSWORD).status_code == 303
    assert client.get(sign_in.HOME).headers["location"] == sign_in.NEW_AUTHENTICATOR


def test_let_in_works_without_a_lockdown(session: Session) -> None:
    ada, _ = new_admin(session)
    accounts.let_in(session, "ada", NOW, NO_MAIL)
    assert not accounts.has_password(session, ada.id)
    assert activity.recent(session)[0].words == "The operator let ada back in"


def test_let_in_refuses_nobody(session: Session) -> None:
    with pytest.raises(accounts.OperatorRefused, match="No administrator"):
        accounts.let_in(session, "nobody", NOW, NO_MAIL)


def test_the_operator_invites_an_administrator(
    session: Session, sent_mail: SentMail
) -> None:
    new_admin(session, email="ada@example.org")
    notify, send = told(sent_mail)
    grace, _ = accounts.invite_by_operator(
        session, "grace", "grace@example.org", NOW, notify
    )
    send()
    assert not accounts.has_password(session, grace.id)
    assert sorted((m.to, m.kind) for m in sent_mail) == [
        ("ada@example.org", "activity"),
        ("grace@example.org", "invited_by_operator"),
    ]
    with pytest.raises(accounts.OperatorRefused, match="has that"):
        accounts.invite_by_operator(session, "grace", None, NOW, NO_MAIL)
    with pytest.raises(accounts.OperatorRefused):
        accounts.invite_by_operator(session, "not a username", None, NOW, NO_MAIL)


def unreadable(session: Session, admin_id: int) -> Authenticator:
    """Sealed with a key version TOTP_KEYS lacks, as after a key is lost."""
    authenticator = session.scalars(
        select(Authenticator).where(Authenticator.admin_id == admin_id)
    ).one()
    authenticator.key_version = 9
    session.flush()
    return authenticator


def test_retiring_unreadable_authenticators_keeps_the_rest(session: Session) -> None:
    ada, _ = new_admin(session)
    ben, _ = new_admin(session, "ben")
    accounts.make_recovery_codes(session, ben.id, NOW)
    lost = unreadable(session, ada.id)
    retired, stranded = accounts.retire_authenticators(
        session, TOTP_KEYS, NOW, every=False, notify=NO_MAIL
    )
    assert (retired, stranded) == (1, ["ada"])
    assert lost.retired_at is not None
    # With no recovery code either, the password alone must not get in.
    assert not accounts.has_password(session, ada.id)
    assert accounts.uses_totp(session, ben.id)
    retired, stranded = accounts.retire_authenticators(
        session, TOTP_KEYS, NOW, every=True, notify=NO_MAIL
    )
    # Ben still has recovery codes, so can sign in with one.
    assert (retired, stranded) == (1, [])


def test_with_authenticators_retired_a_recovery_code_is_still_asked_for(
    session: Session, client: TestClient
) -> None:
    """Not the password alone: its holder could then set up their own."""
    ada, _ = new_admin(session)
    first = accounts.make_recovery_codes(session, ada.id, NOW)[0]
    accounts.retire_authenticators(session, TOTP_KEYS, NOW, every=True, notify=NO_MAIL)
    assert sign_in_with(client).status_code == 400
    # In, and asked to set up an authenticator there and then, even with
    # TOTP optional: else a code each sign-in until none were left.
    policy(session, "optional")
    assert "Set up an authenticator" in sign_in_with(client, code=first).text


@pytest.mark.usefixtures("restore_logging")
def test_the_commands_run_as_operator_commands(
    own_database: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Through kubectl exec (DEPLOYMENT.md, Break-glass); its own database,
    as they commit."""
    monkeypatch.setenv("DATABASE_URL", own_database)
    monkeypatch.delenv("MAIL_HOST", raising=False)
    site = "https://inventory.example.org"
    assert admins.main(["invite", "grace", "--base-url", site]) == 0
    assert (
        capsys.readouterr()
        .out.splitlines()[-1]
        .startswith(f"{site}{sign_in.RESET}?token=")
    )
    assert admins.main(["invite", "grace", "--base-url", site]) == 1
    assert admins.main(["let-in", "grace"]) == 1
    assert "Give --base-url" in capsys.readouterr().err
    assert admins.main(["lockdown", "--expect", "2"]) == 1
    assert admins.main(["lockdown", "--expect", "1"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "Retired 1 administrators."
    assert admins.main(["let-in", "grace", "--base-url", site]) == 0
    assert totp_keys.main(["retire-authenticators", "--all"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "Retired 0 authenticators."


def test_the_operator_invites_without_mail(session: Session) -> None:
    grace, _ = accounts.invite_by_operator(session, "grace", None, NOW, NO_MAIL)
    assert activity.recent(session)[0].words == "The operator invited grace"
    assert grace.retired_at is None


def test_admins_runs_as_a_module(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["admins", "--help"])
    with (
        pytest.warns(RuntimeWarning, match="found in sys.modules"),
        pytest.raises(SystemExit) as done,
    ):
        runpy.run_module("inventory_tng.admins", run_name="__main__")
    assert done.value.code == 0
    assert "lockdown" in capsys.readouterr().out


@pytest.mark.usefixtures("restore_logging")
def test_retiring_authenticators_names_who_must_be_let_in(
    own_database: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", own_database)
    monkeypatch.setattr(
        accounts, "retire_authenticators", lambda *_, **__: (2, ["ada", "ben"])
    )
    assert totp_keys.main(["retire-authenticators", "--unreadable"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == (
        "No second factor left, so let them in: ada, ben."
    )


def test_retiring_authenticators_asks_each_to_set_one_up_again(
    session: Session, sent_mail: SentMail, make_client: Callable[..., TestClient]
) -> None:
    """Signed out, told how, and asked to set one up again whatever the
    policy: not let in by the password alone once the last code is used."""
    ada, _ = new_admin(session, email="ada@example.org")
    ben, _ = new_admin(session, "ben", email="ben@example.org")
    left = codes_with_left(session, ada.id, 1)
    accounts.start_session(session, ada.id, None, NOW)
    notify, send = told(sent_mail)
    accounts.retire_authenticators(session, TOTP_KEYS, NOW, every=True, notify=notify)
    send()
    assert ada.must_set_up_totp
    assert accounts.current_sessions(session, ada.id, NOW) == []
    assert {(m.to, m.kind) for m in sent_mail} == {
        ("ada@example.org", "authenticators_retired"),
        ("ben@example.org", "left_without_a_factor"),
    }
    assert not accounts.has_password(session, ben.id)
    policy(session, "optional")
    client = make_client()
    assert sign_in_with(client, code=left[0]).status_code == 303
    assert client.get(sign_in.HOME).headers["location"] == sign_in.NEW_AUTHENTICATOR
    # The last code used, not set up: the password alone does not get in.
    assert sign_in_with(make_client(address="192.0.2.95")).status_code == 400


def test_setting_up_again_keeps_the_printed_codes(
    session: Session, make_client: Callable[..., TestClient]
) -> None:
    ada, _ = new_admin(session)
    left = codes_with_left(session, ada.id, 3)
    accounts.retire_authenticators(session, TOTP_KEYS, NOW, every=True, notify=NO_MAIL)
    client = make_client()
    sign_in_with(client, code=left[0])
    new_key = accounts.new_totp_key()
    form = {"authenticator_name": "Laptop", "key": new_key, "code": code(new_key)}
    assert client.post(sign_in.NEW_AUTHENTICATOR, data=form).status_code == 303
    assert not ada.must_set_up_totp
    assert accounts.recovery_codes_left(session, ada.id) == 2


def test_let_in_clears_the_ask_to_set_up_again(session: Session) -> None:
    ada, _ = new_admin(session)
    codes_with_left(session, ada.id, 2)
    accounts.retire_authenticators(session, TOTP_KEYS, NOW, every=True, notify=NO_MAIL)
    assert ada.must_set_up_totp
    accounts.let_in(session, "ada", NOW, NO_MAIL)
    assert not ada.must_set_up_totp


def test_one_with_a_readable_authenticator_left_is_not_asked(session: Session) -> None:
    ada, _ = new_admin(session)
    accounts.add_authenticator(
        session, TOTP_KEYS, ada.id, "Spare", accounts.new_totp_key(), 0
    )
    lost = session.scalars(
        select(Authenticator).where(Authenticator.name == "Phone")
    ).one()
    lost.key_version = 9
    session.flush()
    accounts.retire_authenticators(session, TOTP_KEYS, NOW, every=False, notify=NO_MAIL)
    assert accounts.uses_totp(session, ada.id)
    assert not ada.must_set_up_totp


ADA, BEN, GRACE = "ada@example.org", "ben@example.org", "g@example.org"


@pytest.mark.parametrize(
    ("act", "expected"),
    [
        (
            lambda s, ada, ben, n: accounts.invite(s, "grace", GRACE, ada.id, NOW, n),
            [(ADA, "activity"), (BEN, "activity"), (GRACE, "invited")],
        ),
        (
            lambda s, ada, ben, n: accounts.reset_admin_sign_in(
                s, ben.id, ada.id, NOW, n
            ),
            [(ADA, "activity"), (BEN, "reset_link_made")],
        ),
        (
            # Retired, Ben is no longer current: told once, as the one.
            lambda s, ada, ben, n: accounts.retire_admin(s, ben.id, ada.id, NOW, n),
            [(ADA, "activity"), (BEN, "retired")],
        ),
        (
            lambda s, ada, ben, n: accounts.let_in(s, "ben", NOW, n),
            [(ADA, "activity"), (BEN, "let_in")],
        ),
        (
            lambda s, ada, ben, n: accounts.invite_by_operator(
                s, "grace", GRACE, NOW, n
            ),
            [(ADA, "activity"), (BEN, "activity"), (GRACE, "invited_by_operator")],
        ),
    ],
)
def test_each_account_change_tells_the_one_it_was_done_to_and_everyone(
    session: Session,
    sent_mail: SentMail,
    act: Callable[[Session, Admin, Admin, Notifier], object],
    expected: list[tuple[str, str]],
) -> None:
    """From one place (activity.record), each told once: the one it was done
    to in their own words, every other administrator with an address in
    the feed's."""
    ada, _ = new_admin(session, email=ADA)
    ben, _ = new_admin(session, "ben", email=BEN)
    notify, send = told(sent_mail)
    act(session, ada, ben, notify)
    send()
    assert sorted((m.to, m.kind) for m in sent_mail) == expected


def test_bringing_back_and_a_recovery_link_tell_them_too(
    session: Session, sent_mail: SentMail
) -> None:
    ada, _ = new_admin(session, email="ada@example.org")
    ben, _ = new_admin(session, "ben", email="ben@example.org")
    accounts.retire_admin(session, ben.id, ada.id, NOW, NO_MAIL)
    notify, send = told(sent_mail)
    accounts.restore_admin(session, ben.id, ada.id, NOW, notify)
    token = "a recovery token the operator set, long"
    accounts.recover(session, token, token, "ada", "192.0.2.90", NOW, notify)
    send()
    assert sorted((m.to, m.kind) for m in sent_mail) == [
        ("ada@example.org", "activity"),
        ("ada@example.org", "recovery_link_made"),
        ("ben@example.org", "activity"),
        ("ben@example.org", "restored"),
    ]


def test_a_mail_for_nobody_is_refused(session: Session) -> None:
    with pytest.raises(ValueError, match="needs them"):
        activity.record(session, "locked_down", None, None, NO_MAIL, them=mail.let_in)
