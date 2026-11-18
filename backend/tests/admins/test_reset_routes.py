"""Password reset links: asked for by mail, or made by another
administrator, and used on the reset page (docs/decisions/0012)."""

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.admins import accounts, paths
from inventory_tng.admins.reset_routes import ASKED, RESET_NOTICE
from inventory_tng.models.admins import Admin, AdminSession, ResetLink

from helpers.mail import NO_MAIL, SITE_MAIL, SentMail
from helpers.markup import text
from helpers.sign_in import (
    TOTP_KEYS,
    new_admin,
    policy,
    reset_link,
    sign_in_with,
    token_of,
)

NEW_PASSWORD = "a brand new long passphrase"


@pytest.fixture
def client(make_client: Callable[..., TestClient], sent_mail: SentMail) -> TestClient:
    return make_client(mailer=sent_mail)


def with_email(session: Session) -> tuple[Admin, str | None]:
    return new_admin(session, email="ada@example.org")


def ask(client: TestClient, username: str = "ada", **headers: str) -> str:
    response = client.post(paths.FORGOT, data={"username": username}, headers=headers)
    assert response.status_code == 200
    return text(response)


def the_link(sent: SentMail) -> str:
    (message,) = sent
    match = re.search(r"https://\S+", message.body)
    assert match is not None
    return match.group(0)


# Emailed resets


def test_an_emailed_link_resets_the_password(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    admin, key = with_email(session)
    sign_in_with(client, key=key)
    signed_in = session.query(AdminSession).one()
    assert str(ASKED) in ask(client)
    link = the_link(sent_mail)
    assert link.startswith(f"{SITE_MAIL.site_url}{paths.RESET}?token=")
    assert sent_mail[0].to == "ada@example.org"

    form = client.get(link.removeprefix(SITE_MAIL.site_url))
    assert form.status_code == 200
    done = client.post(
        paths.RESET,
        data={
            "token": token_of(link),
            "password": NEW_PASSWORD,
            "password_again": NEW_PASSWORD,
        },
    )
    assert done.headers["location"] == f"{paths.SIGN_IN}?notice=reset"
    assert str(RESET_NOTICE) in text(client.get(done.headers["location"]))
    assert signed_in.ended_at is not None
    assert accounts.check_password(session, admin.id, NEW_PASSWORD)
    # The owner is told.
    assert [m.kind for m in sent_mail] == ["reset_link", "password_changed"]
    # TOTP is still asked for.
    client.cookies.clear()
    assert sign_in_with(client, password=NEW_PASSWORD).status_code == 400
    again = sign_in_with(client, password=NEW_PASSWORD, key=key, later=True)
    assert again.status_code == 303


def test_the_answer_is_the_same_for_anyone(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    new_admin(session, "ben")  # No email.
    answers = {ask(client, "ben"), ask(client, "nobody")}
    with_email(session)
    answers.add(ask(client, "ada"))
    assert len(answers) == 1
    assert [m.to for m in sent_mail] == ["ada@example.org"]


def test_a_link_works_once(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    with_email(session)
    ask(client)
    token = token_of(the_link(sent_mail))
    form = {"token": token, "password": NEW_PASSWORD, "password_again": NEW_PASSWORD}
    assert client.post(paths.RESET, data=form).status_code == 303
    assert client.post(paths.RESET, data=form).status_code == 404
    assert "doesn't work any more" in text(client.get(f"{paths.RESET}?token={token}"))


def test_a_newer_link_voids_the_older(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    with_email(session)
    ask(client)
    ask(client)
    older, newer = (token_of(re.findall(r"https://\S+", m.body)[0]) for m in sent_mail)
    assert client.get(f"{paths.RESET}?token={older}").status_code == 404
    assert client.get(f"{paths.RESET}?token={newer}").status_code == 200


def test_an_expired_link_does_not_work(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    with_email(session)
    ask(client)
    token = token_of(the_link(sent_mail))
    link = session.query(ResetLink).one()
    assert link.expires_at - link.created_at <= timedelta(hours=1, seconds=5)
    link.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.flush()
    assert client.get(f"{paths.RESET}?token={token}").status_code == 404


def test_the_link_never_comes_from_the_requests_host(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    """Whoever sends the request chooses its Host: a link built from it
    could send the token to their site."""
    with_email(session)
    ask(client, host="evil.example")
    assert the_link(sent_mail).startswith(SITE_MAIL.site_url)


def test_a_short_password_is_refused_and_the_link_kept(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    with_email(session)
    ask(client)
    token = token_of(the_link(sent_mail))
    short = {"token": token, "password": "short", "password_again": "short"}
    assert client.post(paths.RESET, data=short).status_code == 400
    assert client.get(f"{paths.RESET}?token={token}").status_code == 200


def test_asking_is_rate_limited(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    with_email(session)
    for _ in range(10):
        ask(client, "nobody")
    limited = client.post(paths.FORGOT, data={"username": "ada"})
    assert limited.status_code == 429
    assert sent_mail == []


def test_without_mail_there_is_no_emailed_reset(
    make_app: Callable[..., FastAPI], session: Session
) -> None:
    with TestClient(make_app(), base_url="https://testserver") as client:
        assert "Forgot your password?" not in text(client.get(paths.SIGN_IN))
        assert "isn't set up here" in text(client.get(paths.FORGOT))
        refused = client.post(paths.FORGOT, data={"username": "ada"})
        assert refused.status_code == 404


def test_the_sign_in_page_offers_it_with_mail(
    make_client_without_database: Callable[..., TestClient], sent_mail: SentMail
) -> None:
    client = make_client_without_database(mailer=sent_mail)
    assert "Forgot your password?" in text(client.get(paths.SIGN_IN))


# Links another administrator makes (inventory-tng-8i4.12.4 makes them from
# a page; the reset page already does what each says)


def made_link(session: Session, admin: Admin) -> str:
    """A reset another administrator made, which removed the password and
    authenticators already."""
    colleague, _ = new_admin(session, "colleague")
    _, token, _ = accounts.reset_admin_sign_in(
        session, admin.id, colleague.id, datetime.now(UTC), NO_MAIL
    )
    return token


def test_a_made_link_sets_a_password_and_tells_the_owner(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    """Its password went when it was made, so none is "changed": told anyway."""
    admin, _ = with_email(session)
    token = made_link(session, admin)
    form = {"token": token, "password": NEW_PASSWORD, "password_again": NEW_PASSWORD}
    assert client.post(paths.RESET, data=form).status_code == 303
    assert accounts.check_password(session, admin.id, NEW_PASSWORD)
    assert not accounts.uses_totp(session, admin.id)
    assert [m.kind for m in sent_mail] == ["password_changed"]


def test_a_made_link_asks_for_an_authenticator_only_while_required(
    client: TestClient, session: Session
) -> None:
    admin, _ = with_email(session)
    token = made_link(session, admin)
    asked = "Set up a new authenticator"
    assert asked in text(client.get(f"{paths.RESET}?token={token}"))
    policy(session, "optional")
    assert asked not in text(client.get(f"{paths.RESET}?token={token}"))


def test_an_authenticator_added_after_a_reset_goes_when_its_link_is_used(
    client: TestClient, session: Session
) -> None:
    """From a session cut off by the reset whose request was already under
    way: the owner must not meet a thief's authenticator at sign-in."""
    admin, _ = with_email(session)
    token = made_link(session, admin)
    accounts.add_authenticator(session, TOTP_KEYS, admin.id, "thief", "KEY", 0)
    form = {"token": token, "password": NEW_PASSWORD, "password_again": NEW_PASSWORD}
    assert client.post(paths.RESET, data=form).status_code == 303
    assert not accounts.uses_totp(session, admin.id)


def test_asking_by_mail_never_undoes_a_reset_another_made(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    """With the authenticators gone too, a mailbox alone would be enough."""
    admin, _ = with_email(session)
    token = made_link(session, admin)
    ask(client)
    assert sent_mail == []
    assert client.get(f"{paths.RESET}?token={token}").status_code == 200
    # One asked for just as the reset was made, so not voided by it, fails.
    emailed = reset_link(session, admin)
    assert accounts.find_reset_link(session, emailed, datetime.now(UTC)) is None


def test_mail_to_one_account_is_limited_whoever_asks(
    client: TestClient,
    session: Session,
    sent_mail: SentMail,
    make_client: Callable[..., TestClient],
) -> None:
    with_email(session)
    for n in range(5):
        ask(make_client(client.app, address=f"192.0.2.{10 + n}"))
    assert len(sent_mail) == 3


def test_using_reset_links_is_rate_limited(
    client: TestClient, session: Session, sent_mail: SentMail
) -> None:
    """Per address, counted before the link is looked up."""
    with_email(session)
    ask(client)
    token = token_of(the_link(sent_mail))
    short = {"token": token, "password": "short", "password_again": "short"}
    for _ in range(10):
        client.post(paths.RESET, data=short)
    limited = client.post(paths.RESET, data=short)
    assert limited.status_code == 429
