"""Who writes which table, and holding what: each model declares it
(db.protection), and db.py says what each declaration means. The app's
flush guard is tested here on PostgreSQL. It sees every write, by setting an
attribute too, but not where a row was made: so the first test below reads
the app's source with Python's ast module (nothing of it runs in the app;
helpers.source.writes) and fails on a row made outside its owner, or one
deleted outside the few functions allowed to. Statements on a table with
history, such as update(Item), are refused everywhere by test_history.py.
"""

import ast
from collections.abc import Callable, Iterator
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import argon2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, Text, exc, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from structlog.testing import capture_logs

from inventory_tng import accounts
from inventory_tng.db import (
    CATALOGUE,
    LOCKS_INFO,
    Action,
    Base,
    DbSession,
    Gone,
    Lock,
    Sessions,
    attempt,
    changed_columns,
    enforce,
    flagged,
    get_locked,
    lock_catalogue,
    locked,
    missing_lock,
    settings_row,
)
from inventory_tng.models import Admin, AdminPassword, Category, Item, SiteSettings

from helpers.rows import setting_up, tag
from helpers.source import SOURCE, offending, writes

FOREIGN_KEY_VIOLATION = "23503"  # PostgreSQL's code for a missing parent.
LOCK_NOT_AVAILABLE = "55P03"  # PostgreSQL's, for NOWAIT on a locked row.

TABLES = Base.metadata.tables
# Each table's owner, as its model declares it. None: no module, only
# ALLOWED's functions (or its migration).
OWNERS: dict[str, str | None] = {t.name: t.info["owner"] for t in TABLES.values()}
# The functions that may write any table: one deletes an administrator's
# password, as it is reset; one is the ledger's one writer; and the seed
# makes its administrator, in a development database only.
ALLOWED = {
    SOURCE / "accounts.py": ("_reset_sign_in",),
    SOURCE / "ledger.py": ("insert_transaction",),
    SOURCE / "seed.py": ("run",),
}
# The tables a write needs no lock for, written out, so none loses its
# lock by a slip in its declaration: only added to, or changed on every
# request by its own browser.
UNLOCKED = {
    "catalogue_history",
    "ledger_transaction",
    "movement",
    "admin_session",
    "sent_form",
}


def not_owned_writes(tree: ast.Module, path: Path) -> list[int]:
    """The lines that make or write a row of a table in a module not its
    owner, or delete any row (session.delete)."""
    module = str(path.relative_to(SOURCE)) if path.is_relative_to(SOURCE) else ""
    return sorted(
        {
            write.line
            for write in writes(tree)
            if write.how == "delete"
            or (write.table is not None and OWNERS[write.table] != module)
        }
    )


@pytest.mark.helper
def test_rows_are_written_by_their_owners_only() -> None:
    found = offending(not_owned_writes, ALLOWED)
    assert found == {}, (
        "Write these tables through their owner's domain functions "
        f"(db.protection's owner, test_write_owners.ALLOWED), which check the"
        f" rules: {found}"
    )


def test_each_owner_is_a_domain_module() -> None:
    """Not a page's: the pages call the owners' functions, which hold the
    rules, so another front end can too (inventory-tng-8i4.122)."""
    owners = {owner for owner in OWNERS.values() if owner is not None}
    assert all((SOURCE / owner).is_file() for owner in owners)
    assert [owner for owner in owners if owner.startswith("admin/")] == []


def test_every_table_declares_its_protection() -> None:
    """So a new table cannot be forgotten (db.protection's types say the
    rest). The catalogue's are made by a module of their own. A table with
    a lock has history, so test_history.py refuses a statement writing it,
    which the guard would not see."""
    for table in TABLES.values():
        assert set(table.info) == {"owner", "lock", "history"}, table.name
        if table.info["lock"] == "catalogue":
            assert table.info["owner"] is not None, table.name
        if table.info["lock"] is not None:
            assert table.info["history"], table.name
    assert {t.name for t in TABLES.values() if t.info["lock"] is None} == UNLOCKED


def test_no_model_has_a_relationship() -> None:
    """The guard and history see only the columns a session was given to
    change (db.flush_writes), not rows a relationship() changes as it
    flushes."""
    related = {
        mapper.class_.__name__
        for mapper in Base.registry.mappers
        if mapper.relationships
    }
    assert related == set(), (
        "db.flush_writes and history.py see column changes only: write the"
        f" foreign key instead of a relationship() on {related}"
    )


@pytest.mark.parametrize(
    ("module", "code", "found"),
    [
        ("admin/items.py", "Item(name='x')", True),
        ("admin/items.py", "session.delete(row)", True),
        ("items.py", "session.delete(row)", True),
        ("items.py", "Item(name='x')", False),
        ("items.py", "ItemPackage(item_id=1)", False),
        ("items.py", "Label(item_id=1)", True),
        # The ledger: only ALLOWED's insert_transaction, in no module.
        ("ledger.py", "Movement(item_id=1)", True),
        (
            "ledger.py",
            "from sqlalchemy import insert\ninsert(LedgerTransaction)",
            True,
        ),
        ("admin/counts.py", "text('INSERT INTO movement VALUES (1)')", True),
        ("admin/settings.py", "SiteSettings(id=2)", True),
        (
            "admin/settings.py",
            "from sqlalchemy import delete\ndelete(ScanSettings)",
            True,
        ),
        ("admin/settings.py", "AdjustmentReason(name='x')", True),
        ("adjustment_reasons.py", "AdjustmentReason(name='x')", False),
        # Not a write.
        ("admin/items.py", "select(Item).where(Item.name == 'x')", False),
    ],
)
def test_the_check_sees_each_way_of_writing(
    module: str, code: str, found: bool
) -> None:
    """So the test above cannot pass by missing one."""
    assert bool(not_owned_writes(ast.parse(code), SOURCE / module)) is found


# The guard's rule, without a database.


@pytest.mark.parametrize(
    ("lock", "action", "changed", "catalogue", "row", "missing"),
    [
        (None, "update", {"name"}, False, False, None),
        ("row", "insert", set(), False, False, None),
        ("row", "update", {"name"}, False, False, "row"),
        ("row", "delete", set(), False, False, "row"),
        ("row", "update", {"name"}, False, True, None),
        ("catalogue", "insert", set(), False, False, "catalogue"),
        ("catalogue", "insert", set(), True, False, None),
        ("catalogue", "update", {"name"}, True, False, "row"),
        ("catalogue", "update", {"name"}, False, True, "catalogue"),
        ("catalogue", "update", {"name"}, True, True, None),
        # Only columns that need its row's lock alone, such as on_order.
        ("catalogue", "update", set(), False, True, None),
        ("catalogue", "update", set(), False, False, "row"),
        ("catalogue", "delete", set(), True, False, "row"),
        ("catalogue", "delete", set(), False, True, "catalogue"),
    ],
)
def test_the_lock_a_write_lacks(
    lock: Lock | None,
    action: Action,
    changed: set[str],
    catalogue: bool,
    row: bool,
    missing: str | None,
) -> None:
    found = missing_lock(
        lock,
        action,
        changed,
        catalogue=catalogue,
        row=row,
    )
    assert found == missing


class _Elsewhere(DeclarativeBase):
    pass


class _Renamed(_Elsewhere):
    """A column mapped under another attribute's name."""

    __tablename__ = "renamed"
    id: Mapped[int] = mapped_column(primary_key=True)
    shown: Mapped[str] = mapped_column("stored", Text, info={"row_lock_only": True})


def test_changes_are_named_by_column() -> None:
    """As protection flags them, whatever attribute maps the column."""
    row = _Renamed(shown="x")
    assert changed_columns(row) == {"stored"}
    assert flagged(_Renamed.__table__, "row_lock_only") == {"stored"}


# On PostgreSQL: the guard as the app's sessions meet it.


@pytest.fixture
def fresh(engine: Engine) -> Iterator[Session]:
    """A session as the app's are, rolled back after."""
    with Session(engine) as session:
        yield session
        session.rollback()


def held(session: Session) -> dict[object, object]:
    return session.info.get(LOCKS_INFO, {})


def test_a_catalogue_row_written_without_the_lock_is_refused(fresh: Session) -> None:
    fresh.add(Category(name="Radios"))
    with pytest.raises(
        RuntimeError, match="category insert without the catalogue lock"
    ):
        fresh.flush()


def test_one_written_under_the_lock_is_not(fresh: Session) -> None:
    lock_catalogue(fresh)
    fresh.add(Category(name="Radios"))
    fresh.flush()
    # Held until the transaction ends.
    fresh.rollback()
    assert held(fresh) == {}


def test_a_lock_taken_in_a_refused_change_goes_with_it(fresh: Session) -> None:
    """The savepoint attempt() rolls back lets go of the lock taken in it."""

    def refused() -> None:
        lock_catalogue(fresh)
        enforce("category_has_items")

    assert attempt(fresh, refused) == (None, "category_has_items")
    assert CATALOGUE not in held(fresh)


def test_a_lock_taken_in_a_change_that_fails_goes_with_it(fresh: Session) -> None:
    """Gone, or any other failure, rolls the savepoint back as a rule does."""

    def gone() -> None:
        lock_catalogue(fresh)
        raise Gone

    with pytest.raises(Gone):
        attempt(fresh, gone)
    assert CATALOGUE not in held(fresh)


def test_a_lock_taken_in_a_change_whose_flush_fails_goes_with_it(
    fresh: Session,
) -> None:
    """A name taken, found as the savepoint flushes: SQLAlchemy rolls the
    savepoint back from inside the flush, and only closes it after."""
    lock_catalogue(fresh)
    fresh.add(Category(name="Radios"))
    fresh.flush()
    del held(fresh)[CATALOGUE]  # As in a transaction that had not taken it.

    def taken() -> None:
        lock_catalogue(fresh)
        fresh.add(Category(name="Radios"))

    assert attempt(fresh, taken) == (None, "unique")
    assert CATALOGUE not in held(fresh)


def test_a_change_breaking_another_constraint_is_raised(fresh: Session) -> None:
    """A foreign key, say: no rule a page words, so a bug. The savepoint
    rolls back, and what came before it is kept."""
    lock_catalogue(fresh)
    category = Category(name="Radios")
    fresh.add(category)
    fresh.flush()

    def orphan() -> None:
        lock_catalogue(fresh)
        fresh.add(Item(name="LiteBeam", category_id=category.id + 1))

    with pytest.raises(exc.IntegrityError) as raised:
        attempt(fresh, orphan)
    assert getattr(raised.value.orig, "sqlstate", None) == FOREIGN_KEY_VIOLATION
    assert fresh.scalars(select(Category.id)).all() == [category.id]
    assert fresh.scalars(select(Item.id)).all() == []
    assert CATALOGUE in held(fresh)  # Taken before the savepoint: still held.


def test_a_receipt_lowers_what_is_on_order_with_the_items_lock_alone(
    fresh: Session,
) -> None:
    """ledger._receive locks the items but takes no catalogue lock:
    on_order alone (row_lock_only)."""
    lock_catalogue(fresh)
    category = Category(name="Radios")
    fresh.add(category)
    fresh.flush()
    item = Item(name="LiteBeam", category_id=category.id, on_order=Decimal(5))
    fresh.add(item)
    fresh.flush()
    del held(fresh)[CATALOGUE]  # As in a transaction that never took it.
    item.on_order = Decimal(2)
    fresh.flush()
    item.name = "LiteBeam 5AC"
    with pytest.raises(RuntimeError, match="item update without the catalogue lock"):
        fresh.flush()


def test_a_row_changed_without_its_lock_is_refused(fresh: Session) -> None:
    """Read with session.get, not locked: set by an attribute, which no
    reading of the source can tie to its owner."""
    site = fresh.get_one(SiteSettings, 1)
    site.time_zone = "Europe/London"
    with pytest.raises(
        RuntimeError,
        match=r"site_settings update without the row lock: lock it in this"
        r" transaction before changing it \(db.get_locked",
    ):
        fresh.flush()


def test_a_row_set_to_what_it_holds_is_no_write(fresh: Session) -> None:
    """The session counts it changed, but writes nothing."""
    site = fresh.get_one(SiteSettings, 1)
    site.time_zone = site.time_zone
    assert site in fresh.dirty
    fresh.flush()


def test_a_row_deleted_without_its_lock_is_refused(fresh: Session) -> None:
    fresh.delete(fresh.get_one(SiteSettings, 1))
    with pytest.raises(RuntimeError, match="site_settings delete without the row lock"):
        fresh.flush()


@pytest.mark.parametrize(
    "lock",
    [
        lambda session: settings_row(session, SiteSettings),
        lambda session: get_locked(session, SiteSettings, 1, key_share=True),
        lambda session: session.scalars(
            # locked(), as a page's query would.
            locked(select(SiteSettings))
        ).one(),
    ],
    ids=["settings_row", "get_locked", "locked"],
)
def test_one_locked_first_is_not(
    fresh: Session, lock: Callable[[Session], SiteSettings | None]
) -> None:
    site = lock(fresh)
    assert site is not None
    site.time_zone = "Europe/London"
    fresh.flush()


@pytest.mark.parametrize(
    ("of", "noted"),
    [
        (SiteSettings, True),
        (SiteSettings.__table__, False),
        (SiteSettings.id, False),
        ([SiteSettings.id], False),
    ],
    ids=["model", "table", "column", "list"],
)
def test_locked_notes_what_it_can_tell_is_locked(
    fresh: Session, of: object, noted: bool
) -> None:
    """A model's rows; for any other `of`, nothing, so a change is refused
    rather than let through."""
    site = fresh.scalars(locked(select(SiteSettings), of=of)).one()  # ty: ignore[invalid-argument-type]
    site.time_zone = "Europe/London"
    if noted:
        fresh.flush()
    else:
        with pytest.raises(RuntimeError, match="site_settings update"):
            fresh.flush()


def test_a_shared_lock_is_not_enough(fresh: Session) -> None:
    """FOR KEY SHARE keeps a row from being deleted, not from a change."""
    site = fresh.scalars(
        locked(select(SiteSettings), key_share=True, shared=True)
    ).one()
    site.time_zone = "Europe/London"
    with pytest.raises(RuntimeError, match="site_settings update"):
        fresh.flush()


def test_a_row_added_in_the_transaction_is_its_own(fresh: Session) -> None:
    """No other transaction can see it until it commits."""
    lock_catalogue(fresh)
    category = Category(name="Radios")
    fresh.add(category)
    fresh.flush()
    category.name = "Antennas"
    fresh.flush()


@pytest.mark.parametrize(
    ("savepoints", "kept"),
    [
        ("rolled back", False),
        ("released", True),
        ("released, then the one around it rolled back", False),
    ],
)
def test_a_savepoint_rolled_back_forgets_its_locks(
    fresh: Session, savepoints: str, kept: bool
) -> None:
    """As PostgreSQL lets go of them; one released passes them to the
    transaction around it."""
    with fresh.begin_nested() as outer:
        with fresh.begin_nested() as inner:
            site = settings_row(fresh, SiteSettings)
            if savepoints == "rolled back":
                inner.rollback()
        if savepoints.endswith("rolled back"):
            outer.rollback()
    site.time_zone = "Europe/London"
    if kept:
        fresh.flush()
    else:
        with pytest.raises(RuntimeError, match="site_settings update"):
            fresh.flush()


@pytest.mark.parametrize("savepoint", ["rolled back", "released"])
def test_a_row_written_in_a_savepoint_keeps_its_lock(
    fresh: Session, engine: Engine, savepoint: str
) -> None:
    """Locked before the savepoint, changed in it: PostgreSQL keeps the
    lock however the savepoint ends (since 9.3, its SELECT page's
    Locking Clause), so the guard does too, and another transaction still
    cannot lock the row."""
    site = settings_row(fresh, SiteSettings)
    with fresh.begin_nested() as inner:
        site.time_zone = "Europe/London"
        fresh.flush()
        if savepoint == "rolled back":
            inner.rollback()
    with engine.connect() as other, pytest.raises(exc.OperationalError) as raised:
        other.execute(
            text("SELECT 1 FROM site_settings WHERE id = 1 FOR UPDATE NOWAIT")
        )
    assert getattr(raised.value.orig, "sqlstate", None) == LOCK_NOT_AVAILABLE
    site.time_zone = "Asia/Tokyo"
    fresh.flush()


def test_the_transactions_end_forgets_every_lock(fresh: Session) -> None:
    site = settings_row(fresh, SiteSettings)
    fresh.rollback()
    assert held(fresh) == {}
    site.time_zone = "Europe/London"
    with pytest.raises(RuntimeError, match="site_settings update"):
        fresh.flush()


def test_a_password_rehashed_as_it_is_checked_is_locked_first(fresh: Session) -> None:
    """argon2's parameters moved on since the hash was made, so checking it
    stores a new one, on the row it locked (accounts.check_password)."""
    admin = Admin(username=f"rehash{tag()}")
    fresh.add(admin)
    fresh.flush()
    older = argon2.PasswordHasher.from_parameters(
        replace(argon2.profiles.CHEAPEST, time_cost=2)
    )
    made = older.hash("correct horse")
    fresh.add(AdminPassword(id=admin.id, password_hash=made))
    fresh.flush()
    held(fresh).clear()  # As in a later transaction, which did not add them.
    assert accounts.check_password(fresh, admin.id, "correct horse")
    fresh.flush()
    assert fresh.get_one(AdminPassword, admin.id).password_hash != made


def test_a_tests_own_session_is_left_alone(fresh: Session) -> None:
    """helpers.rows.setting_up, which writes rows to set up a test."""
    setting_up(fresh)
    fresh.get_one(SiteSettings, 1).time_zone = "Europe/London"
    fresh.add(Category(name="Radios"))
    fresh.flush()


def test_the_apps_requests_in_a_test_are_guarded_and_its_setup_is_not(
    make_app: Callable[..., FastAPI], session: Session
) -> None:
    """The app's requests meet the guard, and what they leave unflushed is
    dropped; the test's setup around them does not (helpers.apps.as_the_app)."""
    app = make_app()

    @app.post("/unlocked")
    def unlocked(request_session: DbSession, sessions: Sessions) -> None:
        with sessions():
            pass
        request_session.get_one(SiteSettings, 1).time_zone = "Europe/London"
        request_session.flush()

    @app.post("/unflushed")
    def unflushed(request_session: DbSession) -> None:
        request_session.add(Category(name=f"Cables {tag()}"))
        request_session.delete(radios)

    # Unflushed, and with the row locked: the app finds neither.
    settings_row(session, SiteSettings).time_zone = "Asia/Tokyo"
    radios = Category(name=f"Radios {tag()}")
    session.add(radios)
    with (
        TestClient(app, base_url="https://testserver") as client,
        capture_logs() as logs,
    ):
        # The app answers its own errors: the guard's refusal is a 500.
        assert client.post("/unlocked").status_code == 500
        assert client.post("/unflushed").status_code == 200
    assert not session.new
    assert not session.deleted
    assert radios in session
    refused = [
        log["table"] for log in logs if log["event"] == "written without its lock"
    ]
    assert refused == ["site_settings"]
    settings = session.get_one(SiteSettings, 1)
    assert settings.time_zone == "Asia/Tokyo"
    settings.time_zone = "Europe/Paris"
    session.flush()
