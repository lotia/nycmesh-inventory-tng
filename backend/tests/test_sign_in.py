"""Administrators signing in (docs/decisions/0012): setup, the one sign-in
form, sessions, a new authenticator and confirming sensitive actions."""

import html
import itertools
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import NoReturn

import pyotp
import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import Headers
from structlog.testing import capture_logs

from inventory_tng import accounts, sign_in
from inventory_tng.cross_site import cross_site
from inventory_tng.masking import Masker
from inventory_tng.models import (
    Admin,
    AdminPassword,
    AdminSession,
    Authenticator,
    RecoveryCode,
    SignInSettings,
)

TOKEN = "t" * 40
PASSWORD = "correct horse battery staple"
NOW = datetime(2026, 11, 2, 12, tzinfo=UTC)


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client(setup_token=TOKEN)


def new_admin(
    session: Session,
    username: str = "ada",
    *,
    totp: bool = True,
    email: str | None = None,
) -> tuple[Admin, str | None]:
    """An administrator with PASSWORD, and their authenticator's key."""
    admin = Admin(username=username, email=email)
    session.add(admin)
    session.flush()
    session.add(
        AdminPassword(id=admin.id, password_hash=accounts.hash_password(PASSWORD))
    )
    key = None
    if totp:
        key = accounts.new_totp_key()
        session.add(Authenticator(admin_id=admin.id, name="Phone", totp_key=key))
    session.flush()
    return admin, key


def code(key: str | None, at: datetime | None = None) -> str:
    assert key is not None
    return pyotp.TOTP(key).at(at or datetime.now(UTC))


def policy(session: Session, value: str) -> None:
    session.get_one(SignInSettings, 1).totp_policy = value
    session.flush()


def sign_in_with(
    client: TestClient, username: str = "ada", password: str = PASSWORD, **form: str
) -> Response:
    return client.post(
        sign_in.SIGN_IN,
        data={"username": username, "password": password} | form,
    )


def text(response: Response) -> str:
    """The page as read, with Jinja's escaping undone."""
    return html.unescape(response.text)


def alert(response: Response) -> str:
    match = re.search(r'role="alert">([^<]*)<', response.text)
    return html.unescape(match.group(1)) if match else ""


def hidden_key(response: Response) -> str:
    match = re.search(r'name="key" value="([A-Z2-7]{32})"', response.text)
    assert match is not None
    return match.group(1)


# Setup


def setup_form(key: str, **fields: str) -> dict[str, str]:
    return {
        "token": TOKEN,
        "username": "ada",
        "password": PASSWORD,
        "password_again": PASSWORD,
        "authenticator_name": "Phone",
        "key": key,
        "code": code(key),
    } | fields


def test_setup_makes_the_first_admin_signed_in_with_recovery_codes(
    client: TestClient, session: Session
) -> None:
    key = hidden_key(client.get(sign_in.SETUP))
    response = client.post(sign_in.SETUP, data=setup_form(key))
    assert response.status_code == 200
    codes = re.findall(r"<li>([A-Z0-9]{5}-[A-Z0-9]{5})</li>", response.text)
    assert len(codes) == accounts.RECOVERY_CODES
    assert response.headers["cache-control"] == "no-store"
    admin = session.scalars(select(Admin)).one()
    assert admin.username == "ada"
    (authenticator,) = session.scalars(select(Authenticator)).all()
    assert authenticator.totp_key == key
    # Signed in: the cookie opens the administrator pages.
    assert client.get(sign_in.HOME).status_code == 200
    # And setup is gone for good.
    assert client.get(sign_in.SETUP).status_code == 404
    assert client.post(sign_in.SETUP, data=setup_form(key)).status_code == 404


def test_setup_is_off_without_a_token(
    make_app: Callable[..., FastAPI],
) -> None:
    with TestClient(make_app()) as client:
        assert "Setup is off" in client.get(sign_in.SETUP).text
        refused = client.post(sign_in.SETUP, data=setup_form("A" * 32))
        assert refused.status_code == 403


def test_setup_refuses_a_wrong_token_then_too_many(client: TestClient) -> None:
    key = hidden_key(client.get(sign_in.SETUP))
    wrong = client.post(sign_in.SETUP, data=setup_form(key, token="x" * 40))
    assert wrong.status_code == 400
    assert "That isn't the setup token." in text(wrong)
    # The form keeps the key, so the app need not be set up again.
    assert hidden_key(wrong) == key
    for _ in range(9):
        client.post(sign_in.SETUP, data=setup_form(key, token="x" * 40))
    limited = client.post(sign_in.SETUP, data=setup_form(key))
    assert limited.status_code == 429


@pytest.mark.parametrize(
    ("fields", "problem"),
    [
        ({"username": "ada lovelace"}, "Use 1 to 64 letters"),
        ({"password": "short", "password_again": "short"}, "at least 15"),
        ({"password_again": PASSWORD + "!"}, "two passwords are different"),
        ({"email": "not an address"}, "doesn't look like an email"),
        ({"code": "000000"}, "That code didn't match"),
        ({"code": ""}, "That code didn't match"),
    ],
)
def test_setup_says_what_is_wrong(
    client: TestClient, session: Session, fields: dict[str, str], problem: str
) -> None:
    key = hidden_key(client.get(sign_in.SETUP))
    form = setup_form(key, **fields)
    if form["code"] == "000000" and code(key) == "000000":
        form["code"] = "111111"
    response = client.post(sign_in.SETUP, data=form)
    assert response.status_code == 400
    assert problem in text(response)
    assert hidden_key(response) == key
    assert session.scalars(select(Admin)).first() is None


def test_setup_without_totp_while_it_is_optional(
    client: TestClient, session: Session
) -> None:
    policy(session, "optional")
    key = hidden_key(client.get(sign_in.SETUP))
    response = client.post(sign_in.SETUP, data=setup_form(key, code=""))
    assert response.status_code == 303
    assert response.headers["location"] == sign_in.HOME
    assert session.scalars(select(Authenticator)).first() is None
    assert client.get(sign_in.HOME).status_code == 200


def test_setup_refuses_once_any_admin_exists_even_retired(
    client: TestClient, session: Session
) -> None:
    admin, _ = new_admin(session)
    admin.retired_at = NOW
    session.flush()
    assert client.get(sign_in.SETUP).status_code == 404
    with pytest.raises(accounts.SetupRefused):
        accounts.create_first_admin(
            session,
            username="eve",
            email=None,
            password=PASSWORD,
            authenticator_name="",
            totp_key="",
            step=None,
            user_agent=None,
            now=NOW,
        )


# Signing in


def test_signing_in_with_all_three(client: TestClient, session: Session) -> None:
    _, key = new_admin(session)
    response = sign_in_with(client, code=code(key), next="/admin?x=1")
    assert response.status_code == 303
    assert response.headers["location"] == "/admin?x=1"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie
    page = client.get(sign_in.HOME)
    assert page.status_code == 200
    assert "<strong>ada</strong>" in page.text


def test_every_refusal_says_the_same(client: TestClient, session: Session) -> None:
    _, key = new_admin(session)
    refusals = [
        sign_in_with(client, "nobody", code=code(key)),
        sign_in_with(client, password="wrong password here", code=code(key)),
        sign_in_with(client, code="000000" if code(key) != "000000" else "111111"),
        sign_in_with(client),
    ]
    assert {response.status_code for response in refusals} == {400}
    assert {alert(response) for response in refusals} == {
        "That username, password or code didn't match."
    }
    assert sign_in.COOKIE not in client.cookies


def test_an_unknown_name_is_checked_against_a_password_all_the_same(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    checked: list[str] = []
    verify = PasswordHasher.verify

    def spy(hasher: PasswordHasher, hash: str, password: str) -> bool:
        checked.append(hash)
        return verify(hasher, hash, password)

    monkeypatch.setattr(PasswordHasher, "verify", spy)
    with pytest.raises(accounts.SignInRefused):
        accounts.sign_in(
            session,
            "nobody",
            PASSWORD,
            "",
            address="192.0.2.1",
            user_agent=None,
            now=NOW,
        )
    assert len(checked) == 1


def test_a_code_works_once(client: TestClient, session: Session) -> None:
    _, key = new_admin(session)
    once = code(key)
    assert sign_in_with(client, code=once).status_code == 303
    client.cookies.clear()
    assert sign_in_with(client, code=once).status_code == 400


def second_factor(session: Session, admin_id: int, typed: str) -> str | None:
    authenticators = accounts.locked_authenticators(session, admin_id)
    return accounts.check_second_factor(session, admin_id, authenticators, typed, NOW)


def test_an_earlier_code_is_refused_after_a_later_one(session: Session) -> None:
    admin, key = new_admin(session)
    assert key is not None
    later = second_factor(session, admin.id, code(key, NOW))
    assert later == "totp"
    earlier = code(key, NOW - timedelta(seconds=30))
    assert second_factor(session, admin.id, earlier) is None


def test_a_code_a_step_either_side_is_accepted(session: Session) -> None:
    _, key = new_admin(session)
    assert key is not None
    for offset in (-30, 30):
        typed = code(key, NOW + timedelta(seconds=offset))
        assert accounts.accepted_step(key, typed, None, NOW) is not None
    stale = code(key, NOW - timedelta(seconds=90))
    if stale != code(key, NOW):
        assert accounts.accepted_step(key, stale, None, NOW) is None


def test_a_recovery_code_signs_in_once(client: TestClient, session: Session) -> None:
    admin, _ = new_admin(session)
    codes = accounts.make_recovery_codes(session, admin.id, NOW)
    with capture_logs() as logs:
        used = sign_in_with(client, code=codes[0].lower().replace("-", " "))
    assert used.status_code == 303
    assert any(line["event"] == "recovery code used" for line in logs)
    client.cookies.clear()
    assert sign_in_with(client, code=codes[0]).status_code == 400
    assert sign_in_with(client, code=codes[1]).status_code == 303


def test_new_recovery_codes_void_the_old(session: Session) -> None:
    admin, _ = new_admin(session)
    old = accounts.make_recovery_codes(session, admin.id, NOW)
    accounts.make_recovery_codes(session, admin.id, NOW)
    assert second_factor(session, admin.id, old[0]) is None
    current = session.scalars(
        select(RecoveryCode).where(RecoveryCode.voided_at.is_(None))
    ).all()
    assert len(current) == accounts.RECOVERY_CODES


def test_without_totp_the_password_is_enough(
    client: TestClient, session: Session
) -> None:
    policy(session, "optional")
    new_admin(session, totp=False)
    assert sign_in_with(client).status_code == 303
    assert client.get(sign_in.HOME).status_code == 200


def test_required_totp_sends_an_admin_without_it_to_set_it_up(
    client: TestClient, session: Session
) -> None:
    new_admin(session, totp=False)
    assert sign_in_with(client).status_code == 303
    home = client.get(sign_in.HOME)
    assert home.headers["location"] == sign_in.NEW_AUTHENTICATOR
    form = client.get(sign_in.NEW_AUTHENTICATOR)
    assert "needs an authenticator app" in form.text
    key = hidden_key(form)
    saved = client.post(
        sign_in.NEW_AUTHENTICATOR,
        data={"authenticator_name": "Phone", "key": key, "code": code(key)},
    )
    assert "Your recovery codes" in saved.text
    assert client.get(sign_in.HOME).status_code == 200


def from_many_addresses(
    make_client: Callable[..., TestClient], client: TestClient
) -> Iterator[TestClient]:
    """Clients of the same app, each from an address of its own."""
    for n in itertools.count(10):
        yield make_client(client.app, address=f"192.0.2.{n}")


def test_wrong_passwords_never_lock_an_admin_out(
    client: TestClient, session: Session, make_client: Callable[..., TestClient]
) -> None:
    """From many addresses, as an attacker would: only the address is
    limited, never the account."""
    _, key = new_admin(session)
    clients = from_many_addresses(make_client, client)
    for _ in range(20):
        sign_in_with(next(clients), password="wrong password here")
    assert sign_in_with(next(clients), code=code(key)).status_code == 303


def test_wrong_codes_after_a_right_password_are_limited(
    client: TestClient, session: Session, make_client: Callable[..., TestClient]
) -> None:
    _, key = new_admin(session)
    wrong = "000000" if code(key) != "000000" else "111111"
    clients = from_many_addresses(make_client, client)
    for _ in range(10):
        sign_in_with(next(clients), code=wrong)
    limited = sign_in_with(next(clients), code=code(key))
    assert limited.status_code == 429
    assert "Too many tries" in alert(limited)


def test_every_attempt_counts_against_its_address(
    client: TestClient, session: Session
) -> None:
    """Counted before checking, so a burst cannot all slip under it."""
    _, key = new_admin(session)
    for _ in range(10):
        sign_in_with(client, "nobody")
    assert sign_in_with(client, code=code(key)).status_code == 429


@pytest.mark.parametrize("target", ["//example.org/x", "https://example.org", "x"])
def test_sign_in_only_goes_on_to_this_site(
    client: TestClient, session: Session, target: str
) -> None:
    _, key = new_admin(session)
    response = sign_in_with(client, code=code(key), next=target)
    assert response.headers["location"] == sign_in.HOME


def test_a_page_sends_someone_signed_out_to_sign_in_and_back(
    client: TestClient,
) -> None:
    response = client.get("/admin?page=2")
    assert response.headers["location"] == "/admin/sign-in?next=/admin%3Fpage%3D2"


def reset_link(
    session: Session,
    admin: Admin,
    *,
    made_by: Admin | None = None,
    password: bool = True,
    authenticators: bool = False,
) -> str:
    """A reset link's token: emailed, unless another administrator made it."""
    return accounts.make_reset_link(
        session,
        admin.id,
        datetime.now(UTC),
        made_by_id=made_by.id if made_by else None,
        resets_password=password,
        resets_authenticators=authenticators,
        lifetime=timedelta(hours=1 if made_by is None else 24),
    )


def other_session(
    session: Session, admin: Admin, user_agent: str | None = None
) -> AdminSession:
    """Another session of theirs, as if signed in elsewhere."""
    return accounts.start_session(session, admin.id, user_agent, datetime.now(UTC))[0]


def signed_in_row(client: TestClient, session: Session) -> AdminSession:
    _, key = new_admin(session)
    sign_in_with(client, code=code(key))
    return session.scalars(select(AdminSession)).one()


def test_signing_out_ends_the_session(client: TestClient, session: Session) -> None:
    row = signed_in_row(client, session)
    cookie = client.cookies[sign_in.COOKIE]
    assert client.post(sign_in.SIGN_OUT).headers["location"] == sign_in.SIGN_IN
    assert row.ended_at is not None
    client.cookies.set(sign_in.COOKIE, cookie)
    assert client.get(sign_in.HOME).status_code == 303


def test_a_session_ends_when_it_expires(client: TestClient, session: Session) -> None:
    row = signed_in_row(client, session)
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.flush()
    assert client.get(sign_in.HOME).status_code == 303


def test_a_retired_admin_is_signed_out_and_cannot_sign_in(
    client: TestClient, session: Session
) -> None:
    row = signed_in_row(client, session)
    session.get_one(Admin, row.admin_id).retired_at = datetime.now(UTC)
    session.flush()
    assert client.get(sign_in.HOME).status_code == 303
    assert sign_in_with(client).status_code == 400


def test_the_session_length_comes_from_the_settings(
    client: TestClient, session: Session
) -> None:
    session.get_one(SignInSettings, 1).session_days = 30
    row = signed_in_row(client, session)
    assert row.expires_at - row.created_at > timedelta(days=29)


# Confirming sensitive actions


@pytest.mark.parametrize(
    ("confirmed", "last_sensitive", "open_"),
    [
        (None, None, False),
        (NOW - timedelta(minutes=59), None, True),
        (NOW - timedelta(minutes=61), None, False),
        # Busy all day: still asked once confirm_hours have passed.
        (NOW - timedelta(hours=23), NOW - timedelta(minutes=5), True),
        (NOW - timedelta(hours=24), NOW - timedelta(minutes=5), False),
        # Idle since the last sensitive action.
        (NOW - timedelta(hours=3), NOW - timedelta(minutes=61), False),
    ],
)
def test_the_confirmation_window(
    session: Session,
    confirmed: datetime | None,
    last_sensitive: datetime | None,
    open_: bool,
) -> None:
    row = AdminSession(confirmed_at=confirmed, last_sensitive_at=last_sensitive)
    settings = session.get_one(SignInSettings, 1)
    assert accounts.confirmation_open(row, settings, NOW) is open_


def test_a_sensitive_page_asks_for_a_code_then_carries_on(
    client: TestClient, session: Session
) -> None:
    row = signed_in_row(client, session)
    authenticator = session.scalars(select(Authenticator)).one()
    key = authenticator.totp_key
    # Signing in counts as confirming, so it goes straight through...
    assert client.get(sign_in.NEW_AUTHENTICATOR).status_code == 200
    # ...until the window closes. That does not sign anyone out.
    row.confirmed_at = row.last_sensitive_at = datetime.now(UTC) - timedelta(hours=2)
    session.flush()
    asked = client.get(sign_in.NEW_AUTHENTICATOR)
    assert asked.headers["location"] == (
        "/admin/confirm?next=/admin/authenticators/new"
    )
    assert client.get(sign_in.HOME).status_code == 200
    assert (
        "authenticator, or a recovery code"
        in client.get(asked.headers["location"]).text
    )
    # A wrong code is refused; the next step's code goes on.
    later = code(key, datetime.now(UTC) + timedelta(seconds=30))
    wrong = client.post(
        sign_in.CONFIRM, data={sign_in.CONFIRM_FIELD: "12345", "next": "/x"}
    )
    assert wrong.status_code == 400
    done = client.post(
        sign_in.CONFIRM,
        data={sign_in.CONFIRM_FIELD: later, "next": sign_in.NEW_AUTHENTICATOR},
    )
    assert done.headers["location"] == sign_in.NEW_AUTHENTICATOR
    assert client.get(sign_in.NEW_AUTHENTICATOR).status_code == 200


def test_without_totp_the_password_confirms(
    client: TestClient, session: Session
) -> None:
    policy(session, "optional")
    new_admin(session, totp=False)
    sign_in_with(client)
    row = session.scalars(select(AdminSession)).one()
    row.confirmed_at = datetime.now(UTC) - timedelta(hours=2)
    session.flush()
    assert "Your password" in client.get(sign_in.CONFIRM).text
    wrong = client.post(sign_in.CONFIRM, data={sign_in.CONFIRM_FIELD: "nope"})
    assert alert(wrong) == "That isn't your password."
    assert (
        client.post(sign_in.CONFIRM, data={sign_in.CONFIRM_FIELD: PASSWORD}).status_code
        == 303
    )
    assert row.confirmed_at is not None
    assert row.confirmed_at > datetime.now(UTC) - timedelta(minutes=1)


def test_a_second_authenticator_needs_its_own_name(
    client: TestClient, session: Session
) -> None:
    signed_in_row(client, session)
    key = hidden_key(client.get(sign_in.NEW_AUTHENTICATOR))
    taken = client.post(
        sign_in.NEW_AUTHENTICATOR,
        data={"authenticator_name": "phone", "key": key, "code": code(key)},
    )
    assert "already have one by that name" in taken.text
    added = client.post(
        sign_in.NEW_AUTHENTICATOR,
        data={"authenticator_name": "Laptop", "key": key, "code": code(key)},
    )
    # Not the first, so no new recovery codes.
    assert added.headers["location"] == sign_in.HOME
    assert len(session.scalars(select(Authenticator)).all()) == 2


# Cross-site requests


@pytest.mark.parametrize(
    ("method", "headers", "refused"),
    [
        ("POST", {"sec-fetch-site": "cross-site"}, True),
        ("POST", {"sec-fetch-site": "same-site"}, True),
        ("POST", {"sec-fetch-site": "same-origin"}, False),
        ("POST", {"sec-fetch-site": "none"}, False),
        ("POST", {"origin": "https://evil.example", "host": "inv.example"}, True),
        ("POST", {"origin": "null", "host": "inv.example"}, True),
        ("POST", {"origin": "https://inv.example", "host": "inv.example"}, False),
        ("POST", {}, False),
        ("GET", {"sec-fetch-site": "cross-site"}, False),
    ],
)
def test_cross_site_requests(
    method: str, headers: dict[str, str], refused: bool
) -> None:
    assert cross_site(method, Headers(headers)) is refused


def test_a_cross_site_sign_in_is_refused(client: TestClient, session: Session) -> None:
    _, key = new_admin(session)
    response = client.post(
        sign_in.SIGN_IN,
        data={"username": "ada", "password": PASSWORD, "code": code(key)},
        headers={"sec-fetch-site": "cross-site"},
    )
    assert response.status_code == 403
    assert sign_in.COOKIE not in client.cookies


def test_signing_in_logs_ids_the_masker_keeps(
    client: TestClient, session: Session
) -> None:
    _, key = new_admin(session)
    with capture_logs() as logs:
        sign_in_with(client, code=code(key))
    (line,) = [line for line in logs if line["event"] == "signed in"]
    kept = Masker().fields(line)
    assert kept["admin_id"] == line["admin_id"]
    assert kept["sign_in_id"] == line["sign_in_id"]
    assert "ada" not in str(line)


def test_losing_the_race_to_set_up_shows_not_found(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Someone else finished first while this one waited for the lock, which
    create_first_admin refuses (test_setup_refuses_once_any_admin_exists)."""
    key = hidden_key(client.get(sign_in.SETUP))

    def lost(*_args: object, **_kwargs: object) -> NoReturn:
        raise accounts.SetupRefused("closed")

    monkeypatch.setattr(accounts, "create_first_admin", lost)
    assert client.post(sign_in.SETUP, data=setup_form(key)).status_code == 404


def test_viewing_a_sensitive_page_keeps_nothing_open(
    client: TestClient, session: Session
) -> None:
    row = signed_in_row(client, session)
    client.get(sign_in.NEW_AUTHENTICATOR)
    assert row.last_sensitive_at is None


def test_a_rate_limited_confirmation_says_so(
    client: TestClient, session: Session
) -> None:
    signed_in_row(client, session)
    for _ in range(10):
        client.post(sign_in.CONFIRM, data={sign_in.CONFIRM_FIELD: "nope"})
    limited = client.post(sign_in.CONFIRM, data={sign_in.CONFIRM_FIELD: "nope"})
    assert limited.status_code == 429
    assert "Too many tries" in alert(limited)


def test_the_cookie_lasts_as_long_as_the_session(
    client: TestClient, session: Session
) -> None:
    _, key = new_admin(session)
    response = sign_in_with(client, code=code(key))
    row = session.scalars(select(AdminSession)).one()
    expires = row.expires_at.astimezone(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert f"expires={expires}" in response.headers["set-cookie"]
