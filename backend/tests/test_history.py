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
import re
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from ipaddress import IPv4Address, IPv4Interface, IPv6Address
from pathlib import Path

import pytest
from sqlalchemy import Executable, delete, exc, text, update
from sqlalchemy.orm import Session

from inventory_tng import devices
from inventory_tng.db import Base, set_actor
from inventory_tng.history import (
    NOT_A_CHANGE,
    SECRET_COLUMNS,
    WITHOUT_HISTORY,
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

from helpers.rows import add, history, refused
from helpers.source import SOURCE, offending, outside

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


ROW: dict[str, object] = {"id": 1, "name": "Radios", "retired_at": None}


def test_insert_update_and_delete_are_recorded() -> None:
    renamed = {**ROW, "name": "Antennas"}
    assert change("category", None, ROW) == Change("insert", None, ROW)
    assert change("category", ROW, renamed) == Change("update", ROW, renamed)
    assert change("category", ROW, None) == Change("delete", ROW, None)


def test_an_update_that_changes_nothing_is_not_history() -> None:
    assert change("category", ROW, dict(ROW)) is None


def test_a_change_to_a_column_that_moves_with_use_alone_is_not_history() -> None:
    seen = {"id": 1, "label": None, "last_seen_at": MOMENT}
    later = {**seen, "last_seen_at": MOMENT + timedelta(hours=1)}
    assert change("device", seen, later) is None
    labelled = {**later, "label": "Store tablet"}
    made = change("device", seen, labelled)
    assert made is not None
    # Recorded with the rest of the row, as it was and is.
    assert made.new_row == {
        **labelled,
        "last_seen_at": json_value(later["last_seen_at"]),
    }
    # The column is skipped for that table only.
    assert change("admin_session", seen, later) is not None


def test_a_secret_changing_is_history_without_the_secret() -> None:
    old = {"id": 1, "password_hash": "$argon2id$x", "changed_at": None}
    new = {**old, "password_hash": "$argon2id$y"}
    assert change("admin_password", old, new) == Change(
        "update", {"id": 1, "changed_at": None}, {"id": 1, "changed_at": None}
    )


# Which tables have history, and the columns named for them.

# The tables that had history triggers before, written out: no table loses
# its history by a slip in WITHOUT_HISTORY, and a new table has history
# unless it is named there.
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
}


def test_every_table_has_history_but_those_named() -> None:
    assert {t for t in Base.metadata.tables if has_history(t)} == HISTORY_TABLES
    assert set(Base.metadata.tables) > WITHOUT_HISTORY


@pytest.mark.parametrize("columns", [SECRET_COLUMNS, NOT_A_CHANGE])
def test_the_columns_named_are_columns_of_tables_with_history(
    columns: dict[str, tuple[str, ...]],
) -> None:
    for table, names in columns.items():
        assert table in HISTORY_TABLES
        assert set(names) <= set(Base.metadata.tables[table].c.keys()), table


# No write skips history.

# Statements that may write to a table with history, by module and
# function, each because history would record nothing of it anyway.
ALLOWED = {
    # Sets last_seen_at alone, which is not history (NOT_A_CHANGE).
    SOURCE / "db.py": ("record_last_seen",),
    # Writes catalogue_history itself.
    SOURCE / "history.py": ("_after_flush",),
}
DML = {"insert", "update", "delete"}
BULK = {"bulk_save_objects", "bulk_insert_mappings", "bulk_update_mappings"}
# Either case: SQL is as often written lower. Prose such as "update the
# item" passes, as "the" is no table.
SQL_WRITE = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|MERGE\s+INTO|TRUNCATE|COPY)\s+\"?(\w+)",
    re.IGNORECASE,
)
MODELS = {m.class_.__name__: m.class_.__tablename__ for m in Base.registry.mappers}


def _has_history(target: ast.expr | None) -> bool:
    """Whether a statement on `target` writes to a table with history: a
    model named outright is looked up; anything else, such as a variable,
    cannot be told, so it counts."""
    if isinstance(target, ast.Name) and target.id in MODELS:
        return has_history(MODELS[target.id])
    return True


def _first(node: ast.Call) -> ast.expr | None:
    return node.args[0] if node.args else None


def _imported(tree: ast.AST) -> tuple[set[str], set[str]]:
    """The names insert, update and delete from SQLAlchemy go by here, and
    the names SQLAlchemy's modules go by."""
    functions: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "sqlalchemy"
        ):
            functions |= {a.asname or a.name for a in node.names if a.name in DML}
        elif isinstance(node, ast.Import):
            modules |= {
                a.asname or a.name.split(".")[0]
                for a in node.names
                if a.name.startswith("sqlalchemy")
            }
    return functions, modules


def _dml_called(node: ast.AST, functions: set[str], modules: set[str]) -> bool:
    """update(Item), or sa.update(Item), however imported."""
    if not isinstance(node, ast.Call):
        return False
    called = node.func
    named = isinstance(called, ast.Name) and called.id in functions
    of_module = (
        isinstance(called, ast.Attribute)
        and called.attr in DML
        and isinstance(called.value, ast.Name)
        and called.value.id in modules
    )
    return (named or of_module) and _has_history(_first(node))


def _table_dml(node: ast.AST) -> bool:
    """Item.__table__.update()."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in DML
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "__table__"
        and _has_history(node.func.value.value)
    )


def _query_dml(node: ast.AST) -> bool:
    """session.query(Item)...update(): the legacy Query's statements."""
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in DML
    ):
        return False
    owner: ast.expr = node.func.value
    while isinstance(owner, ast.Call | ast.Attribute):
        if isinstance(owner, ast.Call):
            if isinstance(owner.func, ast.Attribute) and owner.func.attr == "query":
                return True
            owner = owner.func
        else:
            owner = owner.value
    return False


def _bulk(node: ast.AST) -> bool:
    """session.bulk_save_objects() and the like."""
    return isinstance(node, ast.Attribute) and node.attr in BULK


def _sql_write(node: ast.AST) -> bool:
    """SQL in a string that writes to a table with history."""
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and any(
            table in Base.metadata.tables and has_history(table)
            for table in SQL_WRITE.findall(node.value)
        )
    )


def statement_writes(tree: ast.AST) -> list[int]:
    """Lines that write to a table with history other than through the
    flush."""
    functions, modules = _imported(tree)
    return sorted(
        {
            getattr(node, "lineno", 0)
            for node in ast.walk(tree)
            if any(
                (
                    _dml_called(node, functions, modules),
                    _table_dml(node),
                    _query_dml(node),
                    _bulk(node),
                    _sql_write(node),
                )
            )
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
        ("update(Item).values(name='x')", True),
        ("remove(Device)", True),
        ("pg_insert(Admin)", True),
        ("sa.update(model)", True),
        ("Item.__table__.delete()", True),
        ("session.query(Item).filter_by(id=1).update({'name': 'x'})", True),
        ("session.bulk_save_objects(rows)", True),
        ("text('UPDATE item SET name = 1')", True),
        ("'DELETE FROM reset_link'", True),
        ("'update item set name = 1'", True),
        # Tables without history, prose, and what only shares a name.
        ("update(LedgerTransaction)", False),
        ("pg_insert(SentForm)", False),
        ("Movement.__table__.insert()", False),
        ("text('INSERT INTO movement VALUES (1)')", False),
        ("'update the item'", False),
        ("settings.update(fields)", False),
        ("session.delete(row)", False),
    ],
)
def test_the_check_sees_each_way_of_writing(line: str, found: bool) -> None:
    """So the test above cannot pass by missing one."""
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


def test_history_is_in_python_not_the_database(session: Session) -> None:
    triggers = session.scalars(
        text("SELECT tgname FROM pg_trigger WHERE tgname LIKE 'record_history%'")
    ).all()
    functions = session.scalars(
        text("SELECT proname FROM pg_proc WHERE proname = 'record_catalogue_history'")
    ).all()
    assert (triggers, functions) == ([], [])


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
