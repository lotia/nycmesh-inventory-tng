"""What the labels page reads (labels/labels_routes.py): plain functions
taking a Session, each named for what it returns, and the select its list
searches (docs/decisions/0023)."""

from collections.abc import Sequence

from sqlalchemy import Row, exists, func, select
from sqlalchemy.orm import Session, aliased

from inventory_tng.models.catalogue import Item, ItemPackage
from inventory_tng.models.labels import Label
from inventory_tng.models.places import Location

# The place around a place, which a label's search reads too.
parent = aliased(Location)

# Each label read with what it names: its item, package or place, and the
# place around that place.
with_what_they_name = (
    select(Label)
    .outerjoin(Item, Item.id == Label.item_id)
    .outerjoin(ItemPackage, ItemPackage.id == Label.package_id)
    .outerjoin(Location, Location.id == Label.location_id)
    .outerjoin(parent, parent.id == Location.parent_id)
)


def items_labelled(session: Session) -> Sequence[Row[int, str]]:
    """Each item that has labels, its id and name as shown, by that name
    ignoring case, then id."""
    return session.execute(
        select(Item.id, Item.name_shown)
        .where(exists().where(Label.item_id == Item.id))
        .order_by(func.lower(Item.name_shown), Item.id)
    ).all()
