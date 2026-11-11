"""Requests at once (inventory-tng-8i4.50.4): whatever was under way when
another administrator reset or retired an account waits for it, then finds
it done, and no two paths deadlock. The catalogue's rules hold the same way
(inventory-tng-8i4.58). On connections that really commit, so
none of these is a unit test; each is marked with what it tests:

- `concurrency`: two transactions at once, on two connections. One is held
  open, the other started on a thread (Committed.blocked), and the first
  finishes only once the second waits for its lock.
- `end_to_end`: requests through the whole app (its dependencies, error
  pages and work after the reply), on sessions of their own, as in
  production, or on the test's.
- `helper`: one helper's contract, on a real session.

CONTRIBUTING.md says how to run or skip a kind.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest
from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient
from httpx2 import Response as Answer
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from inventory_tng import (
    accounts,
    categories,
    items,
    locations,
    mail,
    open_packages,
    sign_in,
    volunteers,
)
from inventory_tng.admin.admins import ADMINS
from inventory_tng.admin.volunteers import VOLUNTEERS
from inventory_tng.db import (
    Base,
    DbSession,
    RuleBroken,
    hold_until_commit,
    locked,
    locked_row,
    release_hold,
)
from inventory_tng.ledger import (
    Batch,
    Entry,
    Form,
    Recorded,
    insert_transaction,
    record,
)
from inventory_tng.mail import Message, Notify
from inventory_tng.models import (
    Admin,
    AdminPassword,
    AdminSession,
    Category,
    Item,
    LedgerTransaction,
    Location,
    SentForm,
    SignInSettings,
    Volunteer,
)

from helpers.concurrency import Committed, Worker, delete_made, made, newest_ids
from helpers.mail import NO_MAIL, SentMail
from helpers.rows import item_fields, tag
from helpers.sign_in import (
    PASSWORD,
    TOTP_KEYS,
    code,
    new_admin,
    reset_link,
    sign_in_with,
)
from helpers.stock import Spools, committed_place

NEW_PASSWORD = "a long passphrase of their own"

# Ada's id, Ben's id and his authenticator's key.
Admins = tuple[int, int, str | None]


@pytest.fixture
def admins(committed: Committed) -> Admins:
    session = committed.session()
    ada, _ = new_admin(session)
    ben, key = new_admin(session, "ben", email="ben@example.org")
    session.commit()
    return ada.id, ben.id, key


def signing_in(
    session: Session, key: str | None
) -> tuple[AdminSession, str, accounts.Factor]:
    return accounts.sign_in(
        session,
        "ben",
        PASSWORD,
        code(key),
        address="192.0.2.80",
        user_agent=None,
        now=datetime.now(UTC),
        keys=TOTP_KEYS,
    )


def resetting(committed: Committed, ada: int, ben: int) -> tuple[Session, str]:
    """Ada resetting Ben's sign-in, not yet committed, and its link."""
    session = committed.session()
    _, token, _ = accounts.reset_admin_sign_in(
        session, ben, ada, datetime.now(UTC), NO_MAIL
    )
    return session, token


def signing_in_blocked(committed: Committed, key: str | None) -> Worker:
    """Ben signing in, once it waits for a lock."""
    signing = committed.session()
    return committed.blocked(signing, lambda: signing_in(signing, key))


def reset_blocked(committed: Committed, ada: int, ben: int) -> tuple[Session, Worker]:
    """Ada resetting Ben's sign-in, once it waits for a lock."""
    reset = committed.session()
    worker = committed.blocked(
        reset,
        lambda: accounts.reset_admin_sign_in(
            reset, ben, ada, datetime.now(UTC), NO_MAIL
        ),
    )
    return reset, worker


def started(committed: Committed, ben: int, now: datetime) -> int:
    """A session of Ben's, committed."""
    setup = committed.session()
    session_id = accounts.start_session(setup, ben, None, now)[0].id
    setup.commit()
    return session_id


def gate_held(committed: Committed, by: int) -> Session:
    """The administrators lock, held (as a gated form, a reset, a retire or
    a restore holds it), not yet committed."""
    session = committed.session()
    assert accounts.lock_admins(session, by)
    return session


def gate_blocked(committed: Committed, session_id: int, now: datetime) -> Worker:
    """A sensitive form of that session at the gate, once it waits."""
    form = committed.session()
    row = form.get_one(AdminSession, session_id)
    return committed.blocked(
        form, lambda: accounts.hold_signed_in(form, row, lambda: now)
    )


def signed(
    session: Session, admin_id: int, session_id: int, *, uses_totp: bool
) -> sign_in.SignedIn:
    """What a request knew of them before the gate, as signed_in reads it."""
    return sign_in.SignedIn(
        admin=session.get_one(Admin, admin_id),
        row=session.get_one(AdminSession, session_id),
        settings=session.get_one(SignInSettings, 1),
        uses_totp=uses_totp,
    )


def emailed_link(committed: Committed, ben: int) -> str:
    setup = committed.session()
    token = reset_link(setup, setup.get_one(Admin, ben))
    setup.commit()
    return token


def using(committed: Committed, token: str) -> Session:
    """A link being used, not yet committed."""
    session = committed.session()
    now = datetime.now(UTC)
    found = accounts.find_reset_link(session, token, now, lock=True)
    assert found is not None
    accounts.use_reset_link(session, found[0], NEW_PASSWORD, now)
    return session


@pytest.mark.concurrency
def test_a_sign_in_under_way_waits_for_a_reset_then_is_refused(
    committed: Committed, admins: Admins
) -> None:
    ada, ben, key = admins
    reset, _ = resetting(committed, ada, ben)
    worker = signing_in_blocked(committed, key)
    reset.commit()
    error = worker.finished().error
    assert isinstance(error, accounts.SignInRefused)
    assert error.reason == "bad_password"


@pytest.mark.concurrency
def test_a_sign_in_under_way_waits_for_a_retire_then_is_refused(
    committed: Committed, admins: Admins
) -> None:
    """Retiring removes the password, which the sign-in reads again once it
    holds the account's lock."""
    ada, ben, key = admins
    retiring = committed.session()
    accounts.retire_admin(retiring, ben, ada, datetime.now(UTC), NO_MAIL)
    worker = signing_in_blocked(committed, key)
    retiring.commit()
    error = worker.finished().error
    assert isinstance(error, accounts.SignInRefused)
    assert error.reason == "bad_password"


@pytest.mark.concurrency
def test_a_reset_waits_for_a_sign_in_under_way_then_ends_its_session(
    committed: Committed, admins: Admins
) -> None:
    ada, ben, key = admins
    signing = committed.session()
    row, _, _ = signing_in(signing, key)
    reset, worker = reset_blocked(committed, ada, ben)
    signing.commit()
    assert worker.finished().error is None
    reset.commit()
    assert committed.session().get_one(AdminSession, row.id).ended_at is not None


@pytest.mark.concurrency
def test_a_sign_in_under_way_waits_for_an_emailed_link_used(
    committed: Committed, admins: Admins
) -> None:
    """Using the link changes the password and ends every session, so a
    sign-in with the old one must not slip past it."""
    _, ben, key = admins
    use = using(committed, emailed_link(committed, ben))
    worker = signing_in_blocked(committed, key)
    use.commit()
    assert isinstance(worker.finished().error, accounts.SignInRefused)


@pytest.mark.concurrency
def test_a_form_under_way_waits_for_a_reset_then_is_turned_away(
    committed: Committed, admins: Admins
) -> None:
    """Every sensitive form passes this gate (sign_in.confirmed): changing
    the password, adding an authenticator, making recovery codes, or any
    administrator page's change. Turned away, it changes nothing."""
    ada, ben, _ = admins
    now = datetime.now(UTC)
    session_id = started(committed, ben, now)
    reset, _ = resetting(committed, ada, ben)
    worker = gate_blocked(committed, session_id, now)
    reset.commit()
    assert worker.finished().result is False


@pytest.mark.concurrency
@pytest.mark.parametrize("emailed", [True, False])
def test_a_link_being_used_while_a_reset_is_made_does_not_deadlock(
    committed: Committed, admins: Admins, emailed: bool
) -> None:
    """Using a link locks its administrator, then the link, then the
    password, as a reset locks them: so a reset that comes while a link is
    found but not yet used waits, then undoes it (inventory-tng-8i4.50.3).
    A reset's own link inserts the password afresh, which must not wait on
    the reset's lock."""
    ada, ben, _ = admins
    if emailed:
        token = emailed_link(committed, ben)
    else:
        setup, token = resetting(committed, ada, ben)
        setup.commit()
    use = committed.session()
    now = datetime.now(UTC)
    found = accounts.find_reset_link(use, token, now, lock=True)
    assert found is not None
    reset, worker = reset_blocked(committed, ada, ben)
    accounts.use_reset_link(use, found[0], NEW_PASSWORD, now)
    use.commit()
    assert worker.finished().error is None
    reset.commit()
    assert not accounts.has_password(committed.session(), ben)


@pytest.mark.concurrency
def test_a_code_being_confirmed_while_a_reset_is_made_does_not_deadlock(
    committed: Committed, admins: Admins
) -> None:
    """Confirming locks their authenticators, then writes their session;
    a reset ends sessions, then retires authenticators: both lock the
    administrator's row first, so one waits for the other."""
    ada, ben, key = admins
    now = datetime.now(UTC)
    session_id = started(committed, ben, now)
    confirming = committed.session()
    row = confirming.get_one(AdminSession, session_id)
    # Later than the session's start, so confirming writes its session row.
    later = now + timedelta(seconds=1)
    confirmed = accounts.confirm(
        confirming,
        TOTP_KEYS,
        row,
        code(key, later),
        later,
        recovery=True,
        account_change=False,
    )
    assert confirmed[0] == "confirmed"
    reset, worker = reset_blocked(committed, ada, ben)
    confirming.commit()
    assert worker.finished().error is None
    reset.commit()


@pytest.mark.concurrency
def test_a_form_under_way_waits_for_its_session_ended_elsewhere(
    committed: Committed, admins: Admins
) -> None:
    """Changing the password in another tab ends this session but keeps the
    account able to sign in: only reading the session again finds it."""
    _, ben, _ = admins
    now = datetime.now(UTC)
    setup = committed.session()
    this = accounts.start_session(setup, ben, None, now)[0].id
    other = accounts.start_session(setup, ben, None, now)[0].id
    setup.commit()
    tab = gate_held(committed, ben)
    accounts.change_password(tab, tab.get_one(AdminSession, other), NEW_PASSWORD, now)
    worker = gate_blocked(committed, this, now)
    tab.commit()
    assert worker.finished().result is False


@pytest.mark.concurrency
def test_an_emailed_reset_asked_for_meanwhile_goes_to_the_new_address(
    committed: Committed, admins: Admins
) -> None:
    """A change of email voids links to the old one; one asked for while it
    was under way must not reach the old address either."""
    _, ben, _ = admins
    now = datetime.now(UTC)
    changing = gate_held(committed, ben)
    accounts.change_email(
        changing, changing.get_one(Admin, ben), "new@example.org", now
    )
    asking = committed.session()
    worker = committed.blocked(
        asking, lambda: accounts.emailed_reset(asking, "ben", now)
    )
    changing.commit()
    asked = worker.finished().result
    assert isinstance(asked, accounts.EmailedReset)
    assert asked.email == "new@example.org"


@pytest.mark.concurrency
def test_a_recovery_under_way_waits_for_a_retire_then_is_refused(
    committed: Committed, admins: Admins
) -> None:
    ada, ben, _ = admins
    token = "a recovery token the operator set, long"
    retiring = committed.session()
    accounts.retire_admin(retiring, ben, ada, datetime.now(UTC), NO_MAIL)
    recovering = committed.session()
    worker = committed.blocked(
        recovering,
        lambda: accounts.recover(
            recovering, token, token, "ben", "192.0.2.82", datetime.now(UTC), NO_MAIL
        ),
    )
    retiring.commit()
    error = worker.finished().error
    assert isinstance(error, accounts.TokenRefused)
    assert error.reason == "no_admin"


@pytest.mark.concurrency
@pytest.mark.parametrize("all_of_them", [True, False])
def test_their_lock_holds_up_no_insert_that_refers_to_an_administrator(
    committed: Committed, admins: Admins, all_of_them: bool
) -> None:
    """A row naming an administrator (a session, a link, a ledger entry)
    only needs their row not to vanish, which the lock allows: the gate's
    on every row (lock_admins), or one path's on theirs (lock_admin)."""
    ada, _, _ = admins
    if all_of_them:
        holding = gate_held(committed, ada)
    else:
        holding = committed.session()
        accounts.lock_admin(holding, ada)
    other = committed.session()
    accounts.start_session(other, ada, None, datetime.now(UTC))
    other.commit()
    holding.rollback()


@pytest.mark.concurrency
def test_a_link_looked_up_while_a_reset_commits_comes_back_void(
    committed: Committed, admins: Admins
) -> None:
    """Found usable before its administrator's lock, the link is read again
    once locked: a reset made meanwhile voided it."""
    ada, ben, _ = admins
    token = emailed_link(committed, ben)
    reset, _ = resetting(committed, ada, ben)
    use = committed.session()
    worker = committed.blocked(
        use, lambda: accounts.find_reset_link(use, token, datetime.now(UTC), lock=True)
    )
    reset.commit()
    assert worker.finished().result is None


@pytest.fixture
def carol(committed: Committed) -> tuple[int, int]:
    """Carol, without TOTP, and a session of hers, committed."""
    setup = committed.session()
    admin, _ = new_admin(setup, "carol", totp=False)
    setup.commit()
    return admin.id, started(committed, admin.id, datetime.now(UTC))


def setting_up_totp(committed: Committed, admin_id: int) -> Session:
    """An authenticator being added for them, not yet committed."""
    session = committed.session()
    accounts.lock_admin(session, admin_id)
    accounts.add_authenticator(
        session, TOTP_KEYS, admin_id, "Phone", accounts.new_totp_key(), 0
    )
    return session


@pytest.mark.concurrency
def test_a_password_confirmed_while_totp_is_set_up_is_refused(
    committed: Committed, admins: Admins, carol: tuple[int, int]
) -> None:
    """Once locked, the password no longer confirms: a code is needed."""
    del admins
    carol_id, session_id = carol
    adding = setting_up_totp(committed, carol_id)
    confirming = committed.session()
    row = confirming.get_one(AdminSession, session_id)
    worker = committed.blocked(
        confirming,
        lambda: accounts.confirm(
            confirming,
            TOTP_KEYS,
            row,
            PASSWORD,
            datetime.now(UTC),
            recovery=True,
            account_change=False,
        )[0],
    )
    adding.commit()
    assert worker.finished().result == "refused"


@pytest.mark.concurrency
def test_the_gate_reads_again_whether_they_use_totp(
    committed: Committed, admins: Admins, carol: tuple[int, int]
) -> None:
    """What the request knew before it waited (no TOTP) is read again."""
    del admins
    carol_id, session_id = carol
    adding = setting_up_totp(committed, carol_id)
    form = committed.session()
    before = signed(form, carol_id, session_id, uses_totp=False)
    worker = committed.blocked(form, lambda: sign_in._hold(form, before).uses_totp)
    adding.commit()
    assert worker.finished().result is True


@pytest.mark.concurrency
@pytest.mark.parametrize("change", ["retired", "no_password"])
def test_the_gate_turns_away_one_who_can_no_longer_sign_in(
    committed: Committed, admins: Admins, change: str
) -> None:
    """With their session still going: only reading the account again under
    the lock finds it (lock_admins)."""
    ada, ben, _ = admins
    now = datetime.now(UTC)
    session_id = started(committed, ben, now)
    changing = gate_held(committed, ada)
    if change == "retired":
        changing.get_one(Admin, ben).retired_at = now
    else:
        changing.execute(delete(AdminPassword).where(AdminPassword.id == ben))
    changing.flush()
    form = committed.session()
    row = form.get_one(AdminSession, session_id)
    # Loaded before the wait and kept, as the request keeps it: the session
    # holds only what something refers to.
    loaded = form.get_one(Admin, ben)
    worker = committed.blocked(
        form, lambda: accounts.hold_signed_in(form, row, lambda: now)
    )
    changing.commit()
    assert worker.finished().result is False
    assert loaded.retired_at is not None or change == "no_password"


@pytest.mark.concurrency
def test_the_gate_turns_away_a_session_that_ran_out_while_it_waited(
    committed: Committed, admins: Admins, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The time is read after the wait, not before: the clock is moved past
    the session's end while the form waits."""
    ada, ben, _ = admins
    now = datetime.now(UTC)
    session_id = started(committed, ben, now)
    clock = [now]
    monkeypatch.setattr(sign_in, "_now", lambda: clock[0])
    holding = gate_held(committed, ada)
    form = committed.session()
    before = signed(form, ben, session_id, uses_totp=True)
    worker = committed.blocked(form, lambda: sign_in._hold(form, before))
    clock[0] = before.row.expires_at + timedelta(seconds=1)
    holding.commit()
    assert isinstance(worker.finished().error, sign_in.Redirect)


@pytest.mark.concurrency
def test_a_rename_after_another_is_not_lost(
    committed: Committed, admins: Admins
) -> None:
    """Loaded before another session renamed it, the authenticator is read
    again as it is locked, so setting the old name back is a change."""
    _, ben, _ = admins
    first = committed.session()
    (phone,) = accounts.authenticators(first, ben)
    other = committed.session()
    assert accounts.rename_authenticator(other, ben, phone.id, "Work")
    other.commit()
    assert accounts.rename_authenticator(first, ben, phone.id, "Phone")
    first.commit()
    (renamed,) = accounts.authenticators(committed.session(), ben)
    assert renamed.name == "Phone"


@pytest.mark.helper
def test_cleanup_fails_at_once_on_a_lock_left_held(
    committed: Committed, owner_engine: Engine
) -> None:
    """committed's cleanup waits only briefly for a lock, so one a worker
    left held fails the test, saying so, rather than hanging the run
    (inventory-tng-8i4.105). Here a shorter wait than the fixture's."""
    with owner_engine.connect() as connection:
        before = newest_ids(connection)
    category = Category(name=f"Radios {tag()}")
    made(committed, category)
    holding = committed.session()
    holding.execute(locked(select(Category.id).where(Category.id == category.id)))
    # On a thread, so this test fails rather than hangs if cleanup waits.
    cleanup = Worker(lambda: delete_made(owner_engine, before, wait="50ms"))
    error = cleanup.finished().error
    holding.rollback()
    assert isinstance(error, RuntimeError), error
    assert "a lock another connection still holds" in str(error)


@pytest.mark.helper
def test_a_held_transaction_may_not_be_followed_by_another_commit(
    committed: Committed,
) -> None:
    """A commit releases the gate's lock, so work after one is unguarded."""
    session = committed.session()
    session.execute(text("SELECT 1"))
    hold_until_commit(session)
    session.commit()
    session.execute(text("SELECT 1"))
    with pytest.raises(RuntimeError, match="lock was gone"):
        session.commit()
    release_hold(session)
    session.rollback()


@pytest.mark.end_to_end
def test_a_gated_form_commits_once_and_its_work_after_the_reply_is_free(
    admin_client: TestClient,
) -> None:
    app = cast(FastAPI, admin_client.app)

    def commit_more(session: Session) -> None:
        session.execute(text("SELECT 1"))
        session.commit()

    @app.post("/admin/commits-once")
    def once(
        _: sign_in.ConfirmedAdmin, session: DbSession, tasks: BackgroundTasks
    ) -> Response:
        session.commit()
        tasks.add_task(commit_more, session)
        return Response()

    @app.post("/admin/commits-twice")
    def twice(_: sign_in.ConfirmedAdmin, session: DbSession) -> Response:
        session.commit()
        commit_more(session)
        return Response()

    assert admin_client.post("/admin/commits-once").status_code == 200
    # The app answers its own errors: the second commit raised, so 500.
    assert admin_client.post("/admin/commits-twice").status_code == 500
    # The hold ended with that request: later ones commit as they like.
    assert admin_client.post(sign_in.SIGN_OUT).status_code == 303


@pytest.fixture
def own_client(
    committed: Committed, make_client: Callable[..., TestClient]
) -> Callable[[SentMail], TestClient]:
    """Clients of the app with a session of its own per request, as in
    production, on the committed test database, sending `mail` to the
    given catcher: a request then waits on another connection's lock as it
    would for real, and `committed` deletes what it commits, after."""
    del committed
    return lambda mail: make_client(mailer=mail, own_sessions=True)


@pytest.fixture
def real_client(
    own_client: Callable[[SentMail], TestClient],
) -> tuple[TestClient, SentMail]:
    sent = SentMail()
    return own_client(sent), sent


def signed_in_as_ben(committed: Committed, client: TestClient, key: str | None) -> int:
    assert sign_in_with(client, "ben", code=code(key)).status_code == 303
    found = committed.session().scalar(
        select(AdminSession.id).where(AdminSession.ended_at.is_(None))
    )
    assert found is not None
    return found


def needing_a_code(committed: Committed, ben: int, session_id: int) -> str:
    """One of Ben's recovery codes, with his session's window closed, so a
    form must bring a code."""
    setup = committed.session()
    recovery = accounts.make_recovery_codes(setup, ben, datetime.now(UTC))[0]
    setup.get_one(AdminSession, session_id).confirmed_at = None
    setup.commit()
    return recovery


def posting(committed: Committed, client: TestClient, form: dict[str, str]) -> Worker:
    """A password change posted, once it waits for a lock."""
    return committed.blocked(
        None, lambda: client.post(f"{sign_in.ACCOUNT}/password", data=form)
    )


PASSWORD_FORM = {"password": NEW_PASSWORD, "password_again": NEW_PASSWORD}


@pytest.mark.end_to_end
def test_a_sensitive_form_posted_during_a_reset_is_sent_to_sign_in(
    committed: Committed, admins: Admins, real_client: tuple[TestClient, SentMail]
) -> None:
    """Through the whole app: the gate (sign_in.confirmed) waits for the
    reset, then turns the form away, and nothing it asked for is done."""
    client, _ = real_client
    ada, ben, key = admins
    signed_in_as_ben(committed, client, key)
    reset, _ = resetting(committed, ada, ben)
    worker = posting(committed, client, PASSWORD_FORM)
    reset.commit()
    answer = cast(Answer, worker.finished().result)
    assert answer.headers["location"] == sign_in.SIGN_IN
    assert not accounts.has_password(committed.session(), ben)


@pytest.mark.end_to_end
def test_a_recovery_code_used_on_a_form_turned_away_is_still_told(
    committed: Committed, admins: Admins, real_client: tuple[TestClient, SentMail]
) -> None:
    """Confirmed and committed, the code is used up, then the gate turns the
    form away, as Ben's session ended meanwhile: he is mailed anyway, in
    case it was not him."""
    client, sent = real_client
    ada, ben, key = admins
    recovery = needing_a_code(committed, ben, signed_in_as_ben(committed, client, key))
    # Ada's row only: Ben's code goes through, then the gate waits on it.
    ending = committed.session()
    accounts.lock_admin(ending, ada)
    form = PASSWORD_FORM | {sign_in.CONFIRM_FIELD: recovery}
    worker = posting(committed, client, form)
    accounts.end_sessions(ending, ben, datetime.now(UTC))
    ending.commit()
    answer = cast(Answer, worker.finished().result)
    assert answer.headers["location"] == sign_in.SIGN_IN
    assert accounts.recovery_codes_left(committed.session(), ben) == 9
    assert [m.kind for m in sent] == ["recovery_code_used"]


class LockProbingMail(SentMail):
    """Mail that, as it is sent, tries for every administrator's lock from
    another connection: a request still holding it would time it out."""

    def __init__(self, committed: Committed, admin_id: int) -> None:
        super().__init__()
        self.committed, self.admin_id = committed, admin_id
        self.lock_free: list[bool] = []

    def send(self, message: Message) -> None:
        probe = self.committed.session()
        probe.execute(text("SET LOCAL lock_timeout = '1s'"))
        try:
            self.lock_free.append(accounts.lock_admins(probe, self.admin_id))
        except OperationalError:
            self.lock_free.append(False)
        finally:
            probe.rollback()
        super().send(message)


@pytest.mark.end_to_end
def test_the_hold_ends_with_the_route_on_the_apps_own_sessions(
    committed: Committed,
    admins: Admins,
    own_client: Callable[[SentMail], TestClient],
) -> None:
    """As in production (db.get_session), not the test's stand-in: work
    after the reply that commits on the request's session is not taken for
    the route committing twice."""
    _, _, key = admins
    client = own_client(SentMail())
    app = cast(FastAPI, client.app)
    after: list[str] = []

    def commit_after(session: Session) -> None:
        session.execute(text("SELECT 1"))
        session.commit()
        after.append("committed")

    @app.post("/admin/commits-then-after")
    def route(
        _: sign_in.ConfirmedAdmin, session: DbSession, tasks: BackgroundTasks
    ) -> Response:
        session.commit()
        tasks.add_task(commit_after, session)
        return Response()

    signed_in_as_ben(committed, client, key)
    assert client.post("/admin/commits-then-after").status_code == 200
    assert after == ["committed"]


@pytest.mark.end_to_end
def test_a_form_refused_without_a_commit_lets_go_of_its_lock_before_mail(
    committed: Committed,
    admins: Admins,
    own_client: Callable[[SentMail], TestClient],
) -> None:
    """Through the whole app, with a session per request: a gated form takes
    every administrator's lock, queues mail for after the reply and returns
    with neither a commit nor a rollback, as a refusal may. The request's
    session closes as the route returns (db.DbSession), so the mail goes
    out with the lock already let go, not held while the provider
    answers."""
    ada, _, key = admins
    sent = LockProbingMail(committed, ada)
    client = own_client(sent)
    app = cast(FastAPI, client.app)

    @app.post("/admin/refused-with-mail")
    def refused(signed: sign_in.ConfirmedAdmin, notify: Notify) -> Response:
        notify.tell(signed.admin.email, signed.admin.id, mail.recovery_code_used(9))
        return Response(status_code=400)

    signed_in_as_ben(committed, client, key)
    assert client.post("/admin/refused-with-mail").status_code == 400
    assert [m.kind for m in sent] == ["recovery_code_used"]
    assert sent.lock_free == [True]


@pytest.mark.end_to_end
def test_a_form_that_waited_is_asked_for_a_code_if_the_window_shrank(
    committed: Committed, admins: Admins, real_client: tuple[TestClient, SentMail]
) -> None:
    client, _ = real_client
    ada, _, key = admins
    session_id = signed_in_as_ben(committed, client, key)
    setup = committed.session()
    row = setup.get_one(AdminSession, session_id)
    row.confirmed_at = datetime.now(UTC) - timedelta(minutes=2)
    setup.commit()
    changing = gate_held(committed, ada)
    settings = changing.get_one(SignInSettings, 1)
    was = settings.confirm_idle_minutes
    settings.confirm_idle_minutes = 1
    worker = posting(committed, client, PASSWORD_FORM)
    changing.commit()
    try:
        answer = cast(Answer, worker.finished().result)
        assert answer.status_code == 403
        assert sign_in.CONFIRM_FIELD in answer.text
    finally:
        restoring = committed.session()
        restoring.get_one(SignInSettings, 1).confirm_idle_minutes = was
        restoring.commit()


# The catalogue's rules under concurrent writes (inventory-tng-8i4.58): the
# second write waits for the catalogue lock, or the row, then is refused,
# rather than the two deadlocking or both passing.

NOW = datetime.now(UTC)

# A request's write: a domain function, on the request's session.
Write = Callable[[Session], object]


def at_once(committed: Committed, first: Write, second: Write) -> str | None:
    """The first written, and committed while the second waits: the rule
    the second was refused by, if any."""
    one, two = committed.session(), committed.session()
    first(one)
    one.flush()

    def write_second() -> None:
        second(two)
        two.flush()

    worker = committed.blocked(two, write_second)
    one.commit()
    error = worker.finished().error
    two.rollback()
    if error is None:
        return None
    assert isinstance(error, RuleBroken), error
    return error.rule


def either_first(
    committed: Committed, one: Write, other: Write, *, swapped: bool
) -> str | None:
    """at_once, with `one` first, or `other` if `swapped`."""
    return at_once(committed, *((other, one) if swapped else (one, other)))


def doing[M: Base](
    model: type[M], ident: int, change: Callable[[Session, M], object]
) -> Write:
    """As a page does: the row loaded, then changed by its domain function,
    which locks it."""
    return lambda session: change(session, session.get_one(model, ident))


def catalogue(committed: Committed) -> tuple[int, int]:
    """A category, and a retired item in it, committed."""
    name = tag()
    category = Category(name=f"Radios {name}")
    made(committed, category)
    item = Item(name=f"LiteBeam {name}", category_id=category.id, retired_at=NOW)
    made(committed, item)
    return category.id, item.id


def retiring_category(category_id: int) -> Write:
    return doing(Category, category_id, lambda s, c: categories.retire(s, c, NOW))


@pytest.mark.concurrency
@pytest.mark.parametrize(
    ("swapped", "refused"),
    [(False, "category_has_items"), (True, "item_category_current")],
)
def test_an_item_comes_back_or_its_category_goes_not_both(
    committed: Committed, swapped: bool, refused: str
) -> None:
    category_id, item_id = catalogue(committed)
    restoring = doing(Item, item_id, items.restore)
    retiring = retiring_category(category_id)
    assert either_first(committed, restoring, retiring, swapped=swapped) == refused


def two_volunteers(committed: Committed) -> tuple[int, int]:
    name = tag()
    a, b = Volunteer(name=f"A {name}"), Volunteer(name=f"B {name}")
    made(committed, a, b)
    return a.id, b.id


def merging(volunteer_id: int, into_id: int) -> Write:
    return doing(
        Volunteer, volunteer_id, lambda s, v: volunteers.merge(s, v, into_id, NOW)
    )


def retiring_volunteer(volunteer_id: int) -> Write:
    return doing(Volunteer, volunteer_id, lambda s, v: volunteers.retire(s, v, NOW))


@pytest.mark.concurrency
@pytest.mark.parametrize("swapped", [False, True])
def test_a_merge_and_a_retire_of_its_target_not_both(
    committed: Committed, swapped: bool
) -> None:
    duplicate, kept = two_volunteers(committed)
    one, other = merging(duplicate, kept), retiring_volunteer(kept)
    refused = either_first(committed, one, other, swapped=swapped)
    assert refused == "volunteer_merge_target"


@pytest.mark.concurrency
def test_crossing_merges_queue_and_the_second_is_refused(
    committed: Committed,
) -> None:
    a, b = two_volunteers(committed)
    assert at_once(committed, merging(a, b), merging(b, a)) == "volunteer_merge_target"


@pytest.mark.concurrency
@pytest.mark.parametrize("swapped", [False, True])
def test_a_volunteer_starts_holding_stock_or_is_retired_not_both(
    committed: Committed, swapped: bool
) -> None:
    volunteer_id, _ = two_volunteers(committed)
    holding = doing(Volunteer, volunteer_id, volunteers.hold_stock)
    retiring = retiring_volunteer(volunteer_id)
    refused = either_first(committed, holding, retiring, swapped=swapped)
    assert refused == "location_custody_volunteer"


def stores(committed: Committed) -> tuple[int, int, int]:
    """Two stores, and an area in the first, committed."""
    name = tag()
    one, two = (Location(name=f"Store {n} {name}", kind="store") for n in "12")
    made(committed, one, two)
    shelf = Location(name=f"Shelf {name}", kind="area", parent_id=one.id)
    made(committed, shelf)
    return one.id, two.id, shelf.id


def moving(location_id: int, parent_id: int) -> Write:
    return doing(
        Location,
        location_id,
        lambda s, place: locations.save(s, place, place.name, parent_id, seen=None),
    )


@pytest.mark.concurrency
@pytest.mark.parametrize("swapped", [False, True])
def test_an_area_moves_in_or_its_new_place_is_retired_not_both(
    committed: Committed, swapped: bool
) -> None:
    _, other, shelf = stores(committed)
    retiring = doing(Location, other, lambda s, p: locations.retire(s, p, NOW))
    refused = either_first(committed, moving(shelf, other), retiring, swapped=swapped)
    assert refused == "location_parent_current"


@pytest.mark.concurrency
def test_crossing_moves_queue_and_the_second_is_refused(committed: Committed) -> None:
    """Each alone is fine; together they would make a cycle."""
    store, _, shelf = stores(committed)
    bin_ = Location(name=f"Bin {tag()}", kind="area", parent_id=store)
    made(committed, bin_)
    refused = at_once(committed, moving(shelf, bin_.id), moving(bin_.id, shelf))
    assert refused == "location_no_cycle"


@pytest.mark.concurrency
def test_an_item_added_while_its_category_is_retired(committed: Committed) -> None:
    """The retire waits for the add, then is refused."""
    category_id, _ = catalogue(committed)

    def adding(session: Session) -> Item:
        return items.add(
            session, items.Fields.new(name=f"New {tag()}", category_id=category_id)
        )

    refused = at_once(committed, adding, retiring_category(category_id))
    assert refused == "category_has_items"


@pytest.mark.concurrency
def test_an_item_added_once_its_category_is_locked_does_not_deadlock(
    committed: Committed,
) -> None:
    """The retire's page has locked the category when the add starts: the
    add waits for the catalogue lock the page took first, rather than
    holding it and waiting on the category, then is refused."""
    category_id, _ = catalogue(committed)
    retiring, adding = committed.session(), committed.session()
    category = locked_row(retiring, Category, category_id)
    assert category is not None

    def add() -> None:
        items.add(
            adding, items.Fields.new(name=f"New {tag()}", category_id=category_id)
        )

    worker = committed.blocked(adding, add)
    categories.retire(retiring, category, NOW)
    retiring.commit()
    error = worker.finished().error
    adding.rollback()
    assert isinstance(error, RuleBroken), error
    assert error.rule == "item_category_current"


def receiving(item: Item, store: Location, amount: str) -> Write:
    """A receipt of `amount` of the item into the store, as the ledger
    writes every save, count and adjustment."""
    header = {"kind": "receipt", "to_location_id": store.id}
    return lambda session: insert_transaction(
        session, uuid.uuid4(), header, [(item.id, Decimal(amount))]
    )


@pytest.mark.concurrency
@pytest.mark.parametrize(
    ("swapped", "refused"),
    [(False, "item_unit_fixed"), (True, "movement_whole_units")],
)
def test_a_unit_changed_while_stock_moves_waits_then_is_refused(
    own_committed: Committed, swapped: bool, refused: str
) -> None:
    """A save holds its items FOR KEY SHARE (ledger.hold) until it
    commits, and changing an item's unit holds it FOR UPDATE: whichever
    comes second waits, then is refused, the unit change as stock has
    moved, the save as it moves a fraction of an item now counted in each.
    On a database of its own, as it writes the ledger."""
    place = committed_place(own_committed)
    to_each = doing(
        Item,
        place.cable.id,
        lambda s, item: items.save(s, item, item_fields(item, unit="each"), seen=None),
    )
    receipt = receiving(place.cable, place.store, "1.5")
    assert either_first(own_committed, receipt, to_each, swapped=swapped) == refused


@pytest.mark.concurrency
@pytest.mark.parametrize(
    ("swapped", "refused"),
    [(False, "movement_open_package_whole"), (True, "movement_open_package_place")],
)
def test_a_cut_and_a_whole_move_of_one_open_package_go_one_at_a_time(
    own_committed: Committed, swapped: bool, refused: str
) -> None:
    """Each holds the open package FOR NO KEY UPDATE (ledger._hold_forms)
    until it commits: the second waits, then reads it afresh. A move of all
    that was left, second, is refused, as some was cut meanwhile; a cut,
    second, as the package is in the van now (docs/decisions/0020). On a
    database of its own, as it writes the ledger."""
    setup = own_committed.session()
    spools = Spools(setup)
    open_id = spools.opened().id
    setup.commit()
    cable, opened = spools.cable.id, Form(open_package_id=open_id)
    header = {
        "kind": "move",
        "from_location_id": spools.store.id,
        "to_location_id": spools.van.id,
    }

    def moving(amount: int, arrives: Form) -> Write:
        line = Entry(cable, Decimal(amount), opened, arrives)
        return lambda session: insert_transaction(session, uuid.uuid4(), header, [line])

    cut, whole = moving(20, Form()), moving(305, opened)
    assert either_first(own_committed, cut, whole, swapped=swapped) == refused


@pytest.mark.concurrency
def test_an_open_package_made_twice_at_once_is_made_once(
    own_committed: Committed,
) -> None:
    """Neither finds its key, so both insert: the second waits on the
    first's key, then finds the first's (open_packages.make)."""
    setup = own_committed.session()
    spools = Spools(setup)
    setup.commit()
    key, spool, store = uuid.uuid4(), spools.spool.id, spools.store.id
    one, two = own_committed.session(), own_committed.session()
    first = open_packages.make(one, key, spool, store)
    worker = own_committed.blocked(
        two, lambda: open_packages.make(two, key, spool, store).id
    )
    one.commit()
    assert worker.finished().result == first.id
    two.rollback()


@pytest.mark.concurrency
def test_a_receipt_while_its_item_is_retired_waits_then_is_refused(
    own_committed: Committed,
) -> None:
    """Retiring holds the item FOR UPDATE: a receipt of it waits, then sees
    it retired. On a database of its own, in case the receipt is written."""
    place = committed_place(own_committed)
    retiring = doing(Item, place.item.id, lambda s, i: items.retire(s, i, NOW))
    refused = at_once(own_committed, retiring, receiving(place.item, place.store, "1"))
    assert refused == "receipt_retired_item"


@pytest.mark.concurrency
def test_a_batch_sent_twice_at_once_is_written_once(
    own_committed: Committed,
) -> None:
    """Neither finds its key written, so both reach the insert: the second
    waits on the first's key, then writes nothing and finds the first's
    (ledger.record), which the route answers 200 with. On a database of its
    own, as it writes the ledger."""
    place = committed_place(own_committed)
    batch = Batch.model_validate(place.batch())
    one, two = own_committed.session(), own_committed.session()
    first, wrote, *_ = record(one, batch, device_id=None)
    worker = own_committed.blocked(two, lambda: record(two, batch, device_id=None))
    one.commit()
    finished = worker.finished()
    two.rollback()
    assert finished.error is None
    second, wrote_again, *_ = cast(Recorded, finished.result)
    assert (wrote, wrote_again, second.id) == (True, False, first.id)
    checking = own_committed.session()
    keyed = LedgerTransaction.idempotency_key == batch.key
    assert checking.scalars(select(LedgerTransaction.id).where(keyed)).all() == [
        first.id
    ]


@pytest.mark.concurrency
@pytest.mark.parametrize("model", [Location, Item])
def test_a_page_waits_for_a_write_that_refers_to_its_row(
    committed: Committed, model: type[Location | Item]
) -> None:
    """A save holds its places FOR KEY SHARE (ledger._hold_places), as a
    movement's foreign key does its item, until it commits: a page's lock
    waits for it, so retiring a place or fixing an item's unit reads what it
    wrote (inventory-tng-8i4.69)."""
    if model is Location:
        row: Base = Location(name=f"Store {tag()}", kind="store")
    else:
        row = Item(name=f"LiteBeam {tag()}", category_id=catalogue(committed)[0])
    made(committed, row)
    saving = committed.session()
    saving.execute(
        locked(select(model.id).where(model.id == row.id), key_share=True, shared=True)
    )
    page = committed.session()
    worker = committed.blocked(page, lambda: locked_row(page, model, row.id))
    saving.commit()
    assert worker.finished().error is None


@pytest.mark.end_to_end
def test_ending_custody_waits_for_a_save_into_it(
    committed: Committed, admins: Admins, real_client: tuple[TestClient, SentMail]
) -> None:
    """A save holds its places until it commits (ledger._hold_places):
    ending a volunteer's custody waits for one into it, so it reads what the
    save brought (inventory-tng-8i4.78)."""
    client, _ = real_client
    _, _, key = admins
    signed_in_as_ben(committed, client, key)
    volunteer = Volunteer(name=f"V {tag()}")
    made(committed, volunteer)
    custody = Location(
        name=f"Custody {tag()}", kind="custody", volunteer_id=volunteer.id
    )
    made(committed, custody)
    saving = committed.session()
    saving.execute(
        locked(
            select(Location.id).where(Location.id == custody.id),
            key_share=True,
            shared=True,
        )
    )
    worker = committed.blocked(
        None, lambda: client.post(f"{VOLUNTEERS}/{volunteer.id}/custody/end")
    )
    saving.commit()
    answer = cast(Answer, worker.finished().result)
    assert answer.status_code == 303


@pytest.mark.end_to_end
def test_custody_ended_twice_at_once_is_ended_once(
    committed: Committed, admins: Admins, real_client: tuple[TestClient, SentMail]
) -> None:
    """The second request read the custody as current, then waits for the
    first: it finds it ended, rather than ending it again later."""
    client, _ = real_client
    _, _, key = admins
    signed_in_as_ben(committed, client, key)
    volunteer = Volunteer(name=f"V {tag()}")
    made(committed, volunteer)
    custody = Location(
        name=f"Custody {tag()}", kind="custody", volunteer_id=volunteer.id
    )
    made(committed, custody)
    ending = committed.session()
    held = ending.get_one(Location, custody.id)
    locations.retire(ending, held, NOW)
    ending.flush()
    worker = committed.blocked(
        None, lambda: client.post(f"{VOLUNTEERS}/{volunteer.id}/custody/end")
    )
    ending.commit()
    answer = cast(Answer, worker.finished().result)
    assert answer.status_code == 404
    checking = committed.session()
    assert checking.get_one(Location, custody.id).retired_at == NOW


def changes_open(committed: Committed, session_id: int) -> None:
    """A code typed for a change to another administrator, just now: the
    session may invite without one."""
    setup = committed.session()
    setup.get_one(AdminSession, session_id).account_confirmed_at = datetime.now(UTC)
    setup.commit()


def inviting(client: TestClient, key: str, username: str) -> Answer:
    return client.post(ADMINS, data={"once": key, "username": username})


@pytest.mark.end_to_end
def test_a_form_refused_after_its_key_is_spent_can_be_sent_again(
    committed: Committed, admins: Admins, real_client: tuple[TestClient, SentMail]
) -> None:
    """A taken username is found after the key is spent: refused without a
    commit, nothing is kept, so the same form, put right, goes through
    (once.py, inventory-tng-8i4.74)."""
    client, _ = real_client
    changes_open(committed, signed_in_as_ben(committed, client, admins[2]))
    key = str(uuid.uuid4())
    assert inviting(client, key, "ADA").status_code == 400
    assert inviting(client, key, "grace").status_code == 200
    assert inviting(client, key, "grace").status_code == 409


@pytest.mark.concurrency
def test_a_form_sent_twice_at_once_waits_then_makes_nothing(
    committed: Committed, admins: Admins, real_client: tuple[TestClient, SentMail]
) -> None:
    client, _ = real_client
    changes_open(committed, signed_in_as_ben(committed, client, admins[2]))
    key = uuid.uuid4()
    first = committed.session()
    first.add(SentForm(key=key))
    first.flush()
    worker = committed.blocked(None, lambda: inviting(client, str(key), "grace"))
    first.commit()
    assert cast(Answer, worker.finished().result).status_code == 409
    assert (
        committed.session().scalar(select(Admin.id).where(Admin.username == "grace"))
        is None
    )
