"""What the tests that make, edit or post items share."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from inventory_tng.catalogue.items_routes import RECORDED, RECORDED_IN
from inventory_tng.models.catalogue import Category, Item, ItemPackage

from helpers.markup import Element, row_links
from helpers.rows import add, shown

NOW = datetime(2026, 11, 1, tzinfo=UTC)


def new_item(
    session: Session, category: Category, name: str = "LiteBeam", unit: str = "each"
) -> Item:
    return add(session, Item(name=name, category_id=category.id, unit=unit))


def item_form(category: Category, **fields: str) -> dict[str, str]:
    """An item's form as its page sends it: in the category, counted
    each, with the fields given."""
    return {"category_id": str(category.id), "unit": "each"} | fields


def shown_item_form(item: Item, **fields: str) -> dict[str, str]:
    """The item's edit form as its page shows it, with `fields` changed."""
    return {
        "name": item.name,
        "category_id": str(item.category_id),
        "unit": item.unit,
        "version": shown(item),
    } | fields


def unit_answered(item: Item, answer: str = "yes", **fields: str) -> dict[str, str]:
    """The item's edit form with `fields` changed, answering `answer` (yes
    or no) to whether its recorded amounts were really in its unit now."""
    return shown_item_form(item, **fields) | {RECORDED: answer, RECORDED_IN: item.unit}


def same_size_twins(
    session: Session, item: Item, size: Decimal = Decimal(10)
) -> tuple[ItemPackage, ItemPackage]:
    """Two of the item's packages of one size, by id. The first is renamed
    after the second is added, to a name after the second's, so
    PostgreSQL reads it last on disk and by every index: a list with no
    tie order shows them the wrong way round. A rename changes indexed
    columns, so the row is rewritten in every index too (no HOT update),
    which a change to another column would not do."""
    first = add(session, ItemPackage(item_id=item.id, name="Twin", size=size))
    second = add(session, ItemPackage(item_id=item.id, name="Twin A", size=size))
    first.name = "Twin B"
    session.flush()
    return first, second


def items_named(page: str | Element) -> list[str]:
    """The items the list shows, in its order."""
    return row_links(page, "/admin/items")
