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
from sqlalchemy import Engine, delete, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import InstrumentedAttribute, Session
from structlog.testing import capture_logs

from inventory_tng.admins import (
    accounts,
    authenticator_totp,
    guards,
    mail_messages,
    paths,
)
from inventory_tng.admins.paths import ADMINS
from inventory_tng.catalogue import (
    categories,
    items,
    items_routes,
    reading_rules,
    unit_changes,
    unit_recounts,
    units,
)
from inventory_tng.catalogue.paths import ITEMS
from inventory_tng.core import db_locking
from inventory_tng.core.db_base import Base
from inventory_tng.core.db_errors import Gone, RuleBroken
from inventory_tng.core.db_locking import locked, locked_row
from inventory_tng.core.db_sessions import (
    DbSession,
    get_snapshot,
    hold_until_commit,
    release_hold,
)
from inventory_tng.core.mail import Message, Notify
from inventory_tng.core.settings import RateLimits
from inventory_tng.devices import devices, devices_routes
from inventory_tng.devices.paths import DEVICES
from inventory_tng.labels import labels
from inventory_tng.models.admins import Admin, AdminPassword, AdminSession
from inventory_tng.models.catalogue import Category, Item, ItemPackage, ReadingRule
from inventory_tng.models.core import SentForm
from inventory_tng.models.labels import Label
from inventory_tng.models.places import Location
from inventory_tng.models.site_settings import SignInSettings
from inventory_tng.models.stock import (
    LedgerTransaction,
    Movement,
    OpenPackage,
    OverRecorded,
)
from inventory_tng.models.volunteers import Volunteer
from inventory_tng.places import locations
from inventory_tng.scan import catalogue_api
from inventory_tng.scan.batch import BatchRefused, KeyReused, Recorded, record
from inventory_tng.scan.batch_schemas import Batch
from inventory_tng.stock import (
    adjustment_reasons,
    ledger,
    open_packages,
    over_recorded,
)
from inventory_tng.stock.balance import balance
from inventory_tng.stock.ledger import Entry, Form, insert_transaction
from inventory_tng.stock.moments import expected
from inventory_tng.stock.paths import COUNTS, LENGTHS, USED_UP
from inventory_tng.volunteers import volunteers, volunteers_routes
from inventory_tng.volunteers.paths import VOLUNTEERS
from inventory_tng.web.single_use_forms import FIELD

from helpers.concurrency import (
    Committed,
    Worker,
    delete_made,
    isolation_seen,
    made,
    newest_ids,
)
from helpers.devices import new_device
from helpers.mail import NO_MAIL, SentMail
from helpers.over_recorded import correct_check
from helpers.rows import item_fields, shown, tag
from helpers.sign_in import (
    PASSWORD,
    TOTP_KEYS,
    code,
    new_admin,
    new_admin_id,
    reset_link,
    sign_in_with,
)
from helpers.stock import (
    Place,
    Spools,
    committed_place,
    converted_reel,
    count_form,
    held_by_form,
    length,
    raise_check,
    recount,
    report,
    sealed_line,
)

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
    now = datetime.now(UTC)
    return accounts.sign_in(
        session,
        "ben",
        PASSWORD,
        code(key, now),
        address="192.0.2.80",
        user_agent=None,
        now=now,
        keys=TOTP_KEYS,
        limits=RateLimits(),
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
) -> guards.SignedIn:
    """What a request knew of them before the gate, as signed_in reads it."""
    return guards.SignedIn(
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
    """Every sensitive form passes this gate (guards.confirmed): changing
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
        limits=RateLimits(),
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
            recovering,
            token,
            token,
            "ben",
            "192.0.2.82",
            datetime.now(UTC),
            NO_MAIL,
            RateLimits(),
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
        session, TOTP_KEYS, admin_id, "Phone", authenticator_totp.new_totp_key(), 0
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
            limits=RateLimits(),
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
    worker = committed.blocked(form, lambda: guards._hold(form, before).uses_totp)
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
    monkeypatch.setattr(guards, "_now", lambda: clock[0])
    holding = gate_held(committed, ada)
    form = committed.session()
    before = signed(form, ben, session_id, uses_totp=True)
    worker = committed.blocked(form, lambda: guards._hold(form, before))
    clock[0] = before.row.expires_at + timedelta(seconds=1)
    holding.commit()
    assert isinstance(worker.finished().error, guards.Redirect)


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
        _: guards.ConfirmedAdmin, session: DbSession, tasks: BackgroundTasks
    ) -> Response:
        session.commit()
        tasks.add_task(commit_more, session)
        return Response()

    @app.post("/admin/commits-twice")
    def twice(_: guards.ConfirmedAdmin, session: DbSession) -> Response:
        session.commit()
        commit_more(session)
        return Response()

    assert admin_client.post("/admin/commits-once").status_code == 200
    # The app answers its own errors: the second commit raised, so 500.
    assert admin_client.post("/admin/commits-twice").status_code == 500
    # The hold ended with that request: later ones commit as they like.
    assert admin_client.post(paths.SIGN_OUT).status_code == 303


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
    assert sign_in_with(client, "ben", key=key).status_code == 303
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
        None, lambda: client.post(f"{paths.ACCOUNT}/password", data=form)
    )


PASSWORD_FORM = {"password": NEW_PASSWORD, "password_again": NEW_PASSWORD}


@pytest.mark.end_to_end
def test_a_sensitive_form_posted_during_a_reset_is_sent_to_sign_in(
    committed: Committed, admins: Admins, real_client: tuple[TestClient, SentMail]
) -> None:
    """Through the whole app: the gate (guards.confirmed) waits for the
    reset, then turns the form away, and nothing it asked for is done."""
    client, _ = real_client
    ada, ben, key = admins
    signed_in_as_ben(committed, client, key)
    reset, _ = resetting(committed, ada, ben)
    worker = posting(committed, client, PASSWORD_FORM)
    reset.commit()
    answer = cast(Answer, worker.finished().result)
    assert answer.headers["location"] == paths.SIGN_IN
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
    form = PASSWORD_FORM | {guards.CONFIRM_FIELD: recovery}
    worker = posting(committed, client, form)
    accounts.end_sessions(ending, ben, datetime.now(UTC))
    ending.commit()
    answer = cast(Answer, worker.finished().result)
    assert answer.headers["location"] == paths.SIGN_IN
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
    """As in production (db_sessions.get_session), not the test's stand-in:
    work after the reply that commits on the request's session is not taken for
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
        _: guards.ConfirmedAdmin, session: DbSession, tasks: BackgroundTasks
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
    session closes as the route returns (db_sessions.DbSession), so the mail
    goes out with the lock already let go, not held while the provider
    answers."""
    ada, _, key = admins
    sent = LockProbingMail(committed, ada)
    client = own_client(sent)
    app = cast(FastAPI, client.app)

    @app.post("/admin/refused-with-mail")
    def refused(signed: guards.ConfirmedAdmin, notify: Notify) -> Response:
        notify.tell(
            signed.admin.email, signed.admin.id, mail_messages.recovery_code_used(9)
        )
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
        assert guards.CONFIRM_FIELD in answer.text
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


def second_failed(
    committed: Committed, first: Write, second: Write
) -> BaseException | None:
    """The first written, and committed while the second waits: what the
    second raised, if anything."""
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
    return error


def at_once(committed: Committed, first: Write, second: Write) -> str | None:
    """second_failed: the rule the second was refused by, if any."""
    error = second_failed(committed, first, second)
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


@pytest.mark.concurrency
@pytest.mark.parametrize(
    ("swapped", "refused"),
    [(False, items.ALIAS_IS_NAME), (True, items.NAME_IS_ALIAS)],
)
def test_a_name_and_an_alias_the_same_not_both(
    committed: Committed, swapped: bool, refused: str
) -> None:
    """An item renamed to what another is given as an alias at once: the
    second waits for the catalogue lock, then is refused."""
    name = tag()
    category = Category(name=f"Radios {name}")
    made(committed, category)
    one, other = (Item(name=f"{n} {name}", category_id=category.id) for n in "AB")
    made(committed, one, other)
    wanted = f"LiteBeam {name}"

    def renaming(session: Session, item: Item) -> None:
        fields = items.Fields.new(name=wanted, category_id=category.id)
        items.save(session, item, fields, seen=None)

    def aliasing(session: Session, item: Item) -> None:
        items.add_alias(session, item, wanted)

    first, second = doing(Item, one.id, renaming), doing(Item, other.id, aliasing)
    assert either_first(committed, first, second, swapped=swapped) == refused


@pytest.mark.concurrency
@pytest.mark.parametrize("swapped", [False, True])
def test_a_name_saved_as_a_rule_changes_never_reads_as_another(
    committed: Committed, swapped: bool
) -> None:
    """A rule reading g as Gbps, and an item named with a g that would then
    read as another item's name, at once: the second waits for the
    catalogue lock, then reads what the first committed. The rule is
    refused (READ_ALIKE), or the name is, by its unit key: never both
    taken, two names reading alike."""
    name = tag()
    category = Category(name=f"Parts {name}")
    made(committed, category)
    made(committed, Item(name=f"Box {name} 10 Gbps", category_id=category.id))

    def ruling(session: Session) -> None:
        reading_rules.decide(session, {"g": units.Rule("Gbit/s")}, None)

    def adding(session: Session) -> None:
        fields = items.Fields.new(name=f"Box {name} 10g", category_id=category.id)
        items.add(session, fields)

    first, second = (adding, ruling) if swapped else (ruling, adding)
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
    if swapped:
        assert isinstance(error, RuleBroken), error
        assert error.rule == items.READ_ALIKE
    else:
        assert isinstance(error, IntegrityError), error
        assert "uq_item_unit_key" in str(error.orig)


def reading_rule(committed: Committed) -> int:
    """A reading rule, committed, by a token no other test uses."""
    rule = ReadingRule(token=tag(), reading=None)
    made(committed, rule)
    return rule.id


def deleting_rule(rule_id: int) -> Write:
    return doing(
        ReadingRule, rule_id, lambda s, r: reading_rules.delete(s, r, seen=None)
    )


@pytest.mark.concurrency
@pytest.mark.parametrize("second", ["save", "delete"])
def test_a_reading_rule_deleted_meanwhile_is_gone_for_the_next_change(
    committed: Committed, admins: Admins, second: str
) -> None:
    """A rule deleted while another administrator saves or deletes it:
    the second waits for the catalogue lock, then finds no rule, and is
    answered not found (Gone), not a server error (inventory-tng-oxa0)."""
    ada, _, _ = admins
    rule_id = reading_rule(committed)
    saving = doing(
        ReadingRule,
        rule_id,
        lambda s, r: reading_rules.change(s, r, units.Rule("Gbit/s"), ada, seen=None),
    )
    then = saving if second == "save" else deleting_rule(rule_id)
    with capture_logs() as logs:
        error = second_failed(committed, deleting_rule(rule_id), then)
    assert isinstance(error, Gone), error
    assert [
        {key: line[key] for key in ("table", "row_id")}
        for line in logs
        if line["event"] == "change refused: deleted meanwhile"
    ] == [{"table": "reading_rule", "row_id": rule_id}]
    assert committed.session().get(ReadingRule, rule_id) is None


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
    ("swapped", "refused"), [(False, "item_unit_labelled"), (True, None)]
)
def test_a_unit_changed_while_a_label_is_made_waits_then_is_refused(
    committed: Committed, swapped: bool, refused: str | None
) -> None:
    """Both take the catalogue lock and hold the item FOR UPDATE
    (labels.make_for_item, items.save): a unit change second waits, then
    reads the label and is refused; a label second is made for the new
    unit (Ali, 2026-11-23, inventory-tng-rpud)."""
    place = committed_place(committed)
    reel = ItemPackage(item_id=place.cable.id, name="Reel", size=Decimal(305))
    made(committed, reel)
    to_each = doing(
        Item,
        place.cable.id,
        lambda s, item: items.save(s, item, item_fields(item, unit="each"), seen=None),
    )

    def labelling(session: Session) -> None:
        assert labels.make_for_item(session, place.cable.id, reel.id) is not None

    assert either_first(committed, labelling, to_each, swapped=swapped) == refused


@pytest.mark.concurrency
@pytest.mark.parametrize(
    ("swapped", "refused"), [(False, None), (True, "movement_unit")]
)
def test_a_save_while_a_unit_is_converted_is_converted_or_refused(
    own_committed: Committed, swapped: bool, refused: str | None
) -> None:
    """A receipt of 0.5 m shown in m holds the cable FOR KEY SHARE, and a
    change to ft that converts holds it FOR UPDATE (Ali, 2026-11-24,
    inventory-tng-y50b): the change second waits, then reads it and
    converts it with the rest; the receipt second waits, then is refused
    as shown in m. Never written in m and read as ft. Each reads the cable
    as its change leaves it, before at_once rolls the second back."""
    place = committed_place(own_committed)
    setup = own_committed.session()
    receiving(place.cable, place.store, "1")(setup)
    setup.commit()
    seen: list[tuple[str, Decimal | None]] = []

    def read(session: Session) -> None:
        total = session.scalar(
            select(balance.c.quantity).where(balance.c.item_id == place.cable.id)
        )
        seen.append((session.get_one(Item, place.cable.id).unit, total))

    def to_ft(session: Session) -> None:
        cable = session.get_one(Item, place.cable.id)
        fields = item_fields(cable, unit="ft")
        items.save(session, cable, fields, seen=None, recorded_in="m")
        read(session)

    header = {"kind": "receipt", "to_location_id": place.store.id}
    shown_in_m = Entry(place.cable.id, Decimal("0.5"), unit="m")

    def receipt(session: Session) -> None:
        insert_transaction(session, uuid.uuid4(), header, [shown_in_m])
        read(session)

    assert either_first(own_committed, receipt, to_ft, swapped=swapped) == refused
    # 1.5 m in ft is 4.921 (4.92125...); 1 m alone, 3.281 ft.
    converted = ("ft", Decimal("3.281") if swapped else Decimal("4.921"))
    assert seen == ([converted] if swapped else [("m", Decimal("1.500")), converted])


def _recounting(
    place: Place,
    seen: str,
    admin_id: int,
    unit: str = "ft",
    sizes: dict[int, unit_recounts.Sized] | None = None,
) -> Write:
    """A guided recount of the place's cable into `unit`, its packages
    given `sizes`, from a page that showed `seen`, as its route sends it
    (catalogue/unit_recounts.py)."""

    def recounting(session: Session) -> None:
        cable = session.get_one(Item, place.cable.id)
        recount(session, cable, unit, seen=seen, sizes=sizes, admin_id=admin_id)

    return recounting


def _shown_for_recount(committed: Committed) -> tuple[Place, str, int]:
    """A cable, 1 m of it in the store, and what its recount's page shows,
    and an administrator to recount it, all committed."""
    place = committed_place(committed)
    setup = committed.session()
    receiving(place.cable, place.store, "1")(setup)
    admin_id = new_admin_id(setup)
    setup.commit()
    seen = unit_recounts.planned(setup, setup.get_one(Item, place.cable.id)).seen
    return place, seen, admin_id


@pytest.mark.concurrency
@pytest.mark.parametrize(
    ("swapped", "refused"),
    [(False, "movement_unit"), (True, "changed_since_shown")],
)
def test_a_save_while_a_unit_is_recounted_waits_then_is_refused(
    own_committed: Committed, swapped: bool, refused: str
) -> None:
    """A receipt shown in m holds the store, and the recount holds it FOR
    UPDATE before the cable (Ali, 2026-11-24, inventory-tng-qety): the
    receipt second waits, then is refused as shown in m; the recount
    second waits, then is refused as its page showed the store without the
    receipt. Never written off unseen, nor written in m once in ft."""
    place, seen, admin_id = _shown_for_recount(own_committed)
    header = {"kind": "receipt", "to_location_id": place.store.id}
    shown_in_m = Entry(place.cable.id, Decimal("0.5"), unit="m")

    def receipt(session: Session) -> None:
        insert_transaction(session, uuid.uuid4(), header, [shown_in_m])

    recounting = _recounting(place, seen, admin_id)
    assert either_first(own_committed, recounting, receipt, swapped=swapped) == refused


@pytest.mark.concurrency
@pytest.mark.parametrize(
    ("swapped", "refused", "raised"),
    [(False, None, 0), (True, "item_unit_checks_waiting", 1)],
)
def test_a_check_raised_while_a_unit_is_recounted(
    own_committed: Committed, swapped: bool, refused: str | None, raised: int
) -> None:
    """A batch sent again raising a check of the receipt before the
    recount holds the cable FOR KEY SHARE, which the recount's FOR UPDATE
    waits for: raised second, it waits, then reads the recount's mark and
    raises none, as the recount wrote the receipt off; raised first, the
    recount waits, then is refused as a check waits. Never a check left
    to write back what the recount wrote off."""
    place, seen, admin_id = _shown_for_recount(own_committed)
    reader = own_committed.session()
    movement_id = reader.scalars(
        select(Movement.id).where(Movement.item_id == place.cable.id)
    ).one()

    def raising(session: Session) -> None:
        over_recorded.raise_checks(session, {movement_id: Decimal("0.5")})

    recounting = _recounting(place, seen, admin_id)
    assert either_first(own_committed, recounting, raising, swapped=swapped) == refused
    reader.rollback()
    assert len(reader.scalars(select(OverRecorded)).all()) == raised


def _shown_converted(committed: Committed) -> tuple[Place, int, Write]:
    """A cable converted to ft, no stock moved, its Reel in ft
    (converted_reel), all committed; its Reel's id, and its recount into
    m, giving the Reel 300 m, from a page shown now."""
    setup = committed.session()
    place, _, reel = converted_reel(setup)
    admin_id = new_admin_id(setup)
    setup.commit()
    seen = unit_recounts.planned(setup, setup.get_one(Item, place.cable.id)).seen
    sizes = {reel.id: unit_recounts.Sized(Decimal(300), None)}
    return place, reel.id, _recounting(place, seen, admin_id, "m", sizes)


@pytest.mark.concurrency
@pytest.mark.parametrize(
    ("swapped", "refused"), [(False, None), (True, "changed_since_shown")]
)
def test_a_label_made_while_a_converted_item_is_recounted(
    own_committed: Committed, swapped: bool, refused: str | None
) -> None:
    """Making a label of the Reel locks the cable, which the recount holds
    FOR UPDATE (Ali, 2026-11-25, inventory-tng-l26i). Made second, it
    waits, then is made once the Reel is resized. Made first, the recount
    waits, then is refused, as its page did not say it would revoke it.
    Never revoked unsaid."""
    place, reel_id, recounting = _shown_converted(own_committed)

    def labelling(session: Session) -> None:
        assert labels.make_for_item(session, place.cable.id, reel_id) is not None

    assert either_first(own_committed, recounting, labelling, swapped=swapped) == (
        refused
    )
    # Only the first is committed (at_once).
    check = own_committed.session()
    reel = check.get_one(ItemPackage, reel_id)
    assert (reel.size, reel.unit) == ((1000, "ft") if swapped else (300, "m"))
    revoked = select(Label.revoked_at).where(Label.package_id == reel_id)
    assert check.scalars(revoked).all() == ([None] if swapped else [])


@pytest.mark.concurrency
@pytest.mark.parametrize(
    ("swapped", "refused"),
    [(False, "package_unit_changed"), (True, "changed_since_shown")],
)
def test_a_package_added_while_a_converted_item_is_recounted(
    own_committed: Committed, swapped: bool, refused: str
) -> None:
    """Adding a package takes the catalogue lock, as the recount does.
    Added second, from a form shown in ft, it is refused once the cable is
    in m. Added first, the recount waits, then is refused, as its page
    asked no size of it. Never left in ft unasked."""
    place, _, recounting = _shown_converted(own_committed)

    def adding(session: Session) -> None:
        cable = session.get_one(Item, place.cable.id)
        items.add_package(session, cable, "Box", Decimal(500), shown_unit="ft")

    assert either_first(own_committed, recounting, adding, swapped=swapped) == refused
    cable = own_committed.session().get_one(Item, place.cable.id)
    assert cable.unit == ("ft" if swapped else "m")


@pytest.mark.concurrency
@pytest.mark.parametrize(
    ("swapped", "refused"), [(False, None), (True, "changed_since_shown")]
)
def test_a_retired_package_brought_back_while_a_converted_item_is_recounted(
    own_committed: Committed, swapped: bool, refused: str | None
) -> None:
    """Bringing back a retired Box of 1000 ft takes the catalogue lock, as
    the recount, which resizes it too, does (Ali, 2026-11-25,
    inventory-tng-l26i). Second, it waits, then comes back with the new
    size. First, the recount waits, then is refused, as its page showed
    the Box retired. Never brought back in ft once the cable is in m."""
    setup = own_committed.session()
    place, _, reel = converted_reel(setup)
    box = items.add_package(setup, place.cable, "Box", Decimal(1000), shown_unit="ft")
    items.retire_package(setup, box, NOW)
    admin_id = new_admin_id(setup)
    setup.commit()
    seen = unit_recounts.planned(setup, setup.get_one(Item, place.cable.id)).seen
    sizes = {
        reel.id: unit_recounts.Sized(Decimal(300), None),
        box.id: unit_recounts.Sized(Decimal("304.8"), None),
    }
    recounting = _recounting(place, seen, admin_id, "m", sizes)
    restoring = doing(ItemPackage, box.id, items.restore_package)
    assert either_first(own_committed, recounting, restoring, swapped=swapped) == (
        refused
    )
    # Only the first is committed (at_once).
    check = own_committed.session().get_one(ItemPackage, box.id)
    assert (check.size, check.unit, check.retired_at is None) == (
        (1000, "ft", True) if swapped else (Decimal("304.8"), "m", False)
    )


@pytest.mark.concurrency
def test_a_recount_while_a_place_first_receives_the_item_is_refused(
    own_committed: Committed,
) -> None:
    """The van never had the cable, so the recount does not hold it; a
    receipt there holds the cable, which the recount waits for, then finds
    the van among its places, not held first: refused before it writes
    anything, never holding the van after the cable."""
    place, seen, admin_id = _shown_for_recount(own_committed)
    with capture_logs() as logs:
        refused = at_once(
            own_committed,
            receiving(place.cable, place.van, "1"),
            _recounting(place, seen, admin_id),
        )
    assert refused == "changed_since_shown"
    assert [
        line["reason"] for line in logs if line["event"] == "unit recount refused"
    ] == ["places_added"]


@pytest.mark.concurrency
def test_a_length_reported_while_a_unit_is_converted_is_converted(
    own_committed: Committed,
) -> None:
    """A batch reporting 300 m on an open spool, none reported before,
    holds it FOR NO KEY UPDATE and reads the cable's unit, m, without a
    lock. A change to ft holds every open package of the cable not closed,
    so waits for it, then converts the report with the rest: 984.252 ft,
    never 300 kept in m on a cable in ft (Ali, 2026-11-24,
    inventory-tng-y50b)."""
    setup = own_committed.session()
    spools = Spools(setup)
    made = spools.opened()
    setup.commit()
    said = Batch.model_validate(report(spools, length(made, "300")))
    one, two = own_committed.session(), own_committed.session()
    record(one, said, device_id=None)

    def to_ft() -> None:
        cable = two.get_one(Item, spools.cable.id)
        items.save(
            two, cable, item_fields(cable, unit="ft"), seen=None, recorded_in="m"
        )
        two.flush()

    worker = own_committed.blocked(two, to_ft)
    one.commit()
    assert worker.finished().error is None
    two.commit()
    check = own_committed.session()
    row = check.get_one(OpenPackage, made.id)
    assert (row.length_reported, row.length_reported_unit) == (Decimal("984.252"), "ft")


@pytest.mark.concurrency
def test_a_check_raised_while_a_unit_is_converted_waits_then_is_corrected(
    own_committed: Committed,
) -> None:
    """A change to ft holds the cable FOR UPDATE, then looks for checks
    waiting. A check of 3 m raised meanwhile holds the cable FOR KEY
    SHARE (over_recorded._since_recounts), so waits for the change, then
    is raised in m, the line's unit. Correct reads the cable's unit now
    (over_recorded._said_now) and writes 3 m as 9.843 ft, never refused
    as shown in m (inventory-tng-4fjq)."""
    setup = own_committed.session()
    place = Place(setup)
    cable = place.cable
    admin_id = new_admin_id(setup)
    header = {"kind": "receipt", "to_location_id": place.store.id}
    insert_transaction(setup, uuid.uuid4(), header, [Entry(cable.id, Decimal(10))])
    move = {
        "kind": "move",
        "from_location_id": place.store.id,
        "to_location_id": place.van.id,
    }
    moved = insert_transaction(setup, uuid.uuid4(), move, [Entry(cable.id, Decimal(5))])
    setup.commit()
    changing, raising, correcting = (own_committed.session() for _ in range(3))
    held = changing.get_one(Item, cable.id)
    items.save(changing, held, item_fields(held, unit="ft"), seen=None, recorded_in="m")
    changing.flush()
    worker = own_committed.blocked(
        raising, lambda: raise_check(raising, moved, Decimal(3))
    )
    changing.commit()
    assert worker.finished().error is None
    check = cast(OverRecorded, worker.result)
    raising.commit()
    assert check.unit == "m"
    seen = over_recorded.seen(check)
    assert correct_check(correcting, check, seen, admin_id) == "corrected"
    correcting.commit()
    lines = own_committed.session().execute(
        select(Movement.quantity, Movement.unit)
        .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
        .where(LedgerTransaction.kind == "adjustment")
    )
    assert lines.all() == [(Decimal("9.843"), "ft")] * 2


@pytest.mark.concurrency
@pytest.mark.parametrize("package", [False, True])
def test_a_unit_or_size_changed_while_its_label_is_revoked_waits_then_is_made(
    committed: Committed, package: bool
) -> None:
    """Revoking takes the catalogue lock, as a unit or size change does
    (labels.revoke, items.save, items.save_package): a change second waits,
    then reads the label as revoked and is made (Ali, 2026-11-23,
    inventory-tng-37dj). Second the other way, it would be refused before
    the revoke began, which waits for nothing."""
    place = committed_place(committed)
    reel = ItemPackage(item_id=place.cable.id, name="Reel", size=Decimal(305))
    made(committed, reel)
    setup = committed.session()
    label = labels.make_for_item(setup, place.cable.id, reel.id)
    assert label is not None
    setup.commit()
    label_id = label.id

    def revoking(session: Session) -> None:
        assert labels.revoke(session, label_id, NOW)

    change = (
        doing(
            ItemPackage,
            reel.id,
            lambda s, p: items.save_package(s, p, p.name, Decimal(300), seen=None),
        )
        if package
        else doing(
            Item,
            place.cable.id,
            lambda s, item: items.save(
                s, item, item_fields(item, unit="ft"), seen=None
            ),
        )
    )
    assert at_once(committed, revoking, change) is None


@pytest.mark.concurrency
@pytest.mark.parametrize(
    ("swapped", "refused"),
    [(False, items.PACKAGE_WHOLE), (True, items.PACKAGES_WHOLE)],
)
def test_a_change_to_each_and_a_fraction_on_its_package_go_one_at_a_time(
    committed: Committed, swapped: bool, refused: str
) -> None:
    """Both take the catalogue lock, and each reads what the other writes
    afresh under it: a too-short length with a fraction, second, is
    refused as the item is counted each now; the change to each, second,
    as its package has a fraction (Ali, 2026-11-23, inventory-tng-fa5i)."""
    place = committed_place(committed)
    reel = ItemPackage(item_id=place.cable.id, name="Reel", size=Decimal(305))
    made(committed, reel)
    to_each = doing(
        Item,
        place.cable.id,
        lambda s, item: items.save(s, item, item_fields(item, unit="each"), seen=None),
    )
    short = doing(
        ItemPackage,
        reel.id,
        lambda s, package: items.package_settings(
            s, package, Decimal("1.5"), None, shown_unit=None
        ),
    )
    assert either_first(committed, to_each, short, swapped=swapped) == refused


@pytest.mark.concurrency
@pytest.mark.parametrize(
    ("swapped", "refused"), [(False, unit_changes.UNIT_CHANGED), (True, None)]
)
@pytest.mark.parametrize("form", ["add_package", "package_settings"])
def test_a_package_amount_and_a_unit_switch_go_one_at_a_time(
    committed: Committed, swapped: bool, refused: str | None, form: str
) -> None:
    """Both take the catalogue lock, and the amount's save reads the unit
    afresh under it: second, typed in m, it is refused, as the cable is in
    ft now; first, it is kept, and the switch takes it into ft as typed,
    nothing fixing the unit (unit_changes.follow; inventory-tng-74f3)."""
    place = committed_place(committed)
    reel = ItemPackage(item_id=place.cable.id, name="Reel", size=Decimal(305))
    made(committed, reel)
    to_ft = doing(
        Item,
        place.cable.id,
        lambda s, item: items.save(s, item, item_fields(item, unit="ft"), seen=None),
    )
    amount = (
        doing(
            Item,
            place.cable.id,
            lambda s, item: items.add_package(
                s, item, "Drum", Decimal(300), shown_unit="m"
            ),
        )
        if form == "add_package"
        else doing(
            ItemPackage,
            reel.id,
            lambda s, package: items.package_settings(
                s, package, Decimal(5), None, shown_unit="m"
            ),
        )
    )
    assert either_first(committed, to_ft, amount, swapped=swapped) == refused


@pytest.mark.concurrency
@pytest.mark.parametrize(
    ("swapped", "refused"),
    [(False, "movement_open_package_whole"), (True, "movement_open_package_place")],
)
def test_a_cut_and_a_whole_move_of_one_open_package_go_one_at_a_time(
    own_committed: Committed, swapped: bool, refused: str
) -> None:
    """Each holds the open package FOR NO KEY UPDATE (ledger.hold_forms)
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
@pytest.mark.parametrize(
    ("swapped", "refused"), [(False, None), (True, "movement_open_package_closed")]
)
def test_a_cut_and_following_turned_off_go_one_at_a_time(
    own_committed: Committed, swapped: bool, refused: str | None
) -> None:
    """Turning following off takes the open package's place FOR UPDATE
    first (open_packages.unfollowing), which a save holds FOR KEY SHARE: a
    cut under way is counted before the close, and what it left is made
    loose; a cut second waits, then is refused, as its open package is
    closed (Ali, 2026-11-23, inventory-tng-j89e). On a database of its
    own, as it writes the ledger."""
    setup = own_committed.session()
    spools = Spools(setup)
    admin_id = new_admin_id(setup)
    open_id = spools.opened().id
    setup.commit()
    cable, spool, store = spools.cable.id, spools.spool.id, spools.store.id
    header = {
        "kind": "move",
        "from_location_id": store,
        "to_location_id": spools.van.id,
    }
    line = Entry(cable, Decimal(20), Form(open_package_id=open_id), Form())

    def cutting(session: Session) -> None:
        insert_transaction(session, uuid.uuid4(), header, [line])

    def turning_off(session: Session) -> None:
        package = session.get_one(ItemPackage, spool)
        _, closed = open_packages.unfollowing(
            session,
            admin_id,
            datetime.now(UTC),
            lambda: items.track_package(session, package, False),
            package_ids=[spool],
        )
        assert closed == 1
        # All of it, or what the cut first left.
        loose = Decimal(305) if swapped else Decimal(285)
        assert held_by_form(session, store, cable)[(cable, Form())] == loose

    assert either_first(own_committed, cutting, turning_off, swapped=swapped) == refused


@pytest.mark.concurrency
def test_two_takes_of_the_last_of_a_retired_package_go_one_at_a_time(
    own_committed: Committed,
) -> None:
    """Each holds the retired package it takes FOR NO KEY UPDATE
    (ledger.hold_forms) until it commits: the second waits, then finds none
    left sealed, and is refused (docs/decisions/0003, Amendment,
    2026-11-19), rather than both reading some left. On a database of its
    own, as it writes the ledger."""
    setup = own_committed.session()
    spools = Spools(setup)
    spools.spool.retired_at = NOW
    setup.commit()
    header = {"kind": "install", "from_location_id": spools.store.id}
    line = Entry(spools.cable.id, Decimal(610), spools.sealed)
    installing: Write = lambda session: insert_transaction(
        session, uuid.uuid4(), header, [line]
    )
    refused = at_once(own_committed, installing, installing)
    assert refused == "movement_retired_package_none_left"


@pytest.mark.concurrency
def test_a_package_resized_while_it_moves_waits_then_is_refused(
    own_committed: Committed,
) -> None:
    """A save holds its packages FOR KEY SHARE (ledger.hold_forms) until it
    commits, and resizing one holds it FOR UPDATE: the resize waits, then
    is refused, as some of it has moved in the old size. On a database of
    its own, as it writes the ledger."""
    setup = own_committed.session()
    place = Place(setup)
    box = ItemPackage(item_id=place.cable.id, name="Box", size=Decimal(50))
    setup.add(box)
    setup.commit()
    spool = box.id
    header = {"kind": "receipt", "to_location_id": place.store.id}
    sealed = Form(package_id=spool)
    line = Entry(place.cable.id, Decimal(50), Form(), sealed)
    receipt: Write = lambda session: insert_transaction(
        session, uuid.uuid4(), header, [line]
    )
    resize = doing(
        ItemPackage,
        spool,
        lambda s, p: items.save_package(s, p, p.name, Decimal(25), seen=None),
    )
    assert at_once(own_committed, receipt, resize) == "package_size_fixed"


@pytest.mark.concurrency
def test_a_batch_of_a_package_resized_meanwhile_waits_then_is_refused(
    own_committed: Committed,
) -> None:
    """The outbox's race (inventory-tng-5f7): a batch built from a catalogue
    before a package nobody had used was resized, saved as the resize
    commits. It waits for the package's lock (ledger.hold_forms), then
    finds the size it was sent with gone and is refused, rather than saved
    as twice as many boxes. On a database of its own, as it may write the
    ledger."""
    setup = own_committed.session()
    place = Place(setup)
    box = ItemPackage(item_id=place.cable.id, name="Box", size=Decimal(50))
    setup.add(box)
    setup.commit()
    line = {
        "item_id": place.cable.id,
        "quantity": "50",
        "unit": place.cable.unit,
        "package_id": box.id,
        "package_size": "50",
    }
    batch = Batch.model_validate(
        place.batch(
            kind="receipt",
            from_location_id=None,
            to_location_id=place.store.id,
            lines=[line],
        )
    )
    one, two = own_committed.session(), own_committed.session()
    doing(
        ItemPackage,
        box.id,
        lambda s, p: items.save_package(s, p, p.name, Decimal(25), seen=None),
    )(one)
    one.flush()
    worker = own_committed.blocked(two, lambda: record(two, batch, device_id=None))
    one.commit()
    error = worker.finished().error
    two.rollback()
    assert isinstance(error, BatchRefused), error
    assert error.problems == [(("lines", 0, "package_size"), "package_resized")]


@pytest.mark.concurrency
def test_a_batch_of_an_item_switched_to_ft_meanwhile_waits_then_is_refused(
    own_committed: Committed,
) -> None:
    """The outbox's race of a unit (inventory-tng-57u8): a receipt of 50 m
    of a cable nobody had moved, saved as its switch to ft commits. It waits
    for the item's lock (ledger.hold), then finds it shown in m and is
    refused, rather than saved as 50 ft. On a database of its own, as it
    may write the ledger."""
    setup = own_committed.session()
    place = Place(setup)
    setup.commit()
    line = {"item_id": place.cable.id, "quantity": "50", "unit": "m"}
    batch = Batch.model_validate(
        place.batch(
            kind="receipt",
            from_location_id=None,
            to_location_id=place.store.id,
            lines=[line],
        )
    )
    one, two = own_committed.session(), own_committed.session()
    doing(
        Item,
        place.cable.id,
        lambda s, item: items.save(s, item, item_fields(item, unit="ft"), seen=None),
    )(one)
    one.flush()
    worker = own_committed.blocked(two, lambda: record(two, batch, device_id=None))
    one.commit()
    error = worker.finished().error
    two.rollback()
    assert isinstance(error, BatchRefused), error
    assert error.problems == [(("lines", 0, "unit"), "unit_changed")]


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
def test_the_used_up_reason_made_twice_at_once_is_made_once(
    own_committed: Committed,
) -> None:
    """Renamed away, two closes at once each make it: the second waits on
    the first's name, then finds it (adjustment_reasons.seeded)."""
    setup = own_committed.session()
    seeded = adjustment_reasons.seeded(setup, adjustment_reasons.USED_UP)
    seeded.name = "Spent"
    setup.commit()
    one, two = own_committed.session(), own_committed.session()
    first = adjustment_reasons.seeded(one, adjustment_reasons.USED_UP)
    one.flush()
    worker = own_committed.blocked(
        two, lambda: adjustment_reasons.seeded(two, adjustment_reasons.USED_UP).id
    )
    one.commit()
    assert worker.finished().result == first.id
    two.rollback()


@pytest.mark.concurrency
def test_a_whole_move_sent_twice_at_once_is_written_once(
    own_committed: Committed,
) -> None:
    """The second waits on the open package's lock, then finds the first
    written, rather than refusing it as somewhere else now (batch.record)."""
    setup = own_committed.session()
    spools = Spools(setup)
    made = spools.opened()
    setup.commit()
    line = {
        "item_id": spools.cable.id,
        "quantity": "305",
        "unit": spools.cable.unit,
        "open_package_key": str(made.key),
        "whole": True,
    }
    batch = Batch.model_validate(spools.place.batch(lines=[line]))
    one, two = own_committed.session(), own_committed.session()
    first, wrote, *_ = record(one, batch, device_id=None)
    worker = own_committed.blocked(two, lambda: record(two, batch, device_id=None))
    one.commit()
    finished = worker.finished()
    two.rollback()
    assert finished.error is None, finished.error
    second, wrote_again, *_ = cast(Recorded, finished.result)
    assert (wrote, wrote_again, second.id) == (True, False, first.id)


@pytest.mark.concurrency
def test_the_last_of_a_retired_package_sent_twice_at_once_is_written_once(
    own_committed: Committed,
) -> None:
    """The second waits on the retired package's lock, then finds the first
    written, rather than refusing it as none left (batch.record)."""
    setup = own_committed.session()
    spools = Spools(setup)
    spools.install(spools.store.id)
    spools.spool.retired_at = NOW
    setup.commit()
    batch = Batch.model_validate(
        spools.place.batch(
            kind="install", to_location_id=None, lines=[sealed_line(spools, "305")]
        )
    )
    one, two = own_committed.session(), own_committed.session()
    first, wrote, *_ = record(one, batch, device_id=None)
    worker = own_committed.blocked(two, lambda: record(two, batch, device_id=None))
    one.commit()
    finished = worker.finished()
    two.rollback()
    assert finished.error is None, finished.error
    second, wrote_again, *_ = cast(Recorded, finished.result)
    assert (wrote, wrote_again, second.id) == (True, False, first.id)


@pytest.mark.concurrency
@pytest.mark.parametrize("same", [True, False])
def test_a_report_sent_twice_at_once_is_compared_with_the_first(
    own_committed: Committed, same: bool
) -> None:
    """The second waits, on the open package's lock for the same report or
    on the first's key for another, then finds the first written and
    compares the reports each sent (inventory-tng-ckl): the same are
    replayed, others refused as the key reused."""
    setup = own_committed.session()
    spools = Spools(setup)
    one, two = spools.opened(), spools.opened()
    setup.commit()
    batch = Batch.model_validate(report(spools, used_up=[one.key]))
    other = batch if same else batch.model_copy(update={"used_up": [two.key]})
    first_session, second_session = own_committed.session(), own_committed.session()
    first, *_ = record(first_session, batch, device_id=None)
    worker = own_committed.blocked(
        second_session, lambda: record(second_session, other, device_id=None)
    )
    first_session.commit()
    finished = worker.finished()
    second_session.rollback()
    if same:
        assert finished.error is None, finished.error
        second, wrote_again, *_ = cast(Recorded, finished.result)
        assert (wrote_again, second.id) == (False, first.id)
    else:
        assert isinstance(finished.error, KeyReused)


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
    (batch.record), which the route answers 200 with. On a database of its
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


@pytest.mark.concurrency
def test_a_save_takes_its_places_in_id_order(committed: Committed) -> None:
    """A save holds its places by id (ledger._hold_places), as a count
    locks several (ledger.lock_places): one waiting for the first place a
    count holds has not taken the second, so the count takes it, rather
    than the two waiting on each other (inventory-tng-8i4.51.3). The save
    reads the table in the order it stores rows (no index), where renaming
    the first place puts it after the second, so only ordering by id takes
    it first."""
    # Made one after the other, so the first has the lower id.
    first = Location(name=f"Store {tag()}", kind="store")
    made(committed, first)
    second = Location(name=f"Store {tag()}", kind="store")
    made(committed, second)
    renaming = committed.session()
    stored_after = text(
        "SELECT (SELECT ctid FROM location WHERE id = :first)"
        " > (SELECT ctid FROM location WHERE id = :second)"
    )
    ids = {"first": first.id, "second": second.id}
    # A rename can land in a slot an earlier test freed: rename until not.
    for _ in range(20):
        renaming.execute(
            update(Location)
            .where(Location.id == first.id)
            .values(name=f"Store {tag()}")
        )
        renaming.commit()
        if renaming.scalar(stored_after, ids):
            break
    else:
        pytest.fail("the first place never came to be stored after the second")
    counting = committed.session()
    ledger.lock_place(counting, first.id)
    saving = committed.session()
    saving.execute(text("SET LOCAL enable_indexscan = off"))
    saving.execute(text("SET LOCAL enable_bitmapscan = off"))
    header = {"from_location_id": second.id, "to_location_id": first.id}
    worker = committed.blocked(saving, lambda: ledger.hold(saving, header, ()))
    ledger.lock_place(counting, second.id)
    counting.commit()
    assert worker.finished().error is None
    saving.rollback()


def two_areas(session: Session, store: Location) -> tuple[Location, Location]:
    """Two areas of the store, made one after the other, so the first has
    the lower id."""
    first, second = (
        Location(name=f"Shelf {tag()}", kind="area", parent_id=store.id)
        for _ in range(2)
    )
    session.add(first)
    session.flush()
    session.add(second)
    session.flush()
    assert first.id < second.id
    return first, second


def signed_in(
    committed: Committed, make_client: Callable[..., TestClient], totp: str | None
) -> tuple[Session, TestClient]:
    """A client on a session of its own, signed in with `totp`."""
    page = committed.session()
    client = make_client(on=page)
    assert sign_in_with(client, key=totp).status_code == 303
    return page, client


def held(column: InstrumentedAttribute[int], row_id: int) -> Write:
    """Taking the row of `column` `row_id`, FOR UPDATE."""
    return lambda session: session.execute(
        locked(select(column).where(column == row_id))
    )


def tracking(item_ids: list[int], on: bool) -> Write:
    """Setting these items to track opened packages, or not
    (items.track_items, FOR UPDATE by id), all of them taken."""

    def track(session: Session) -> None:
        assert items.track_items(session, item_ids, on) == item_ids

    return track


def answered_while_held(
    committed: Committed,
    page: Session,
    send: Callable[[], object],
    first: Write,
    then: Write,
) -> Answer:
    """`send` run while another session has done `first`: it waits for
    that holding nothing the other needs, so the other does `then` and
    commits, then `send` finishes. What it answered."""
    other = committed.session()
    first(other)
    worker = committed.blocked(page, send)
    then(other)
    other.commit()
    finished = worker.finished()
    assert finished.error is None
    return cast(Answer, finished.result)


@pytest.mark.end_to_end
def test_a_store_count_waits_for_a_save_between_two_of_its_areas(
    own_committed: Committed, make_client: Callable[..., TestClient]
) -> None:
    """A save between two areas of a store has held the first by id, as
    saves take places (ledger._hold_places), when the store's count is
    sent: the count waits for that place, not having taken the second
    (ledger.lock_places), so the save takes the second, writes and
    commits, rather than the two waiting on each other. The count then
    finds the radios moved after its moment, withholds them at both areas
    and asks for them again, writing no line of them (inventory-tng-099,
    inventory-tng-8i4.51.4). On a database of its own, as it writes the
    ledger."""
    setup = own_committed.session()
    place = Place(setup)
    _, totp = new_admin(setup)
    store = place.store
    first, second = two_areas(setup, store)
    radio = place.item.id
    insert_transaction(
        setup,
        uuid.uuid4(),
        {"kind": "receipt", "to_location_id": second.id},
        [(radio, Decimal(3))],
    )
    setup.commit()
    page, client = signed_in(own_committed, make_client, totp)
    path = f"{COUNTS}/{store.id}"
    form = count_form(client.get(path)) | {
        f"at_{first.id}_counted_{radio}": "0",
        f"at_{second.id}_counted_{radio}": "3",
    }
    saving = own_committed.session()
    saving.execute(
        locked(
            select(Location.id).where(Location.id == first.id),
            key_share=True,
            shared=True,
        )
    )
    worker = own_committed.blocked(page, lambda: client.post(path, data=form))
    insert_transaction(
        saving,
        uuid.uuid4(),
        {"kind": "move", "from_location_id": second.id, "to_location_id": first.id},
        [(radio, Decimal(1))],
    )
    saving.commit()
    finished = worker.finished()
    assert finished.error is None
    assert cast(Answer, finished.result).status_code == 409
    checking = own_committed.session()
    counted = checking.scalars(
        select(Movement.item_id)
        .join(LedgerTransaction, LedgerTransaction.id == Movement.transaction_id)
        .where(LedgerTransaction.kind == "count")
    ).all()
    assert counted == []
    assert expected(checking, first.id) == {radio: Decimal(1)}
    assert expected(checking, second.id) == {radio: Decimal(2)}


@pytest.mark.end_to_end
def test_a_store_count_holds_its_items_by_id_across_its_areas(
    own_committed: Committed, make_client: Callable[..., TestClient]
) -> None:
    """A store count holds every item its areas' counts name, by id across
    them all (ledger.hold_across), before writing the first area's. Two
    items set to track opened packages meanwhile (items.track_items, FOR
    UPDATE by id), the first held when the count is sent: the count waits
    for it holding neither, so tracking takes both and commits, then the
    count writes. Written area by area, the count would hold the second
    item, counted at the first area, and wait for the first, counted at
    the second, while tracking waits for the second: a deadlock
    (inventory-tng-nfia). On a database of its own, as it writes the
    ledger."""
    setup = own_committed.session()
    place = Place(setup)
    _, totp = new_admin(setup)
    first, second = two_areas(setup, place.store)
    radio, cable = place.item.id, place.cable.id
    assert radio < cable
    for area, item_id in ((first, cable), (second, radio)):
        insert_transaction(
            setup,
            uuid.uuid4(),
            {"kind": "receipt", "to_location_id": area.id},
            [(item_id, Decimal(3))],
        )
    setup.commit()
    page, client = signed_in(own_committed, make_client, totp)
    path = f"{COUNTS}/{place.store.id}"
    form = count_form(client.get(path)) | {
        f"at_{first.id}_counted_{cable}": "4",
        f"at_{second.id}_counted_{radio}": "4",
    }
    answered = answered_while_held(
        own_committed,
        page,
        lambda: client.post(path, data=form),
        held(Item.id, radio),
        tracking([radio, cable], True),
    )
    assert answered.status_code == 303
    checking = own_committed.session()
    assert expected(checking, first.id) == {cable: Decimal(4)}
    assert expected(checking, second.id) == {radio: Decimal(4)}


@pytest.mark.end_to_end
def test_a_count_holds_what_it_finds_and_loses_by_id(
    own_committed: Committed, make_client: Callable[..., TestClient]
) -> None:
    """A count of one place holds the items it finds and loses by id
    (ledger.hold_across) before writing either. Written as it was, what
    it found first, then what it lost, the count held the cable it found
    and waited for the radio it lost, held when it was sent by tracking
    opened packages (items.track_items), which then waited for the cable:
    a deadlock (inventory-tng-nfia). On a database of its own, as it
    writes the ledger."""
    setup = own_committed.session()
    place = Place(setup)
    _, totp = new_admin(setup)
    store = place.store.id
    radio, cable = place.item.id, place.cable.id
    assert radio < cable
    insert_transaction(
        setup,
        uuid.uuid4(),
        {"kind": "receipt", "to_location_id": store},
        [(radio, Decimal(3)), (cable, Decimal(3))],
    )
    setup.commit()
    page, client = signed_in(own_committed, make_client, totp)
    path = f"{COUNTS}/{store}"
    form = count_form(client.get(path)) | {
        f"counted_{radio}": "2",
        f"counted_{cable}": "4",
    }
    answered = answered_while_held(
        own_committed,
        page,
        lambda: client.post(path, data=form),
        held(Item.id, radio),
        tracking([radio, cable], True),
    )
    assert answered.status_code == 303
    checking = own_committed.session()
    assert expected(checking, store) == {radio: Decimal(2), cable: Decimal(4)}


@pytest.mark.end_to_end
def test_a_store_count_holds_the_checks_it_settles_by_id_across_its_areas(
    own_committed: Committed, make_client: Callable[..., TestClient]
) -> None:
    """A store count holds every check of recorded more than moved it may
    put right, by id across its areas (over_recorded.hold_waiting), before
    writing the first area's. Radios recorded more moved from a van to
    each area, the second area's check first, so it has the lower id; a
    count at the van holds that check when the store's is sent, as it
    takes them by id: the store's count waits for it holding neither, so
    the van's takes the other check too and commits. Put right area by
    area, the store's would hold the first area's check, of the higher
    id, and wait for the second's, while the van's waits for the first's:
    a deadlock (inventory-tng-nfia). On a database of its own, as it
    writes the ledger."""
    setup = own_committed.session()
    place = Place(setup)
    _, totp = new_admin(setup)
    first, second = two_areas(setup, place.store)
    radio = place.item.id
    insert_transaction(
        setup,
        uuid.uuid4(),
        {"kind": "receipt", "to_location_id": place.van.id},
        [(radio, Decimal(6))],
    )
    raised = [
        raise_check(
            setup,
            insert_transaction(
                setup,
                uuid.uuid4(),
                {
                    "kind": "move",
                    "from_location_id": place.van.id,
                    "to_location_id": area.id,
                },
                [(radio, Decimal(3))],
            ),
        ).id
        for area in (second, first)
    ]
    setup.commit()
    assert raised == sorted(raised)
    page, client = signed_in(own_committed, make_client, totp)
    path = f"{COUNTS}/{place.store.id}"
    form = count_form(client.get(path)) | {
        f"at_{first.id}_counted_{radio}": "3",
        f"at_{second.id}_counted_{radio}": "3",
    }
    lower, higher = (held(OverRecorded.id, check) for check in raised)
    answered = answered_while_held(
        own_committed, page, lambda: client.post(path, data=form), lower, higher
    )
    assert answered.status_code == 303
    checking = own_committed.session()
    arrived = checking.scalars(
        select(OverRecorded.arrived_count_id).where(OverRecorded.id.in_(raised))
    ).all()
    assert len(arrived) == 2
    assert None not in arrived


def two_spools(session: Session) -> tuple[Spools, Spools]:
    """Two cables in spools, each at a store of its own, the first's item
    of the lower id."""
    first, second = Spools(session), Spools(session)
    assert first.cable.id < second.cable.id
    return first, second


def opened_out_of_item_order(
    session: Session,
    spools: tuple[Spools, Spools],
    reported: Callable[[Spools, OpenPackage], dict[str, object]],
) -> tuple[tuple[int, int], tuple[OpenPackage, OpenPackage]]:
    """The two cables' items (two_spools), and a spool of each opened and
    reported by a volunteer's batch (`reported`): the second cable's
    first, so its open package has the lower id, the other way round from
    their items."""
    first, second = spools
    second_open, first_open = second.opened(), first.opened()
    assert second_open.id < first_open.id
    for each, row in ((first, first_open), (second, second_open)):
        record(session, Batch.model_validate(reported(each, row)), device_id=None)
    return (first.cable.id, second.cable.id), (first_open, second_open)


@pytest.mark.end_to_end
def test_used_up_confirmed_together_holds_their_items_by_id(
    own_committed: Committed, make_client: Callable[..., TestClient]
) -> None:
    """Confirming several open packages used up at once holds every one's
    item, then them, by id across them all (ledger.hold_across), before
    closing the first. Closed one after another, in open package order,
    the answer would hold the second cable, of the first open package, and
    wait for the first, held by tracking opened packages
    (items.track_items), which then waits for the second: a deadlock
    (inventory-tng-q0t5). On a database of its own, as it writes the
    ledger."""
    setup = own_committed.session()
    _, totp = new_admin(setup)
    cables, opened = opened_out_of_item_order(
        setup, two_spools(setup), lambda spools, row: report(spools, used_up=[row.key])
    )
    setup.commit()
    page, client = signed_in(own_committed, make_client, totp)
    form = {"open_package": [str(row.id) for row in opened], "do": "confirm"}
    answered = answered_while_held(
        own_committed,
        page,
        lambda: client.post(USED_UP, data=form),
        held(Item.id, cables[0]),
        tracking(list(cables), False),
    )
    assert answered.headers["location"].endswith("notice=used_up_confirmed")
    checking = own_committed.session()
    closed = checking.scalars(
        select(OpenPackage.closed_at).where(
            OpenPackage.id.in_([row.id for row in opened])
        )
    ).all()
    assert len(closed) == 2
    assert None not in closed


@pytest.mark.end_to_end
def test_length_checks_answered_together_hold_their_items_by_id(
    own_committed: Committed, make_client: Callable[..., TestClient]
) -> None:
    """Setting several length checks at once holds every open package's
    item, then them, by id across them all (ledger.hold_across), before
    counting the first. Counted one after another, in open package order,
    the answer would hold the second cable, of the first open package, and
    wait for the first, held by tracking opened packages
    (items.track_items), which then waits for the second: a deadlock
    (inventory-tng-q0t5). On a database of its own, as it writes the
    ledger."""
    setup = own_committed.session()
    _, totp = new_admin(setup)
    cables, opened = opened_out_of_item_order(
        setup, two_spools(setup), lambda spools, row: report(spools, length(row, "250"))
    )
    setup.commit()
    ticked = [f"{row.id} {open_packages.check_seen(row, 'm')}" for row in opened]
    page, client = signed_in(own_committed, make_client, totp)
    form = {"open_package": ticked, "do": "set"}
    answered = answered_while_held(
        own_committed,
        page,
        lambda: client.post(LENGTHS, data=form),
        held(Item.id, cables[0]),
        tracking(list(cables), False),
    )
    assert answered.headers["location"].endswith("notice=length_set")
    checking = own_committed.session()
    reported = checking.scalars(
        select(OpenPackage.length_reported).where(
            OpenPackage.id.in_([row.id for row in opened])
        )
    ).all()
    assert reported == [None, None]


@pytest.mark.end_to_end
def test_length_checks_answered_together_hold_the_checks_they_settle_by_id(
    own_committed: Committed, make_client: Callable[..., TestClient]
) -> None:
    """Setting several length checks at once holds every check of recorded
    more than moved the Sets may put right, by id across them all
    (over_recorded.hold_waiting), before counting the first. A spool of
    each cable moved from its store recorded more than moved, the first
    cable's check first, so it has the lower id; a count holding that
    check when the answer is sent, as it takes them by id, lets the
    answer wait for it holding neither, so the count takes the other check
    too and commits. Counted one after another, in open package order,
    the answer would hold the second cable's check, of the higher id, and
    wait for the first's, while the count waits for the second's: a
    deadlock (inventory-tng-q0t5). On a database of its own, as it writes
    the ledger."""
    setup = own_committed.session()
    _, totp = new_admin(setup)
    spools = two_spools(setup)
    raised = [
        raise_check(
            setup,
            each.move(Entry(each.cable.id, Decimal(305), each.sealed, each.sealed)),
        ).id
        for each in spools
    ]
    assert raised == sorted(raised)
    _, opened = opened_out_of_item_order(
        setup, spools, lambda each, row: report(each, length(row, "250"))
    )
    setup.commit()
    ticked = [f"{row.id} {open_packages.check_seen(row, 'm')}" for row in opened]
    page, client = signed_in(own_committed, make_client, totp)
    form = {"open_package": ticked, "do": "set"}
    lower, higher = (held(OverRecorded.id, check) for check in raised)
    answered = answered_while_held(
        own_committed, page, lambda: client.post(LENGTHS, data=form), lower, higher
    )
    assert answered.headers["location"].endswith("notice=length_set")


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
    return client.post(ADMINS, data={FIELD: key, "username": username})


@pytest.mark.end_to_end
def test_a_form_refused_after_its_key_is_spent_can_be_sent_again(
    committed: Committed, admins: Admins, real_client: tuple[TestClient, SentMail]
) -> None:
    """A taken username is found after the key is spent: refused without a
    commit, nothing is kept, so the same form, put right, goes through
    (web/single_use_forms.py, inventory-tng-8i4.74)."""
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


@pytest.mark.concurrency
def test_the_catalogue_reads_one_snapshot(
    own_committed: Committed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A batch committed while the catalogue is read is in neither its
    stock nor `saved`; one committed before is in both (inventory-tng-19c).
    Under READ COMMITTED, the receipt saved between the stock's read and the
    keys' would be said saved and not counted, and the page would never
    take it off. On a database of its own, as it writes the ledger."""
    setup = own_committed.session()
    spools = Spools(setup)
    before, during = uuid.uuid4(), uuid.uuid4()
    spools.receive(before)
    setup.commit()
    reader = own_committed.session()
    # The device's read, as the route's request makes it first.
    reader.scalar(select(Location.id).limit(1))
    get_snapshot(reader)
    to_take = catalogue_api.open_to_take

    def saving_meanwhile(session: Session, item_ids: set[int]) -> object:
        spools.receive(during)
        setup.commit()
        return to_take(session, item_ids)

    monkeypatch.setattr(catalogue_api, "open_to_take", saving_meanwhile)
    reply = catalogue_api.with_saved(reader, [before, during])
    reader.rollback()
    assert reply.saved == [before]
    [cable] = [each for each in reply.items if each.id == spools.cable.id]
    # Two received with the place, and `before`'s.
    assert [(s.location_id, s.count) for s in cable.packages[0].sealed] == [
        (spools.store.id, 3)
    ]
    # One that has read nothing yet begins its snapshot as is.
    fresh = get_snapshot(own_committed.session())
    assert fresh.scalar(text("SHOW transaction_isolation")) == "repeatable read"
    fresh.rollback()


@pytest.mark.end_to_end
def test_the_catalogue_route_reads_one_snapshot(
    committed: Committed,
    make_client: Callable[..., TestClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a session of its own, as in production: REPEATABLE READ, from
    after its device was read."""
    setup = committed.session()
    _, token = new_device(setup)
    setup.commit()
    seen = isolation_seen(monkeypatch, catalogue_api, "with_saved")
    client = make_client(own_sessions=True)
    client.cookies.set(devices.COOKIE, token)
    assert client.get("/api/catalogue").status_code == 200
    assert seen == [("repeatable read", "on")]


def _unlocked_forms(
    session: Session,
) -> dict[str, tuple[object, str, dict[str, str], Base, str, str]]:
    """Each form checked first by db_locking.stale_as_read, which takes no
    lock: the route's module, where it is sent, what it sends but its
    version, its row, and a column another administrator saves meanwhile
    with its new value."""
    radios = Category(name="Radios")
    session.add(radios)
    session.flush()
    item = Item(name="LiteBeam", category_id=radios.id)
    ada = Volunteer(name="Ada")
    session.add_all([item, ada])
    device, _ = new_device(session)
    session.flush()
    return {
        "item": (
            items_routes,
            f"{ITEMS}/{item.id}",
            {"category_id": str(radios.id), "unit": "each", "name": "LiteBeam 5AC"},
            item,
            "notes",
            "Outdoor only",
        ),
        "volunteer": (
            volunteers_routes,
            f"{VOLUNTEERS}/{ada.id}",
            {"name": "Ada L", "email": ""},
            ada,
            "email",
            "ada@example.org",
        ),
        "device": (
            devices_routes,
            f"{DEVICES}/{device.id}",
            {"label": "Store tablet"},
            device,
            "label",
            "Lost",
        ),
    }


@pytest.mark.end_to_end
@pytest.mark.parametrize("name", ["item", "volunteer", "device"])
def test_a_save_after_the_unlocked_check_is_still_refused(
    committed: Committed,
    admins: Admins,
    real_client: tuple[TestClient, SentMail],
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    """Another administrator's save, committed after stale_as_read found
    the form current and before the route locks the row: the save checks
    the version again under its lock, read afresh, so it is refused and the
    other's change kept, not lost (inventory-tng-ryjv)."""
    client, _ = real_client
    signed_in_as_ben(committed, client, admins[2])
    setup = committed.session()
    module, path, sends, row, column, meanwhile = _unlocked_forms(setup)[name]
    setup.commit()
    model, row_id, version = type(row), getattr(row, "id"), shown(row)  # noqa: B009
    unlocked = db_locking.stale_as_read

    def saved_meanwhile(found: Base, seen: int | None) -> bool:
        stale = unlocked(found, seen)
        other = committed.session()
        setattr(other.get_one(model, row_id), column, meanwhile)
        other.commit()
        return stale

    monkeypatch.setattr(module, "stale_as_read", saved_meanwhile)
    refused = client.post(path, data=sends | {"version": version})
    assert refused.status_code == 409
    after = committed.session().get_one(model, row_id)
    assert getattr(after, column) == meanwhile
