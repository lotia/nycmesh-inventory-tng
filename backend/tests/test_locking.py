"""Locking a row (db.locked, db.get_locked; inventory-tng-8i4.57): every
locking read in the app goes through them, which read a row the session had
already loaded again as locked (db.py says why).

The first test reads the app's source with Python's ast module, in the test
suite only (nothing of it runs in the app), and fails on with_for_update, or
a lock written in SQL, anywhere but in db.py: Ruff's banned-api rule cannot,
as it matches names written out (Select.with_for_update), not methods called
on an object (query.with_for_update()).
"""

import ast
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import devices
from inventory_tng.admin.categories import CATEGORIES
from inventory_tng.admin.items import ITEMS
from inventory_tng.admin.locations import LOCATIONS
from inventory_tng.admin.settings import SETTINGS
from inventory_tng.admin.volunteers import VOLUNTEERS
from inventory_tng.db import (
    Base,
    get_locked,
    locked,
)
from inventory_tng.models import (
    AdjustmentReason,
    Category,
    Item,
    ItemPackage,
    Location,
    RegistrationCode,
    Volunteer,
)

from helpers.concurrency import Committed, made
from helpers.sign_in import code, new_admin, sign_in_with
from helpers.source import SOURCE, offending, outside

# The only code that may lock with with_for_update: the wrappers' bodies.
WRAPPERS = ("locked", "get_locked")
DB = SOURCE / "db.py"
# A row lock written in SQL: FOR UPDATE, FOR NO KEY UPDATE, FOR SHARE,
# FOR KEY SHARE.
SQL_LOCK = re.compile(
    r"\bFOR\s+(NO\s+KEY\s+UPDATE|UPDATE|KEY\s+SHARE|SHARE)\b", re.IGNORECASE
)


def lockings(tree: ast.AST, _path: Path | None = None) -> list[int]:
    """The lines in `tree` that lock a row without the wrappers:
    - a method named with_for_update called (query.with_for_update());
    - a keyword argument by that name (Session.get's, Session.refresh's);
    - Session.refresh given its lock positionally (its third argument);
    - the name as a string, as getattr(query, "with_for_update") or
      **{"with_for_update": True} would need;
    - a lock clause in SQL passed to text().
    Comments, and strings that only mention it in passing, do not count.
    Arguments unpacked from a variable (**options) cannot be seen, so the
    rule in CONTRIBUTING.md still applies to them."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "with_for_update":
            found.append(node.lineno)
        elif isinstance(node, ast.keyword) and node.arg == "with_for_update":
            found.append(node.value.lineno)
        elif isinstance(node, ast.Constant) and node.value == "with_for_update":
            found.append(node.lineno)
        elif isinstance(node, ast.Call):
            called = node.func
            name = (
                called.attr
                if isinstance(called, ast.Attribute)
                else (called.id if isinstance(called, ast.Name) else None)
            )
            if (name == "refresh" and len(node.args) >= 3) or (
                name == "text"
                and any(
                    isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and SQL_LOCK.search(arg.value)
                    for arg in node.args
                )
            ):
                found.append(node.lineno)
    return sorted(set(found))


def outside_the_wrappers(path: Path, wrappers_in: Path = DB) -> list[int]:
    """lockings in `path`, but in the wrappers' own module (db.py, the one
    file, not any of that name) not those inside the wrappers."""
    return outside(path, lockings, {wrappers_in: WRAPPERS})


@pytest.mark.helper
def test_every_locking_read_goes_through_db() -> None:
    found = offending(lockings, {DB: WRAPPERS})
    assert found == {}, (
        "Lock rows with db.locked (a select) or db.get_locked (Session.get), "
        f"not with_for_update or FOR UPDATE in SQL (db.py says why): {found}"
    )


@pytest.mark.helper
def test_the_check_sees_each_way_of_locking(tmp_path: Path) -> None:
    """So the test above cannot pass by missing them; and the wrappers
    themselves are allowed, the rest of db.py is not."""
    code = tmp_path / "locks.py"
    code.write_text(
        "query.with_for_update()\n"
        "session.get(Model, 1, with_for_update=True)\n"
        "session.refresh(row, None, {'key_share': True})\n"
        "getattr(query, 'with_for_update')()\n"
        "session.get(Model, 1, **{'with_for_update': True})\n"
        "session.execute(text('SELECT id FROM item FOR NO KEY UPDATE'))\n"
        "# with_for_update in a comment\n"
        "'a string that mentions with_for_update in passing'\n"
        "session.refresh(row)\n"
        "text('SELECT 1')\n"
    )
    assert outside_the_wrappers(code) == [1, 2, 3, 4, 5, 6]
    db = tmp_path / "db.py"
    db.write_text(
        "def locked(query):\n"
        "    return query.with_for_update()\n"
        "def other(query):\n"
        "    return query.with_for_update()\n"
    )
    assert outside_the_wrappers(db, wrappers_in=db) == [4]
    # Another file of that name is no exception.
    assert outside_the_wrappers(db) == [2, 4]


def changed(
    committed: Committed, model: type[Base], row_id: int, **values: object
) -> None:
    """The row changed from another connection, committed."""
    other = committed.session()
    row = other.get_one(model, row_id)
    for field, value in values.items():
        setattr(row, field, value)
    other.commit()


@pytest.fixture
def cables(committed: Committed) -> int:
    """A category's id, committed."""
    category = Category(name="Cables")
    made(committed, category)
    return category.id


@pytest.mark.helper
@pytest.mark.parametrize(
    ("read", "name"),
    [
        ("a plain locking get", "Cables"),
        ("a plain locking select", "Cables"),
        ("get_locked", "Leads"),
        ("locked", "Leads"),
    ],
)
def test_what_a_locking_read_returns_for_a_row_loaded_before_it(
    committed: Committed, cables: int, read: str, name: str
) -> None:
    """Why the wrappers: a plain locking read takes the lock but keeps the
    object as first loaded; each wrapper reads it again."""
    first = committed.session()
    loaded = first.get_one(Category, cables)
    changed(committed, Category, cables, name="Leads")
    query = select(Category).where(Category.id == cables)
    again = {
        "a plain locking get": lambda: first.get(
            Category, cables, with_for_update=True
        ),
        "a plain locking select": lambda: first.scalar(query.with_for_update()),
        "get_locked": lambda: get_locked(first, Category, cables),
        "locked": lambda: first.scalar(locked(query)),
    }[read]()
    assert again is loaded
    assert loaded.name == name
    first.rollback()


# Each site: what a future change would do (load the row in the request's
# session before the route locks it) is done by the test, then another
# connection changes the row and commits, then the route runs. Through
# db.get_locked (or db.relock, which a catalogue page's domain function
# takes) it sees the change; with a plain locking read it would act on the
# row as first loaded, which these tests fail on. Sites whose outcome does
# not depend on the row's earlier values have none: saving an item, a
# volunteer, a location or a category's name (each writes what the form
# says), adding a package and ending custody (they only need the row to be
# there), and the recovery code a sign-in uses (PostgreSQL re-checks its
# WHERE on the locked row). Merging locks as retiring does
# (volunteers._lock), tested here.


@pytest.fixture
def primed(
    committed: Committed, make_client: Callable[..., TestClient]
) -> tuple[TestClient, Session]:
    """A signed-in client whose requests use one committed session the test
    also holds: what the test loads into it, a request finds loaded."""
    session = committed.session()
    client = make_client(on=session)
    _, key = new_admin(session, "primer")
    session.commit()
    assert sign_in_with(client, "primer", code=code(key)).status_code == 303
    return client, session


@pytest.mark.end_to_end
@pytest.mark.parametrize(
    ("what", "path"),
    [
        ("item", f"{ITEMS}/{{id}}/retire"),
        ("category", f"{CATEGORIES}/{{id}}/retire"),
        ("volunteer", f"{VOLUNTEERS}/{{id}}/retire"),
        ("location", f"{LOCATIONS}/{{id}}/retire"),
        ("package", f"{ITEMS}/{{item}}/packages/{{id}}/retire"),
    ],
)
def test_retiring_what_was_retired_meanwhile_is_not_found(
    committed: Committed, primed: tuple[TestClient, Session], what: str, path: str
) -> None:
    """Its retired_at, as the request first loaded it, said current."""
    client, session = primed
    category = Category(name="Cables")
    made(committed, category)
    item = Item(name="Patch lead", category_id=category.id)
    made(committed, item)
    row: Base = {
        "item": item,
        "category": Category(name="Spare"),
        "volunteer": Volunteer(name="Pat"),
        "location": Location(name="Shelf", kind="store"),
        "package": ItemPackage(item_id=item.id, name="Box", size=Decimal(10)),
    }[what]
    if row is not item:
        made(committed, row)
    model, row_id = type(row), row.id
    loaded = session.get_one(model, row_id)
    changed(committed, model, row_id, retired_at=datetime.now(UTC))
    url = path.format(id=row_id, item=item.id)
    assert client.post(url).status_code == 404
    assert loaded.retired_at is not None


@pytest.mark.end_to_end
@pytest.mark.parametrize(
    ("what", "path"),
    [
        ("item", f"{ITEMS}/{{id}}/restore"),
        ("category", f"{CATEGORIES}/{{id}}/restore"),
        ("volunteer", f"{VOLUNTEERS}/{{id}}/restore"),
        ("location", f"{LOCATIONS}/{{id}}/restore"),
    ],
)
def test_bringing_back_what_was_brought_back_meanwhile_is_not_found(
    committed: Committed, primed: tuple[TestClient, Session], what: str, path: str
) -> None:
    """Its retired_at, as the request first loaded it, said retired."""
    client, session = primed
    category = Category(name="Cables")
    made(committed, category)
    retired = datetime.now(UTC)
    row: Base = {
        "item": Item(name="Patch lead", category_id=category.id, retired_at=retired),
        "category": Category(name="Spare", retired_at=retired),
        "volunteer": Volunteer(name="Pat", retired_at=retired),
        "location": Location(name="Shelf", kind="store", retired_at=retired),
    }[what]
    made(committed, row)
    model, row_id = type(row), row.id
    loaded = session.get_one(model, row_id)
    changed(committed, model, row_id, retired_at=None)
    url = path.format(id=row_id)
    assert client.post(url).status_code == 404
    assert loaded.retired_at is None


@pytest.mark.helper
def test_a_code_used_up_meanwhile_registers_no_device(committed: Committed) -> None:
    """Its uses, as the request first loaded them, left one: the count read
    again under the lock says none, so two phones never share a last use."""
    setup = committed.session()
    code_row, phrase = devices.create_code(
        setup, max_uses=1, lifetime=timedelta(days=1)
    )
    setup.commit()
    request = committed.session()
    loaded = request.get_one(RegistrationCode, code_row.id)
    changed(committed, RegistrationCode, code_row.id, uses=1)
    with pytest.raises(devices.RegistrationRejected) as rejected:
        devices.register(request, phrase, None)
    assert rejected.value.reason == "used_up"
    assert loaded.uses == 1
    request.rollback()


@pytest.mark.end_to_end
def test_custody_started_after_a_rename_takes_the_new_name(
    committed: Committed, primed: tuple[TestClient, Session]
) -> None:
    """Holding stock names the volunteer's custody after them: their name
    as the request first loaded it would be the old one."""
    client, session = primed
    volunteer = Volunteer(name="Pat")
    made(committed, volunteer)
    # Kept, as the request would keep it: the session holds only what
    # something refers to.
    loaded = session.get_one(Volunteer, volunteer.id)
    changed(committed, Volunteer, volunteer.id, name="Patricia")
    assert client.post(f"{VOLUNTEERS}/{volunteer.id}/custody").status_code == 303
    assert loaded.name == "Patricia"
    custody = (
        committed.session()
        .scalars(select(Location).where(Location.volunteer_id == volunteer.id))
        .one()
    )
    assert custody.name == "Patricia"


@pytest.mark.end_to_end
def test_a_reason_renamed_meanwhile_keeps_it(
    committed: Committed, primed: tuple[TestClient, Session]
) -> None:
    """With no name sent, the reason keeps the name it has, not the one the
    request first loaded."""
    client, session = primed
    reason = AdjustmentReason(name="Dropped in the river")
    made(committed, reason)
    loaded = session.get_one(AdjustmentReason, reason.id)
    changed(committed, AdjustmentReason, reason.id, name="Soaked")
    sent = client.post(f"{SETTINGS}/reasons/{reason.id}", data={"name": ""})
    assert sent.status_code == 303
    assert committed.session().get_one(AdjustmentReason, reason.id).name == "Soaked"
    assert loaded.name == "Soaked"
