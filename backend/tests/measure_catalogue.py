"""How long the scan page's catalogue takes against a ledger of years
(inventory-tng-kcz): `mise run measure-catalogue`. Not a test: pytest
collects it only when named, as that task does. It fills a database of its
own with a store's worth of items, places and volunteers, and years of
saves, sealed and open, then prints the plans of the ledger's queries the
catalogue makes and how long GET /api/catalogue takes."""

import uuid
from functools import partial

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from inventory_tng.app import create_app
from inventory_tng.core.settings import Settings
from inventory_tng.devices import devices
from inventory_tng.models.catalogue import Category, Item, ItemPackage
from inventory_tng.models.places import Location
from inventory_tng.models.volunteers import Volunteer
from inventory_tng.scan import catalogue_api

from helpers.devices import new_device
from helpers.measuring import TIMES, plans, summary, timed
from helpers.rows import setting_up
from helpers.sign_in import TOTP_KEYS, new_admin
from helpers.years_of_saves import add_years_of_saves

ITEMS, PACKAGED, STORES, AREAS, VANS, VOLUNTEERS = 250, 150, 3, 20, 25, 60


@pytest.fixture
def filled(own_engine: Engine, own_database: str) -> Engine:
    with setting_up(Session(own_engine)) as session:
        _catalogue(session)
        session.commit()
    add_years_of_saves(own_engine, own_database)
    return own_engine


def _catalogue(session: Session) -> None:
    """Items, most of them packaged, cable among them; stores, their areas
    and vans; volunteers; an administrator to close open packages."""
    category = Category(name="Supplies")
    session.add(category)
    session.flush()
    unit = ("m", "each")
    items = [
        Item(
            name=f"Item {n}",
            category_id=category.id,
            unit=unit[n % 2],
            # Packaged ones' opened packages followed, as cable's are.
            track_opened=n < PACKAGED,
        )
        for n in range(ITEMS)
    ]
    session.add_all(items)
    session.flush()
    session.add_all(
        ItemPackage(item_id=item.id, name=f"Pack {size}", size=size)
        for item in items[:PACKAGED]
        for size in (50, 305)
    )
    stores = [Location(name=f"Store {n}", kind="store") for n in range(STORES)]
    session.add_all(stores)
    session.flush()
    session.add_all(
        Location(name=f"Area {n}", kind="area", parent_id=stores[n % STORES].id)
        for n in range(AREAS)
    )
    session.add_all(Location(name=f"Van {n}", kind="vehicle") for n in range(VANS))
    session.add_all(Volunteer(name=f"Volunteer {n}") for n in range(VOLUNTEERS))
    new_admin(session)


def _ledger_plans(engine: Engine, keys: list[uuid.UUID]) -> list[str]:
    """The catalogue's statements that read the ledger, asked about `keys`
    as a page's outbox asks, each with its plan as run."""
    with Session(engine) as session:
        return plans(
            engine,
            lambda statement: (
                "movement" in statement or "ledger_transaction" in statement
            ),
            partial(catalogue_api.with_saved, session, keys),
        )


def test_measure_the_catalogue(filled: Engine, own_database: str) -> None:
    with filled.connect() as connection:
        sizes = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM ledger_transaction),"
                " (SELECT count(*) FROM movement),"
                " (SELECT count(*) FROM movement WHERE from_package_id IS NOT NULL"
                "  OR to_package_id IS NOT NULL),"
                " (SELECT count(*) FROM open_package),"
                " (SELECT count(*) FROM open_package WHERE closed_at IS NULL)"
            )
        ).one()
        # As many as an outbox asks about, the newest saved.
        keys = list(
            connection.scalars(
                text(
                    "SELECT idempotency_key FROM ledger_transaction"
                    " ORDER BY id DESC LIMIT :n"
                ),
                {"n": catalogue_api.MOST_KEYS},
            )
        )
    print(
        f"\nLedger: {sizes[0]} transactions, {sizes[1]} movements, {sizes[2]}"
        f" of sealed packages; {sizes[3]} open packages, {sizes[4]} not closed"
    )
    for plan in _ledger_plans(filled, keys):
        print(f"\n{plan}")
    app = create_app(
        Settings(database_url=own_database), totp_keys=TOTP_KEYS, startup_checks=False
    )
    with setting_up(app.state.db.session()) as session:
        _, token = new_device(session)
        session.commit()
    with TestClient(app, base_url="https://testserver") as client:
        client.cookies.set(devices.COOKIE, token)
        asked = {"key": [str(key) for key in keys]}
        took, reply = timed(client, "/api/catalogue", asked)
    print(
        f"\nGET /api/catalogue, {TIMES} times: {summary(took)},"
        f" {len(reply.content) / 1024:.0f} KiB"
    )
