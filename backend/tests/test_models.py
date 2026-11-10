"""The catalogue's rules: the database's own (models.py, migration 0002),
each failing write checked by the constraint it names, which is what
describe_error() logs; and the app's, each refused through its domain
function by the rule's name (db.RuleBroken), whose every case
test_catalogue_rules.py tests without a database."""

import dataclasses
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from inventory_tng import categories, items, labels, locations, volunteers
from inventory_tng.db import ACTOR_INFO, Base, refusal, set_actor
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

from helpers.rows import add, breaks, item_fields, violates

NOW = datetime(2026, 11, 1, tzinfo=UTC)


def change(session: Session, row: Base, **values: object) -> None:
    """An UPDATE straight to the database, as its constraints see it."""
    model = type(row)
    session.execute(update(model).where(model.id == row.id).values(**values))  # ty: ignore[unresolved-attribute]


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


def test_names_are_unique_ignoring_case_retired_ones_too(
    session: Session, category: Category, store: Location
) -> None:
    """So a retired one can always be brought back (docs/decisions/0019)."""
    with violates(session, "uq_category_name"):
        session.add(Category(name="RADIOS"))
    category.retired_at = NOW
    session.flush()
    with violates(session, "uq_category_name"):
        session.add(Category(name="radios"))
    store.retired_at = NOW
    session.flush()
    with violates(session, "uq_location_name"):
        session.add(Location(name=store.name.upper(), kind=store.kind))


def test_a_custody_name_is_unique_only_among_current_ones(session: Session) -> None:
    """Retired, and made again, each time its volunteer stops and starts
    holding stock."""
    sam = add(session, Volunteer(name="Sam"))
    held = add(session, Location(name="Sam", kind="custody", volunteer_id=sam.id))
    held.retired_at = NOW
    session.flush()
    add(session, Location(name="Sam", kind="custody", volunteer_id=sam.id))
    other = add(session, Volunteer(name="Sam B"))
    with violates(session, "uq_location_custody_name"):
        session.add(Location(name="sam", kind="custody", volunteer_id=other.id))


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


def test_one_current_label_for_each_thing(
    session: Session, new_item: Callable[..., Item], store: Location
) -> None:
    item = new_item()
    add(session, Label(item_id=item.id))
    add(session, Label(location_id=store.id))
    with violates(session, "uq_label_current"):
        session.add(Label(item_id=item.id))
    with violates(session, "uq_label_current"):
        session.add(Label(location_id=store.id))
    add(session, Label(item_id=item.id, revoked_at=datetime.now(UTC)))


def test_a_labels_package_belongs_to_its_item(
    session: Session, new_item: Callable[..., Item], store: Location
) -> None:
    cable, radio = new_item("Cable"), new_item("Radio")
    reel = add(session, ItemPackage(item_id=cable.id, name="reel", size=Decimal(305)))
    with violates(session, "ck_label_package_needs_item"):
        session.add(Label(location_id=store.id, package_id=reel.id))
    # Another item's package names nothing of this one's.
    assert labels.make_for_item(session, radio.id, reel.id) is None
    assert labels.make_for_item(session, cable.id, reel.id) is not None


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
    van = locations.add(session, "Van", "vehicle")
    with breaks(session, "location_area_parent"):
        locations.add(session, "Glovebox", "area", van.id)
    shelf = locations.add(session, "Shelf", "area", store.id)
    bin_ = locations.add(session, "Bin", "area", store.id)
    with breaks(session, "location_area_parent"):
        locations.save(session, bin_, "Bin", van.id, seen=None)
    locations.save(session, bin_, "Bin", shelf.id, seen=None)
    # Renamed where it is, nothing is checked.
    locations.save(session, van, "Big van", seen=None)


def test_the_location_tree_has_no_cycles(session: Session, store: Location) -> None:
    shelf = locations.add(session, "Shelf", "area", store.id)
    bin_ = locations.add(session, "Bin", "area", shelf.id)
    with breaks(session, "location_no_cycle"):
        locations.save(session, shelf, "Shelf", bin_.id, seen=None)
    with breaks(session, "location_no_cycle"):
        locations.save(session, shelf, "Shelf", shelf.id, seen=None)


def test_custody_belongs_to_one_current_volunteer(session: Session) -> None:
    alice = add(session, Volunteer(name="Volunteer A"))
    add(session, Location(name="With A", kind="custody", volunteer_id=alice.id))
    with violates(session, "uq_location_custody_volunteer_id"):
        session.add(Location(name="Also with A", kind="custody", volunteer_id=alice.id))
    gone = add(session, Volunteer(name="Volunteer B", retired_at=NOW))
    with breaks(session, "location_custody_volunteer"):
        locations.add_custody(session, gone.id, "With B")


def test_a_volunteer_holding_stock_stays_current(session: Session) -> None:
    holder = volunteers.add(session, "Volunteer A", None)
    other = volunteers.add(session, "Volunteer B", None)
    custody = volunteers.hold_stock(session, holder)
    with breaks(session, "location_custody_volunteer"):
        volunteers.retire(session, holder, NOW)
    with breaks(session, "location_custody_volunteer"):
        volunteers.merge(session, holder, other.id, NOW)
    locations.retire(session, custody, NOW)
    volunteers.merge(session, holder, other.id, NOW)
    # Nor does custody come back for a volunteer who is gone.
    with breaks(session, "location_custody_volunteer"):
        locations.restore(session, custody)


def test_a_volunteers_custody_is_renamed_with_them(session: Session) -> None:
    holder = volunteers.add(session, "Volunteer A", None)
    other = volunteers.add(session, "Volunteer B", None)
    custody = volunteers.hold_stock(session, holder)
    volunteers.save(session, holder, "Volunteer Ada", "ada@example.org", seen=None)
    volunteers.save(session, other, "Volunteer Ben", None, seen=None)
    assert (custody.name, holder.email) == ("Volunteer Ada", "ada@example.org")


def test_current_areas_have_current_parents(session: Session, store: Location) -> None:
    shelf = locations.add(session, "Shelf", "area", store.id)
    with breaks(session, "location_parent_current"):
        locations.retire(session, store, NOW)
    locations.retire(session, shelf, NOW)
    locations.retire(session, store, NOW)
    with breaks(session, "location_parent_current"):
        locations.add(session, "New shelf", "area", store.id)
    with breaks(session, "location_parent_current"):
        locations.restore(session, shelf)


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
    a, b, c = (volunteers.add(session, f"Volunteer {n}", None) for n in "ABC")
    with violates(session, "ck_volunteer_not_merged_into_self"):
        merge(session, a, a)
    with violates(session, "ck_volunteer_merged_is_retired"):
        change(session, a, merged_into_id=b.id)
    volunteers.merge(session, a, b.id, NOW)
    # Not into one that is merged itself...
    with breaks(session, "volunteer_merge_target"):
        volunteers.merge(session, c, a.id, NOW)
    # ...and the target stays current while others point at it.
    with breaks(session, "volunteer_merge_target"):
        volunteers.retire(session, b, NOW)
    # Point the others onward first, and then it can go.
    volunteers.restore(session, a)
    volunteers.merge(session, a, c.id, NOW)
    volunteers.merge(session, b, c.id, NOW)


def test_saving_an_item_blanks_no_field_left_out() -> None:
    """items.save sets every field, so none may have a default: one left
    out is an error, not a blank (inventory-tng-8i4.122)."""
    defaults = [
        field.name
        for field in dataclasses.fields(items.Fields)
        if field.default is not dataclasses.MISSING
        or field.default_factory is not dataclasses.MISSING
    ]
    assert defaults == []
    with pytest.raises(TypeError):
        items.Fields(name="LiteBeam", category_id=1)  # ty: ignore[missing-argument]


def test_current_items_sit_in_current_categories(session: Session) -> None:
    """inventory-tng-8i4.58."""
    radios = categories.add(session, "Radios rule")
    old = categories.add(session, "Old rule")
    categories.retire(session, old, NOW)
    item = items.add(
        session, items.Fields.new(name="LiteBeam rule", category_id=radios.id)
    )
    with breaks(session, "item_category_current"):
        items.add(session, items.Fields.new(name="Into the old", category_id=old.id))
    with breaks(session, "item_category_current"):
        items.save(session, item, item_fields(item, category_id=old.id), seen=None)
    # A category with a current item stays current...
    with breaks(session, "category_has_items"):
        categories.retire(session, radios, NOW)
    # ...until the item goes, and a retired item cannot come back into it.
    items.retire(session, item, NOW)
    categories.retire(session, radios, NOW)
    with breaks(session, "item_category_current"):
        items.restore(session, item)
    # A retired item may sit in a retired category, but is read only there
    # until brought back (docs/decisions/0019).
    with breaks(session, "retired_read_only"):
        items.save(session, item, item_fields(item, notes="kept for parts"), seen=None)


# What the migrations made that runs inside PostgreSQL, by kind: triggers
# (PostgreSQL's own, for foreign keys, aside), functions (an extension's,
# such as pg_trgm's, aside), views, rules (pg_rules leaves out a view's own
# _RETURN rule), and row-level security, its policies or a table with it on.
PUBLIC = "relnamespace = 'public'::regnamespace"
LOGIC = {
    "trigger": "SELECT tgrelid::regclass || ' ' || tgname FROM pg_trigger"
    f" WHERE NOT tgisinternal AND tgrelid IN (SELECT oid FROM pg_class WHERE {PUBLIC})",
    "function": "SELECT proname FROM pg_proc p"
    " WHERE pronamespace = 'public'::regnamespace AND NOT EXISTS"
    " (SELECT FROM pg_depend WHERE classid = 'pg_proc'::regclass"
    " AND objid = p.oid AND deptype = 'e')",
    "view": f"SELECT relname FROM pg_class WHERE {PUBLIC} AND relkind IN ('v', 'm')",
    "rule": "SELECT tablename || ' ' || rulename FROM pg_rules"
    " WHERE schemaname = 'public'",
    "policy": "SELECT polrelid::regclass || ' ' || polname FROM pg_policy"
    f" WHERE polrelid IN (SELECT oid FROM pg_class WHERE {PUBLIC})",
    "row security": f"SELECT relname FROM pg_class WHERE {PUBLIC}"
    " AND (relrowsecurity OR relforcerowsecurity)",
}


def test_no_logic_is_in_the_database(session: Session) -> None:
    """Every rule is the app's (domain modules, ledger.insert_transaction,
    SiteSettings), history is history.py's, stock is a select, and what may
    never change or go is kept by the app login's privileges."""
    found = {kind: session.scalars(text(sql)).all() for kind, sql in LOGIC.items()}
    assert found == {kind: [] for kind in LOGIC}


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
    # Set without its value loaded, the ORM cannot tell it is the same, so
    # it writes the UPDATE: history is what sees that nothing changed.
    name = category.name
    session.expire(category, ["name"])
    category.name = name
    session.flush()
    actions = session.scalars(
        select(CatalogueHistory.action).where(
            CatalogueHistory.table_name == "category",
            CatalogueHistory.row_id == category.id,
        )
    ).all()
    assert actions == ["insert"]


def test_history_refuses_a_name_as_actor(session: Session) -> None:
    """Even set without set_actor(), as a shell or script might."""
    session.info[ACTOR_INFO] = "Jane Doe"
    with violates(session, "ck_catalogue_history_actor"):
        session.add(Category(name="Tools"))
