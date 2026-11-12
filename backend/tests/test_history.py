"""History in Python (history.py; inventory-tng-8i4.97).

What history records is decided by pure functions, tested first without a
database. The rest is on PostgreSQL: rows written in the same transaction,
as to_jsonb() wrote them before, and only ever added to.

The app's source is read with Python's ast module (in the tests only, as
tests/test_locking.py does) for statements that write to a table with
history, which skip the flush and so its history. A check of the source
rather than of statements as they run covers code no test reaches, and
leaves tests free to write with statements on purpose.
"""

import ast
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from ipaddress import IPv4Address, IPv4Interface, IPv6Address
from pathlib import Path

import pytest
from sqlalchemy import Executable, delete, exc, text, update
from sqlalchemy.orm import Session

from inventory_tng import devices
from inventory_tng.db import Base, Flag, set_actor
from inventory_tng.history import (
    Change,
    change,
    has_history,
    json_value,
)
from inventory_tng.models import (
    Admin,
    AdminPassword,
    CatalogueHistory,
    Category,
    Item,
)

from helpers.rows import add, flagged_columns, history, refused
from helpers.source import SOURCE, How, Write, offending, outside, writes

# The pure functions.

MOMENT = datetime(2026, 11, 8, 12, 30, 5, tzinfo=UTC)


@pytest.mark.parametrize(
    ("value", "written"),
    [
        (None, None),
        (True, True),
        (7, 7),
        ("Store tablet", "Store tablet"),
        (Decimal("5.000"), 5.0),
        (Decimal("123456789.125"), 123456789.125),
        (MOMENT, "2026-11-08T12:30:05+00:00"),
        (MOMENT.replace(microsecond=120000), "2026-11-08T12:30:05.12+00:00"),
        (MOMENT.replace(microsecond=7), "2026-11-08T12:30:05.000007+00:00"),
        (
            MOMENT.astimezone(timezone(timedelta(hours=-5))),
            "2026-11-08T12:30:05+00:00",
        ),
        (b"\x00\xab", "\\x00ab"),
        (IPv4Address("192.0.2.1"), "192.0.2.1"),
        (IPv4Interface("192.0.2.0/24"), "192.0.2.0/24"),
        (IPv6Address("2001:db8::1"), "2001:db8::1"),
    ],
)
def test_values_are_written_as_to_jsonb_wrote_them(
    value: object, written: object
) -> None:
    assert json_value(value) == written


def test_a_value_history_cannot_write_is_refused() -> None:
    """Rather than written some other way: a new column type needs a case."""
    with pytest.raises(TypeError, match="complex"):
        json_value(1j)


TABLES = Base.metadata.tables
ROW: dict[str, object] = {"id": 1, "name": "Radios", "retired_at": None}


def test_insert_update_and_delete_are_recorded() -> None:
    renamed = {**ROW, "name": "Antennas"}
    assert change(TABLES["category"], None, ROW) == Change("insert", None, ROW)
    assert change(TABLES["category"], ROW, renamed) == Change("update", ROW, renamed)
    assert change(TABLES["category"], ROW, None) == Change("delete", ROW, None)


def test_an_update_that_changes_nothing_is_not_history() -> None:
    assert change(TABLES["category"], ROW, dict(ROW)) is None


def test_a_change_to_a_column_that_moves_with_use_alone_is_not_history() -> None:
    seen = {"id": 1, "label": None, "last_seen_at": MOMENT}
    later = {**seen, "last_seen_at": MOMENT + timedelta(hours=1)}
    assert change(TABLES["device"], seen, later) is None
    labelled = {**later, "label": "Store tablet"}
    made = change(TABLES["device"], seen, labelled)
    assert made is not None
    # Recorded with the rest of the row, as it was and is.
    assert made.new_row == {
        **labelled,
        "last_seen_at": json_value(later["last_seen_at"]),
    }
    # The column is skipped for that table only.
    assert change(TABLES["admin_session"], seen, later) is not None


def test_a_secret_changing_is_history_without_the_secret() -> None:
    old = {"id": 1, "password_hash": "$argon2id$x", "changed_at": None}
    new = {**old, "password_hash": "$argon2id$y"}
    assert change(TABLES["admin_password"], old, new) == Change(
        "update", {"id": 1, "changed_at": None}, {"id": 1, "changed_at": None}
    )


# Which tables have history, and the columns named for them.

# The tables with history, written out: no table loses its history by a
# slip in its declaration (db.protection).
HISTORY_TABLES = {
    "category",
    "item",
    "item_package",
    "vendor",
    "vendor_offer",
    "vendor_price",
    "volunteer",
    "location",
    "label",
    "registration_code",
    "device",
    "scan_settings",
    "admin",
    "sign_in_settings",
    "admin_password",
    "authenticator",
    "recovery_code",
    "reset_link",
    "site_settings",
    "adjustment_reason",
    "open_package",
}


def test_every_table_has_history_but_those_named() -> None:
    assert {t.name for t in TABLES.values() if has_history(t)} == HISTORY_TABLES


VERSIONED = (
    "adjustment_reason",
    "category",
    "item",
    "item_package",
    "location",
    "scan_settings",
    "sign_in_settings",
    "site_settings",
    "volunteer",
)


@pytest.mark.parametrize(
    ("flag", "columns"),
    [
        (
            "secret",
            {
                "admin_password": {"password_hash"},
                "authenticator": {"sealed_key"},
                "recovery_code": {"code_hash"},
                "reset_link": {"token_hash", "recovery_token_hash"},
            },
        ),
        (
            "not_a_change",
            {
                "device": {"last_seen_at", "edit_version"},
                # Resealing (totp_keys.py) changes nothing anyone did.
                "authenticator": {"last_step", "sealed_key", "key_version"},
            }
            # Each edit form's row's (models.Versioned), which moves with
            # every change.
            | {table: {"edit_version"} for table in VERSIONED},
        ),
        ("row_lock_only", {"item": {"on_order"}}),
    ],
)
def test_the_flagged_columns_are_on_tables_with_history(
    flag: Flag, columns: dict[str, set[str]]
) -> None:
    assert flagged_columns(flag) == columns
    assert set(columns) <= HISTORY_TABLES


# No write skips history.

# Statements that may write to a table with history, by module and
# function, each because history would record nothing of it anyway.
ALLOWED = {
    # Sets last_seen_at alone, which is not history ("not_a_change").
    SOURCE / "db.py": ("record_last_seen",),
    # Writes catalogue_history itself.
    SOURCE / "history.py": ("_after_flush",),
}


def statement_writes(tree: ast.AST, _path: Path | None = None) -> list[int]:
    """Lines that write to a table with history other than through the
    flush: a statement on such a table, or on one the source cannot
    tell."""
    return sorted(
        {
            write.line
            for write in writes(tree)
            if write.how == "statement"
            and (write.table is None or has_history(TABLES[write.table]))
        }
    )


@pytest.mark.helper
def test_every_write_to_a_table_with_history_goes_through_the_flush() -> None:
    found = offending(statement_writes, ALLOWED)
    assert found == {}, (
        "Write tables with history through the ORM (session.add, an object's "
        "attributes, session.delete), not statements, which history never "
        f"sees (history.py): {found}"
    )


IMPORTS = (
    "from sqlalchemy import update, delete as remove\n"
    "from sqlalchemy.dialects.postgresql import insert as pg_insert\n"
    "import sqlalchemy as sa\n"
)


@pytest.mark.helper
@pytest.mark.parametrize(
    ("line", "found"),
    [
        # Statements, however written.
        ("update(Item).values(name='x')", ("item", "statement")),
        ("remove(Device)", ("device", "statement")),
        ("pg_insert(Admin)", ("admin", "statement")),
        ("sa.update(model)", (None, "statement")),
        ("sa.insert(models.Movement)", ("movement", "statement")),
        ("Item.__table__.delete()", ("item", "statement")),
        ("remove(LedgerTransaction.__table__)", ("ledger_transaction", "statement")),
        (
            "session.query(Item).filter_by(id=1).update({'name': 'x'})",
            ("item", "statement"),
        ),
        ("session.bulk_save_objects(rows)", (None, "statement")),
        ("text('UPDATE item SET name = 1')", ("item", "statement")),
        ("'DELETE FROM reset_link'", ("reset_link", "statement")),
        ("'update item set name = 1'", ("item", "statement")),
        ("text('INSERT INTO movement VALUES (1)')", ("movement", "statement")),
        # Rows made and deleted through the ORM.
        ("Movement(item_id=1)", ("movement", "row")),
        ("models.LedgerTransaction(kind='move')", ("ledger_transaction", "row")),
        ("session.delete(row)", (None, "delete")),
        # Prose, reads, and what only shares a name.
        ("'update the item'", None),
        ("def f():\n    'Refuses UPDATE item.'", None),
        ("settings.update(fields)", None),
        ("select(Movement)", None),
        ("Item.id.in_(ids)", None),
    ],
)
def test_the_detector_sees_each_way_of_writing(
    line: str, found: tuple[str | None, How] | None
) -> None:
    """helpers.source.writes, which this file's check and
    test_write_owners.py's share, so neither can pass by missing one."""
    expected = [] if found is None else [Write(4, *found)]
    assert writes(ast.parse(IMPORTS + line)) == expected


@pytest.mark.helper
@pytest.mark.parametrize(
    ("line", "found"),
    [
        ("update(Item)", True),
        ("sa.update(model)", True),
        ("update(LedgerTransaction)", False),
        ("pg_insert(SentForm)", False),
        ("Item(name='x')", False),
        ("session.delete(row)", False),
    ],
)
def test_only_statements_on_tables_with_history_count(line: str, found: bool) -> None:
    """A statement on a table without history, or a write through the
    flush, is not this check's."""
    assert statement_writes(ast.parse(IMPORTS + line)) == ([4] if found else [])


@pytest.mark.helper
def test_the_allowed_writes_are_only_in_their_own_functions(tmp_path: Path) -> None:
    db = tmp_path / "db.py"
    db.write_text(
        "from sqlalchemy import update\n"
        "def record_last_seen(model):\n"
        "    update(model)\n"
        "def other(model):\n"
        "    update(model)\n"
        "    def record_last_seen(model):\n"
        "        update(model)\n"
    )
    # Not in another function, nor a nested one of that name...
    assert outside(db, statement_writes, {db: ("record_last_seen",)}) == [5, 7]
    # ...nor in another file of that name.
    assert outside(db, statement_writes, ALLOWED) == [3, 5, 7]


# On PostgreSQL.


@pytest.fixture
def item(session: Session) -> Item:
    radios = add(session, Category(name="Radios"))
    return add(
        session,
        Item(
            name="LiteBeam",
            manufacturer="Ubiquiti",
            category_id=radios.id,
            on_order=Decimal("2.500"),
        ),
    )


def as_to_jsonb(session: Session, table: str, row_id: int) -> object:
    return session.scalar(
        text(f"SELECT to_jsonb(t) FROM {table} t WHERE id = :id"), {"id": row_id}
    )


def test_rows_are_recorded_as_to_jsonb_would(session: Session, item: Item) -> None:
    """Numbers, times, the column PostgreSQL computes (search_text), bytes
    and an address: as the database's own to_jsonb() writes the row now."""
    code, phrase = devices.create_code(session, max_uses=1, lifetime=timedelta(days=1))
    device, _ = devices.register(session, phrase, "2001:db8::1")
    session.flush()
    for table, row_id in (
        ("item", item.id),
        ("device", device.id),
        ("registration_code", code.id),
    ):
        (*_, last) = history(session, table, row_id)
        assert last.new_row == as_to_jsonb(session, table, row_id), table


def test_an_update_records_the_row_before_even_when_not_loaded(
    session: Session, item: Item
) -> None:
    set_actor(session, "admin:4")
    session.expire(item)
    item.notes = "by the door"
    session.flush()
    *_, changed = history(session, "item", item.id)
    assert (changed.action, changed.actor) == ("update", "admin:4")
    assert changed.old_row is not None
    assert changed.new_row is not None
    assert (changed.old_row["notes"], changed.new_row["notes"]) == (None, "by the door")
    assert changed.old_row["on_order"] == 2.5


@pytest.mark.parametrize(
    ("password", "actions"),
    [("$argon2id$new", ["insert", "update"]), ("$argon2id$old", ["insert"])],
    ids=["changed", "the same"],
)
def test_a_row_deleted_and_added_again_in_one_flush_is_an_update(
    session: Session, password: str, actions: list[str]
) -> None:
    """SQLAlchemy writes it as one UPDATE, so history says so: or nothing,
    if the row is as it was."""
    admin = add(session, Admin(username="ada"))
    old = add(session, AdminPassword(id=admin.id, password_hash="$argon2id$old"))
    session.delete(old)
    session.add(AdminPassword(id=admin.id, password_hash=password))
    session.flush()
    assert [
        row.action for row in history(session, "admin_password", admin.id)
    ] == actions


def test_deleting_a_row_already_gone_is_no_history(session: Session) -> None:
    category = add(session, Category(name="Gone already"))
    row_id = category.id
    # A statement the session does not see, as another request might.
    categories = Base.metadata.tables["category"]
    session.execute(delete(categories).where(categories.c.id == row_id))
    session.delete(category)
    with pytest.warns(exc.SAWarning, match="0 were matched"):
        session.flush()
    assert [row.action for row in history(session, "category", row_id)] == ["insert"]


def test_a_delete_records_the_row_as_it_was(session: Session) -> None:
    category = add(session, Category(name="Gone soon"))
    row_id = category.id
    session.delete(category)
    session.flush()
    actions = [
        (row.action, row.old_row, row.new_row)
        for row in history(session, "category", row_id)
    ]
    assert [a for a, *_ in actions] == ["insert", "delete"]
    *_, (_, old, new) = actions
    assert new is None
    assert old is not None
    assert old["name"] == "Gone soon"


def test_no_row_is_null_not_json_null(session: Session) -> None:
    """As a query for inserts (old_row IS NULL) expects."""
    category = add(session, Category(name="Nulls"))
    session.delete(category)
    session.flush()
    nulls = session.execute(
        text(
            "SELECT action, old_row IS NULL, new_row IS NULL FROM catalogue_history"
            " WHERE table_name = 'category' AND row_id = :id ORDER BY id"
        ),
        {"id": category.id},
    ).all()
    assert [tuple(row) for row in nulls] == [
        ("insert", True, False),
        ("delete", False, True),
    ]


def test_the_actor_lasts_for_the_session(session: Session) -> None:
    set_actor(session, "system:test")
    session.commit()
    category = add(session, Category(name="After a commit"))
    (added,) = history(session, "category", category.id)
    assert added.actor == "system:test"


@pytest.mark.parametrize(
    "rewrite",
    [update(CatalogueHistory).values(actor=None), delete(CatalogueHistory)],
    ids=["update", "delete"],
)
def test_the_app_may_not_rewrite_history(session: Session, rewrite: Executable) -> None:
    """Its login lacks the privileges (migration 0002)."""
    add(session, Category(name="Kept"))
    with refused(session):
        session.execute(rewrite)
