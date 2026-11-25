"""Labels the tests print for an item."""

from decimal import Decimal

from sqlalchemy.orm import Session

from inventory_tng.labels import labels
from inventory_tng.models.catalogue import Item, ItemPackage
from inventory_tng.models.labels import Label

from helpers.rows import add


def labelled(session: Session, item: Item, packages: int) -> list[Label]:
    """The item's own label printed, and one for each of `packages` new
    spools of 305."""
    made = [labels.make_for_item(session, item.id)]
    for n in range(packages):
        package = add(
            session, ItemPackage(item_id=item.id, name=f"Spool {n}", size=Decimal(305))
        )
        made.append(labels.make_for_item(session, item.id, package.id))
    assert all(made)
    return [label for label in made if label is not None]
