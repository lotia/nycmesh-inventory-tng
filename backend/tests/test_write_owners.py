"""Each catalogue table is written by one module, its owner, whose domain
functions take the catalogue lock and check the rules (db.locked_row); the
ledger, by ledger.insert_transaction alone, which checks its rules and
writes a transaction's movements with it. Each settings table has one row,
made by its migration, which nothing makes again or deletes.

Two checks hold that: the app refuses a flush that writes a catalogue row
without the catalogue lock (db.LOCK_INFO), tested here on PostgreSQL; and
the first test below reads the app's source with Python's ast module
(nothing of it runs in the app; helpers.source.writes) and fails on a row
made or written outside its owner, or a row deleted outside the few
functions allowed to. Nothing in the catalogue is deleted. Statements on a
table with history, such as update(Item), are refused everywhere by
test_history.py.
"""

import ast
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, exc, select
from sqlalchemy.orm import Session

from inventory_tng.db import LOCK_INFO, Gone, attempt, enforce, lock_catalogue
from inventory_tng.models import Category, Item

from helpers.source import SOURCE, offending, writes

FOREIGN_KEY_VIOLATION = "23503"  # PostgreSQL's code for a missing parent.

# Each table written by one module only, and that module. None: no module,
# only ALLOWED's functions.
OWNERS: dict[str, str | None] = {
    "category": "categories.py",
    "item": "items.py",
    "item_package": "items.py",
    "label": "labels.py",
    "location": "locations.py",
    "volunteer": "volunteers.py",
    # Nothing writes these yet.
    "vendor": None,
    "vendor_offer": None,
    "vendor_price": None,
    # Only ALLOWED's insert_transaction.
    "ledger_transaction": None,
    "movement": None,
    # Never made or deleted. Their row is changed by setting attributes
    # (admin/settings.py, scan_settings.py), which this check cannot see.
    "scan_settings": None,
    "sign_in_settings": None,
    "site_settings": None,
}
# The functions that may write any table: one deletes an administrator's
# password, as it is reset; the other is the ledger's one writer.
ALLOWED = {
    SOURCE / "accounts.py": ("_reset_sign_in",),
    SOURCE / "ledger.py": ("insert_transaction",),
}


def not_owned_writes(tree: ast.Module, path: Path) -> list[int]:
    """The lines that make or write a row of a table in OWNERS in a module
    not its owner, or delete any row (session.delete)."""
    module = str(path.relative_to(SOURCE)) if path.is_relative_to(SOURCE) else ""
    return sorted(
        {
            write.line
            for write in writes(tree)
            if write.how == "delete"
            or (write.table in OWNERS and OWNERS[write.table] != module)
        }
    )


@pytest.mark.helper
def test_rows_are_written_by_their_owners_only() -> None:
    found = offending(not_owned_writes, ALLOWED)
    assert found == {}, (
        "Write these tables through their owner's domain functions "
        f"(test_write_owners.OWNERS, ALLOWED), which check the rules: {found}"
    )


def test_each_owner_is_a_module() -> None:
    assert all(
        (SOURCE / owner).is_file() for owner in OWNERS.values() if owner is not None
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
        # Not the catalogue's, and not a write.
        ("admin/items.py", "AdjustmentReason(name='x')", False),
        ("admin/items.py", "select(Item).where(Item.name == 'x')", False),
    ],
)
def test_the_check_sees_each_way_of_writing(
    module: str, code: str, found: bool
) -> None:
    """So the test above cannot pass by missing one."""
    assert bool(not_owned_writes(ast.parse(code), SOURCE / module)) is found


# On PostgreSQL: the guard as the app's sessions meet it.


@pytest.fixture
def fresh(engine: Engine) -> Iterator[Session]:
    """A session as the app's are, rolled back after."""
    with Session(engine) as session:
        yield session
        session.rollback()


def test_a_catalogue_row_written_without_the_lock_is_refused(fresh: Session) -> None:
    fresh.add(Category(name="Radios"))
    with pytest.raises(RuntimeError, match="catalogue lock"):
        fresh.flush()


def test_one_written_under_the_lock_is_not(fresh: Session) -> None:
    lock_catalogue(fresh)
    fresh.add(Category(name="Radios"))
    fresh.flush()
    # Held until the transaction ends.
    fresh.rollback()
    assert LOCK_INFO not in fresh.info


def test_a_lock_taken_in_a_refused_change_goes_with_it(fresh: Session) -> None:
    """The savepoint attempt() rolls back lets go of the lock taken in it."""

    def refused() -> None:
        lock_catalogue(fresh)
        enforce("category_has_items")

    assert attempt(fresh, refused) == (None, "category_has_items")
    assert LOCK_INFO not in fresh.info


def test_a_lock_taken_in_a_change_that_fails_goes_with_it(fresh: Session) -> None:
    """Gone, or any other failure, rolls the savepoint back as a rule does."""

    def gone() -> None:
        lock_catalogue(fresh)
        raise Gone

    with pytest.raises(Gone):
        attempt(fresh, gone)
    assert LOCK_INFO not in fresh.info


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
    assert LOCK_INFO in fresh.info  # Taken before the savepoint: still held.


def test_a_receipt_lowers_what_is_on_order_without_the_lock(fresh: Session) -> None:
    """ledger._receive, which takes no catalogue lock: on_order alone."""
    lock_catalogue(fresh)
    category = Category(name="Radios")
    fresh.add(category)
    fresh.flush()
    item = Item(name="LiteBeam", category_id=category.id, on_order=Decimal(5))
    fresh.add(item)
    fresh.flush()
    del fresh.info[LOCK_INFO]  # As in a transaction that never took it.
    item.on_order = Decimal(2)
    fresh.flush()
    item.name = "LiteBeam 5AC"
    with pytest.raises(RuntimeError, match="catalogue lock"):
        fresh.flush()
