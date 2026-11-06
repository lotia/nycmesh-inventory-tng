"""The database's own rules (models.py, migration 0002). Each failing write is
checked by the constraint it names, which is what describe_error() logs."""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, delete, exc, select, text, update
from sqlalchemy.orm import Session

from inventory_tng.db import Base, describe_error, refusal, set_actor
from inventory_tng.models import (
    LABEL_ALPHABET,
    LABEL_LENGTH,
    CatalogueHistory,
    Category,
    Item,
    ItemPackage,
    Label,
    Location,
    Volunteer,
    mint_code,
    mint_label_code,
)

NOW = datetime(2026, 11, 1, tzinfo=UTC)


@contextmanager
def violates(session: Session, constraint: str) -> Generator[None]:
    """The block's writes fail on `constraint`, and are rolled back to a
    savepoint, so the test can go on."""
    # The block is the caller's, so it cannot be one statement.
    with pytest.raises(exc.IntegrityError) as raised, session.begin_nested():  # noqa: PT012
        yield
        session.flush()
    assert describe_error(raised.value).get("constraint") == constraint


def add[T: Base](session: Session, row: T) -> T:
    session.add(row)
    session.flush()
    return row


def named[T: Base](session: Session, model: type[T], name: str) -> T:
    """The one row of `model` with this name."""
    table: Any = model
    return session.scalars(select(model).where(table.name == name)).one()


def change(session: Session, row: Base, **values: object) -> None:
    """An UPDATE straight to the database, as a trigger sees it."""
    model = type(row)
    session.execute(update(model).where(model.id == row.id).values(**values))  # ty: ignore[unresolved-attribute]


def history(
    session: Session, table: str, row_id: int | None = None
) -> list[CatalogueHistory]:
    """A table's history, or one row's, oldest first."""
    query = select(CatalogueHistory).where(CatalogueHistory.table_name == table)
    if row_id is not None:
        query = query.where(CatalogueHistory.row_id == row_id)
    return list(session.scalars(query.order_by(CatalogueHistory.id)))


def merge(session: Session, duplicate: Volunteer, into: Volunteer) -> None:
    change(session, duplicate, merged_into_id=into.id, retired_at=NOW)


@pytest.fixture
def category(session: Session) -> Category:
    return add(session, Category(name="Radios"))


@pytest.fixture
def new_item(session: Session, category: Category) -> Callable[..., Item]:
    def make(name: str = "LiteBeam 5AC", **fields: object) -> Item:
        return add(session, Item(name=name, category_id=category.id, **fields))

    return make


@pytest.fixture
def store(session: Session) -> Location:
    return add(session, Location(name="Main store", kind="store"))


def test_names_are_unique_ignoring_case_among_current_rows(
    session: Session, category: Category
) -> None:
    with violates(session, "uq_category_name"):
        session.add(Category(name="RADIOS"))
    category.retired_at = NOW
    session.flush()
    add(session, Category(name="radios"))


def test_location_names_are_unique_among_siblings(
    session: Session, store: Location
) -> None:
    other = add(session, Location(name="Hub store", kind="store"))
    add(session, Location(name="Shelf A", kind="area", parent_id=store.id))
    add(session, Location(name="Shelf A", kind="area", parent_id=other.id))
    with violates(session, "uq_location_name"):
        session.add(Location(name="shelf a", kind="area", parent_id=store.id))


def test_identifiers_are_unique(
    new_item: Callable[..., Item], session: Session
) -> None:
    new_item(manufacturer="Ubiquiti", part_number="LBE-5AC-Gen2", old_code="NYCM-1")
    with violates(session, "uq_item_manufacturer_part_number"):
        new_item("Other", manufacturer="ubiquiti", part_number="lbe-5ac-gen2")
    with violates(session, "uq_item_old_code"):
        new_item("Another", old_code="NYCM-1")
    # Either half alone is not unique.
    new_item("Third", manufacturer="Ubiquiti")
    new_item("Fourth", part_number="LBE-5AC-Gen2")


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"unit": "kg"}, "ck_item_unit"),
        ({"minimum": Decimal(-1)}, "ck_item_minimum"),
        ({"on_order": Decimal(-1)}, "ck_item_on_order"),
    ],
)
def test_item_checks(
    new_item: Callable[..., Item],
    session: Session,
    fields: dict[str, object],
    constraint: str,
) -> None:
    with violates(session, constraint):
        new_item(**fields)


def test_unit_defaults_to_each(new_item: Callable[..., Item], session: Session) -> None:
    item = new_item()
    session.refresh(item)
    assert item.unit == "each"


def test_nothing_is_deleted_out_from_under_a_reference(
    new_item: Callable[..., Item], session: Session, category: Category
) -> None:
    new_item()
    with violates(session, "fk_item_category_id_category"):
        session.execute(delete(Category).where(Category.id == category.id))


def test_codes_of_any_length_come_from_the_alphabet() -> None:
    code = mint_code(40)
    assert len(code) == 40
    assert set(code) <= set(LABEL_ALPHABET)


def test_minted_codes_pass_the_check(session: Session, store: Location) -> None:
    code = mint_label_code()
    assert len(code) == LABEL_LENGTH
    assert set(code) <= set(LABEL_ALPHABET)
    label = add(session, Label(location_id=store.id))
    assert len(label.code) == LABEL_LENGTH
    with violates(session, "ck_label_code"):
        session.add(Label(code="O0I1L" * 2, location_id=store.id))


def test_a_label_names_an_item_or_a_location(
    session: Session, new_item: Callable[..., Item], store: Location
) -> None:
    item = new_item()
    with violates(session, "ck_label_item_or_location"):
        session.add(Label())
    with violates(session, "ck_label_item_or_location"):
        session.add(Label(item_id=item.id, location_id=store.id))


def test_a_label_code_never_changes(session: Session, store: Location) -> None:
    label = add(session, Label(location_id=store.id))
    with violates(session, "label_code_immutable"):
        session.execute(
            update(Label).where(Label.id == label.id).values(code=mint_label_code())
        )


def test_a_labels_package_belongs_to_its_item(
    session: Session, new_item: Callable[..., Item], store: Location
) -> None:
    cable, radio = new_item("Cable"), new_item("Radio")
    reel = add(session, ItemPackage(item_id=cable.id, name="reel", size=Decimal(305)))
    with violates(session, "ck_label_package_needs_item"):
        session.add(Label(location_id=store.id, package_id=reel.id))
    with violates(session, "label_package_item"):
        session.add(Label(item_id=radio.id, package_id=reel.id))
    add(session, Label(item_id=cable.id, package_id=reel.id))
    with violates(session, "label_package_item"):
        change(session, reel, item_id=radio.id)


def test_package_sizes_are_positive(
    session: Session, new_item: Callable[..., Item]
) -> None:
    item = new_item()
    with violates(session, "ck_item_package_size"):
        session.add(ItemPackage(item_id=item.id, name="empty", size=Decimal(0)))


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"kind": "area"}, "ck_location_area_has_parent"),
        ({"kind": "custody"}, "ck_location_custody_has_volunteer"),
        ({"kind": "shed"}, "ck_location_kind"),
    ],
)
def test_location_checks(
    session: Session, fields: dict[str, object], constraint: str
) -> None:
    with violates(session, constraint):
        session.add(Location(name="Somewhere", **fields))


def test_an_areas_parent_is_a_store_or_an_area(
    session: Session, store: Location
) -> None:
    van = add(session, Location(name="Van", kind="vehicle"))
    with violates(session, "location_area_parent"):
        session.add(Location(name="Glovebox", kind="area", parent_id=van.id))
    shelf = add(session, Location(name="Shelf", kind="area", parent_id=store.id))
    add(session, Location(name="Bin", kind="area", parent_id=shelf.id))
    # A store with areas under it stays something that can have them.
    with violates(session, "location_area_parent"):
        change(session, store, kind="vehicle")


def test_the_location_tree_has_no_cycles(session: Session, store: Location) -> None:
    shelf = add(session, Location(name="Shelf", kind="area", parent_id=store.id))
    bin_ = add(session, Location(name="Bin", kind="area", parent_id=shelf.id))
    with violates(session, "location_no_cycle"):
        change(session, shelf, parent_id=bin_.id)
    with violates(session, "location_no_cycle"):
        change(session, shelf, parent_id=shelf.id)


def test_custody_belongs_to_one_current_volunteer(session: Session) -> None:
    alice = add(session, Volunteer(name="Volunteer A"))
    add(session, Location(name="With A", kind="custody", volunteer_id=alice.id))
    with violates(session, "uq_location_custody_volunteer_id"):
        session.add(Location(name="Also with A", kind="custody", volunteer_id=alice.id))
    gone = add(session, Volunteer(name="Volunteer B", retired_at=NOW))
    with violates(session, "location_custody_volunteer"):
        session.add(Location(name="With B", kind="custody", volunteer_id=gone.id))


def test_a_volunteer_holding_stock_stays_current(session: Session) -> None:
    holder = add(session, Volunteer(name="Volunteer A"))
    other = add(session, Volunteer(name="Volunteer B"))
    custody = add(
        session, Location(name="With A", kind="custody", volunteer_id=holder.id)
    )
    with violates(session, "location_custody_volunteer"):
        change(session, holder, retired_at=NOW)
    with violates(session, "location_custody_volunteer"):
        merge(session, holder, other)
    custody.retired_at = NOW
    session.flush()
    merge(session, holder, other)


def test_current_areas_have_current_parents(session: Session, store: Location) -> None:
    shelf = add(session, Location(name="Shelf", kind="area", parent_id=store.id))
    with violates(session, "location_parent_current"):
        change(session, store, retired_at=NOW)
    shelf.retired_at = NOW
    session.flush()
    store.retired_at = NOW
    session.flush()
    with violates(session, "location_parent_current"):
        session.add(Location(name="New shelf", kind="area", parent_id=store.id))


def test_root_locations_of_different_kinds_may_share_a_name(session: Session) -> None:
    alex = add(session, Volunteer(name="Alex"))
    add(session, Location(name="Alex", kind="vehicle"))
    add(session, Location(name="Alex", kind="custody", volunteer_id=alex.id))
    with violates(session, "uq_location_name"):
        session.add(Location(name="ALEX", kind="vehicle"))


@pytest.mark.parametrize("actor", ["Jane Doe", "admin:", "admin:3; DROP", "root:1"])
def test_actors_are_ids(session: Session, actor: str) -> None:
    with pytest.raises(ValueError, match="actor"):
        set_actor(session, actor)


def test_merges_point_at_a_current_volunteer(session: Session) -> None:
    a, b, c = (add(session, Volunteer(name=f"Volunteer {n}")) for n in "ABC")
    with violates(session, "ck_volunteer_not_merged_into_self"):
        merge(session, a, a)
    with violates(session, "ck_volunteer_merged_is_retired"):
        change(session, a, merged_into_id=b.id)
    merge(session, a, b)
    # Not into one that is merged itself...
    with violates(session, "volunteer_merge_target"):
        merge(session, c, a)
    # ...and the target stays current while others point at it.
    with violates(session, "volunteer_merge_target"):
        change(session, b, retired_at=NOW)
    # Point the others onward first, and then it can go.
    merge(session, a, c)
    merge(session, b, c)


def test_current_items_sit_in_current_categories(session: Session) -> None:
    """inventory-tng-8i4.58: whatever writes them."""
    radios = add(session, Category(name="Radios rule"))
    old = add(session, Category(name="Old rule", retired_at=NOW))
    item = add(session, Item(name="LiteBeam rule", category_id=radios.id))
    with violates(session, "item_category_current"):
        session.add(Item(name="Into the old", category_id=old.id))
    with violates(session, "item_category_current"):
        change(session, item, category_id=old.id)
    # A category with a current item stays current...
    with violates(session, "category_has_items"):
        change(session, radios, retired_at=NOW)
    # ...until the item goes, and a retired item cannot come back into it.
    change(session, item, retired_at=NOW)
    change(session, radios, retired_at=NOW)
    with violates(session, "item_category_current"):
        change(session, item, retired_at=None)
    # A retired item may sit in a retired category.
    add(session, Item(name="Retired too", category_id=old.id, retired_at=NOW))


def test_a_rule_a_change_does_not_expect_is_raised(session: Session) -> None:
    """db.refusal: a check the page should have caught first, refused by
    the database, is a bug, raised rather than shown as a vague problem."""
    radios = add(session, Category(name="Radios refused"))
    bad = Item(name="Below nothing", category_id=radios.id, minimum=-1)
    with pytest.raises(ValueError, match="ck_item_minimum"):
        refusal(session, lambda: session.add(bad), "unique")


def test_volunteer_emails_are_unique_ignoring_case(session: Session) -> None:
    add(session, Volunteer(name="Volunteer A", email="a@example.org"))
    with violates(session, "uq_volunteer_email"):
        session.add(Volunteer(name="Volunteer B", email="A@example.org"))


def test_every_change_is_recorded_with_its_actor(
    session: Session, new_item: Callable[..., Item]
) -> None:
    set_actor(session, "admin:3")
    item = new_item()
    item.notes = "on the top shelf"
    session.flush()
    rows = session.scalars(
        select(CatalogueHistory)
        .where(CatalogueHistory.table_name == "item")
        .where(CatalogueHistory.row_id == item.id)
        .order_by(CatalogueHistory.id)
    ).all()
    assert [(row.action, row.actor) for row in rows] == [
        ("insert", "admin:3"),
        ("update", "admin:3"),
    ]
    inserted, updated = rows
    assert inserted.old_row is None
    assert updated.old_row is not None
    assert updated.new_row is not None
    assert (updated.old_row["notes"], updated.new_row["notes"]) == (
        None,
        "on the top shelf",
    )


def test_every_table_records_history(engine: Engine) -> None:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT event_object_table, trigger_name "
                "FROM information_schema.triggers "
                "WHERE trigger_name LIKE 'record_history%'"
            )
        )
        watched = {(table, trigger) for table, trigger in rows}
    assert watched == {
        (table, trigger)
        # The ledger is append-only, so it is its own history; sessions
        # change on every request.
        for table in set(Base.metadata.tables)
        - {"catalogue_history", "ledger_transaction", "movement", "admin_session"}
        for trigger in ("record_history", "record_history_update")
    }


def test_search_is_fuzzy_and_indexed(
    session: Session, new_item: Callable[..., Item]
) -> None:
    new_item("LiteBeam 5AC", manufacturer="Ubiquiti", part_number="LBE-5AC-Gen2")
    new_item("Patch cable")
    found = session.scalars(
        select(Item.name).where(Item.matches("litebeem ubiquity"))
    ).all()
    assert found == ["LiteBeam 5AC"]
    # The query's expression is the index's, so the planner can use it. Alembic
    # cannot compare this index, so this is what checks it.
    session.execute(text("SET LOCAL enable_seqscan = off"))
    query = select(Item.id).where(Item.matches("litebeam"))
    plan = "\n".join(
        session.scalars(
            text(
                "EXPLAIN " + str(query.compile(compile_kwargs={"literal_binds": True}))
            )
        )
    )
    assert "ix_item_search" in plan


def test_an_update_that_changes_nothing_is_not_recorded(
    session: Session, category: Category
) -> None:
    change(session, category, name=category.name)
    actions = session.scalars(
        select(CatalogueHistory.action).where(
            CatalogueHistory.table_name == "category",
            CatalogueHistory.row_id == category.id,
        )
    ).all()
    assert actions == ["insert"]


def test_history_refuses_a_name_as_actor(session: Session) -> None:
    """Even set without set_actor(), as a shell or script might."""
    session.execute(text("SELECT set_config('inventory.actor', 'Jane Doe', true)"))
    with violates(session, "ck_catalogue_history_actor"):
        session.add(Category(name="Tools"))
