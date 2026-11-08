"""Each catalogue table is written by one module, its owner, whose domain
functions take the catalogue lock and check the rules (db.locked_row).

Two checks hold that: the app refuses a flush that writes a catalogue row
without the catalogue lock (db.LOCK_INFO), tested here on PostgreSQL; and
the first test below reads the app's source with Python's ast module
(nothing of it runs in the app) and fails on a catalogue row made outside
its owner, or a row deleted outside the few functions allowed to. Nothing
in the catalogue is deleted. Statements such as update(Item) are refused
everywhere by test_history.py.
"""

import ast
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from inventory_tng.db import LOCK_INFO, attempt, enforce, lock_catalogue
from inventory_tng.models import Category, Item

from helpers.source import MODELS, SOURCE, offending

# Each catalogue table, and the module that alone makes its rows.
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
}
# The functions that delete a row: an administrator's password, as it is
# reset.
ALLOWED = {SOURCE / "accounts.py": ("_reset_sign_in",)}


def not_owned_writes(tree: ast.Module, path: Path) -> list[int]:
    """The lines that make a catalogue row in a module not its owner, or
    delete any row (session.delete)."""
    module = str(path.relative_to(SOURCE)) if path.is_relative_to(SOURCE) else ""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        match node.func:
            case ast.Name(id=name) if name in MODELS and MODELS[name] in OWNERS:
                if OWNERS[MODELS[name]] != module:
                    found.append(node.lineno)
            case ast.Attribute(attr="delete") if len(node.args) == 1:
                found.append(node.lineno)
    return sorted(found)


@pytest.mark.helper
def test_catalogue_rows_are_made_by_their_owners_only() -> None:
    found = offending(not_owned_writes, ALLOWED)
    assert found == {}, (
        "Make catalogue rows through their owner's domain functions "
        f"(test_write_owners.OWNERS), which check the rules: {found}"
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
