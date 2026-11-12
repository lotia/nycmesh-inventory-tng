"""How long the scan page's catalogue takes against a ledger of years
(inventory-tng-kcz): `mise run measure-catalogue`. Not a test: pytest
collects it only when named, as that task does. It fills a database of its
own with a store's worth of items, places and volunteers, and YEARS of
saves, sealed and open, then prints the plans of the ledger's queries the
catalogue makes and how long GET /api/catalogue takes."""

import os
import statistics
import time
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, event, text
from sqlalchemy.orm import Session

from inventory_tng import catalogue, devices
from inventory_tng.app import create_app
from inventory_tng.db import make_engine
from inventory_tng.models import (
    Category,
    Item,
    ItemPackage,
    Location,
    Volunteer,
)
from inventory_tng.settings import Settings

from databases import OWNER, as_role
from helpers.devices import new_device
from helpers.rows import setting_up
from helpers.sign_in import TOTP_KEYS, new_admin

# The scale, from the shell: years of saves, and saves a day.
YEARS = int(os.environ.get("MEASURE_YEARS", "5"))
PER_DAY = int(os.environ.get("MEASURE_PER_DAY", "40"))
ITEMS, PACKAGED, STORES, AREAS, VANS, VOLUNTEERS = 250, 150, 3, 20, 25, 60
# Of the open packages made, those still open.
STILL_OPEN = 150
# Downloads timed, after one to warm up.
TIMES = 20


@pytest.fixture
def filled(own_engine: Engine, own_database: str) -> Engine:
    engine = own_engine
    with setting_up(Session(engine)) as session:
        _catalogue(session)
        session.commit()
    with engine.begin() as connection:
        _run(connection, LEDGER)
    # As the owner, who may retire rows outside the app and ANALYZE.
    owner = make_engine(as_role(own_database, OWNER))
    with owner.begin() as connection:
        _run(connection, RETIRE)
        connection.execute(text("ANALYZE"))
    owner.dispose()
    return engine


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


# Saves in blocks of 20, each block of one package at one store and one van,
# by its place in the block: receipts of sealed packages into the store, one
# opened there and cut from four times to installs, two moved sealed to the
# van, and loose stock moved or installed; each but an opening with a loose
# line of another item, as a batch often has two. So sealed stock at the
# stores stays level and the vans fill. Each block's open package is closed
# as the block ends, but the newest STILL_OPEN blocks'.
LEDGER = """
CREATE TEMPORARY TABLE pack AS
  SELECT row_number() OVER (ORDER BY id) - 1 AS n, id, item_id, size
  FROM item_package;
CREATE TEMPORARY TABLE store AS
  SELECT row_number() OVER (ORDER BY id) - 1 AS n, id FROM location
  WHERE kind IN ('store', 'area');
CREATE TEMPORARY TABLE van AS
  SELECT row_number() OVER (ORDER BY id) - 1 AS n, id FROM location
  WHERE kind = 'vehicle';
CREATE TEMPORARY TABLE helper AS
  SELECT row_number() OVER (ORDER BY id) - 1 AS n, id FROM volunteer;
CREATE TEMPORARY TABLE loose AS
  SELECT row_number() OVER (ORDER BY id) - 1 AS n, id FROM item;
CREATE TEMPORARY TABLE plan AS
  SELECT g, g / 20 AS block,
    CASE
      WHEN g % 20 IN (0, 1, 3) THEN 'receipt'
      WHEN g % 20 = 2 THEN 'open'
      WHEN g % 20 IN (4, 5) THEN 'move'
      WHEN g % 20 BETWEEN 7 AND 10 THEN 'cut'
      WHEN g % 20 IN (6, 11, 12, 13) THEN 'loose'
      ELSE 'install' END AS what,
    now() - make_interval(secs => (:saves - g) * 86400.0 / :per_day) AS at
  FROM generate_series(0, :saves - 1) AS g;
CREATE TEMPORARY TABLE block AS
  SELECT b AS block, p.id AS package_id, p.item_id, p.size,
    s.id AS store, v.id AS van, h.id AS volunteer, l.id AS loose_item
  FROM generate_series(0, (:saves - 1) / 20) AS b
  JOIN pack p ON p.n = b % (SELECT count(*) FROM pack)
  JOIN store s ON s.n = b % (SELECT count(*) FROM store)
  JOIN van v ON v.n = b % (SELECT count(*) FROM van)
  JOIN helper h ON h.n = b % (SELECT count(*) FROM helper)
  JOIN loose l ON l.n = (b * 7 + 3) % (SELECT count(*) FROM loose);
INSERT INTO open_package (key, opened_at, item_id, package_id, location_id,
  closed_at, closed_by_admin_id)
  SELECT gen_random_uuid(), plan.at, item_id, package_id, store,
    CASE WHEN block < (:saves - 1) / 20 - :still_open
      THEN plan.at + make_interval(secs => 18 * 86400.0 / :per_day) END,
    CASE WHEN block < (:saves - 1) / 20 - :still_open
      THEN (SELECT min(id) FROM admin) END
  FROM plan JOIN block USING (block) WHERE what = 'open' ORDER BY g;
-- One a block, made in order.
CREATE TEMPORARY TABLE opened AS
  SELECT row_number() OVER (ORDER BY id) - 1 AS block, id AS open_id
  FROM open_package;
INSERT INTO ledger_transaction (created_at, idempotency_key, kind,
  from_location_id, to_location_id, volunteer_id, time_zone)
  SELECT at, gen_random_uuid(),
    CASE what WHEN 'cut' THEN 'install' WHEN 'loose' THEN 'move' ELSE what END,
    CASE what WHEN 'receipt' THEN NULL WHEN 'loose' THEN van
      WHEN 'install' THEN van ELSE store END,
    CASE what WHEN 'move' THEN van WHEN 'cut' THEN NULL
      WHEN 'install' THEN NULL ELSE store END,
    volunteer, 'America/New_York'
  FROM plan JOIN block USING (block) ORDER BY g;
CREATE TEMPORARY TABLE tx AS
  SELECT row_number() OVER (ORDER BY id) - 1 AS g, id FROM ledger_transaction;
INSERT INTO movement (transaction_id, item_id, quantity, from_package_id,
  from_open_package_id, to_package_id, to_open_package_id)
  SELECT tx.id, item_id,
    CASE WHEN what IN ('receipt', 'move', 'open') THEN size
      WHEN what = 'cut' THEN 12 ELSE 3 END,
    CASE WHEN what IN ('move', 'open') THEN package_id END,
    CASE WHEN what = 'cut' THEN open_id END,
    CASE WHEN what IN ('receipt', 'move') THEN package_id END,
    CASE WHEN what = 'open' THEN open_id END
  FROM plan JOIN block USING (block) JOIN tx USING (g)
    LEFT JOIN opened USING (block)
  WHERE what NOT IN ('loose', 'install')
  UNION ALL
  SELECT tx.id, loose_item, 2, NULL, NULL, NULL, NULL
  FROM plan JOIN block USING (block) JOIN tx USING (g)
  WHERE what <> 'open';
"""
# Retired, with stock left: what volunteers may still take, which the
# catalogue reads from the ledger too.
RETIRE = """
UPDATE item SET retired_at = now() WHERE id IN (
  SELECT item_id FROM item_package ORDER BY id LIMIT 10);
UPDATE item_package SET retired_at = now() WHERE id IN (
  SELECT id FROM item_package ORDER BY id DESC LIMIT 10)
"""


def _run(connection: Connection, statements: str) -> None:
    saves = YEARS * 365 * PER_DAY
    for statement in statements.split(";"):
        if statement.strip():
            connection.execute(
                text(statement),
                {"saves": saves, "per_day": PER_DAY, "still_open": STILL_OPEN},
            )


def _plans(engine: Engine, session: Session, keys: list[uuid.UUID]) -> list[str]:
    """The catalogue's statements that read the ledger, asked about `keys`
    as a page's outbox asks, each with its plan as run: EXPLAIN (ANALYZE,
    BUFFERS), with the parameters it was sent."""
    sent: list[tuple[str, dict[str, Any]]] = []

    def keep(
        _connection: object,
        _cursor: object,
        statement: str,
        parameters: dict[str, Any],
        *_: object,
    ) -> None:
        if "movement" in statement or "ledger_transaction" in statement:
            sent.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", keep)
    try:
        catalogue.with_saved(session, keys)
    finally:
        event.remove(engine, "before_cursor_execute", keep)
    plans = []
    with engine.connect() as connection:
        for statement, parameters in sent:
            rows = connection.exec_driver_sql(
                f"EXPLAIN (ANALYZE, BUFFERS) {statement}", parameters
            )
            plans.append(f"{statement}\n" + "\n".join(row[0] for row in rows))
    return plans


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
                {"n": catalogue.MOST_KEYS},
            )
        )
    print(
        f"\nLedger: {sizes[0]} transactions, {sizes[1]} movements, {sizes[2]}"
        f" of sealed packages; {sizes[3]} open packages, {sizes[4]} not closed"
    )
    with Session(filled) as session:
        for plan in _plans(filled, session, keys):
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
        assert client.get("/api/catalogue", params=asked).status_code == 200
        took = []
        for _ in range(TIMES):
            start = time.perf_counter()
            reply = client.get("/api/catalogue", params=asked)
            took.append((time.perf_counter() - start) * 1000)
            assert reply.status_code == 200
    print(
        f"\nGET /api/catalogue, {TIMES} times: median {statistics.median(took):.0f}"
        f" ms, slowest {max(took):.0f} ms, {len(reply.content) / 1024:.0f} KiB"
    )
