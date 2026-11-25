import uuid
from decimal import Decimal

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

from inventory_tng.models.catalogue import Category, Item, ItemPackage, VendorOffer
from inventory_tng.models.places import Location
from inventory_tng.models.stock import LedgerTransaction, Movement, OverRecorded

from helpers.rows import add, setting_up


def test_migrations_match_the_models(alembic_config: Config) -> None:
    command.check(alembic_config)


def test_migrations_go_down_and_back_up(alembic_config: Config) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")


def test_stored_amounts_take_their_items_unit_as_units_are_added(
    alembic_config: Config,
) -> None:
    """Rows from before every stored amount recorded its unit are given
    their item's, a check its line's, upgrading through 0005
    (inventory-tng-2rlr)."""
    connection: Connection = alembic_config.attributes["connection"]
    session = setting_up(Session(bind=connection))
    category = add(session, Category(name="Cables"))
    cable = add(session, Item(name="Cat6", category_id=category.id, unit="ft"))
    store = add(session, Location(name="Store", kind="store"))
    receipt = add(
        session,
        LedgerTransaction(
            idempotency_key=uuid.uuid4(),
            kind="receipt",
            to_location_id=store.id,
            time_zone="UTC",
        ),
    )
    line = add(
        session,
        Movement(transaction_id=receipt.id, item_id=cable.id, quantity=500, unit="ft"),
    )
    add(session, ItemPackage(item_id=cable.id, name="Box", size=Decimal(1000)))
    add(session, VendorOffer(item_id=cable.id, url="https://example.org", unit="ft"))
    add(session, OverRecorded(movement_id=line.id, quantity=5, unit="ft"))
    session.close()
    command.downgrade(alembic_config, "0004")
    command.upgrade(alembic_config, "head")
    units = {
        table: connection.scalars(text(f"SELECT unit FROM {table}")).all()
        for table in ("movement", "item_package", "vendor_offer", "over_recorded")
    }
    assert units == {table: ["ft"] for table in units}
