"""How long the labels list and its suggestions take against a store's
worth of labels (inventory-tng-8i4.60.24): `mise run measure-labels`. Not a
test: pytest collects it only when named, as that task does. It fills a
database of its own with items, packages and places, a label for each and
some revoked, and years of saves (measure_catalogue's), then prints the
plan of each statement that reads labels, as run, and how long each search
takes on GET /admin/labels and its /suggest."""

import os
import statistics
import time
from collections.abc import Callable
from functools import partial
from typing import Any

import pytest
from fastapi.testclient import TestClient
from measure_catalogue import LEDGER, RETIRE, run_ledger
from sqlalchemy import Engine, event, func, text
from sqlalchemy.orm import Session

from inventory_tng.app import create_app
from inventory_tng.core.db_sessions import make_engine
from inventory_tng.core.settings import Settings
from inventory_tng.labels.paths import LABELS
from inventory_tng.models.catalogue import Category, Item, ItemPackage
from inventory_tng.models.labels import Label
from inventory_tng.models.places import Location
from inventory_tng.models.volunteers import Volunteer

from databases import OWNER, as_role
from helpers.rows import setting_up
from helpers.sign_in import TOTP_KEYS, new_admin, sign_in_with

# The scale, from the shell: items, of which packaged ones have two
# packages each, and places; each has a label, and REVOKED_EVERY'th one an
# older, revoked label too.
ITEMS = int(os.environ.get("MEASURE_ITEMS", "1500"))
PACKAGED = ITEMS * 2 // 3
STORES, AREAS, VANS, VOLUNTEERS = 5, 300, 60, 60
REVOKED_EVERY = 4
# Requests timed for each search, after one to warm up.
TIMES = 20
# What volunteers' administrators type: nothing, a word, a word mistyped, a
# part number, words and a size, and a fragment of a code.
SEARCHES = ("", "litebeam", "litebeem", "LBE-5AC", "cable 305", "7K3")

MAKERS = ("Ubiquiti", "MikroTik", "TP-Link", "Netgear", "Belden", "Panduit")
THINGS = (
    "LiteBeam 5AC",
    "NanoStation",
    "PowerBeam",
    "Omni antenna",
    "Cat6 cable",
    "RJ45 connector",
    "PoE injector",
    "Mounting bracket",
    "Switch 8 port",
    "Router hAP",
    "Zip ties",
    "Grounding wire",
)


@pytest.fixture
def filled(own_engine: Engine, own_database: str) -> str | None:
    """The database filled, and its administrator's authenticator key."""
    with setting_up(Session(own_engine)) as session:
        _catalogue(session)
        _, key = new_admin(session)
        session.commit()
    with own_engine.begin() as connection:
        run_ledger(connection, LEDGER)
    owner = make_engine(as_role(own_database, OWNER))
    with owner.begin() as connection:
        run_ledger(connection, RETIRE)
        connection.execute(text("ANALYZE"))
    owner.dispose()
    return key


def _catalogue(session: Session) -> None:
    """Items with makers and part numbers; two packages of most; stores,
    their areas and vans; a label on each, and revoked ones; volunteers,
    for the ledger."""
    category = Category(name="Supplies")
    session.add(category)
    session.flush()
    items = [
        Item(
            name=f"{THINGS[n % len(THINGS)]} {n}",
            manufacturer=MAKERS[n % len(MAKERS)],
            part_number=f"P{n:05d}-{THINGS[n % len(THINGS)][:3].upper()}",
            category_id=category.id,
            unit="m" if "cable" in THINGS[n % len(THINGS)] else "each",
            track_opened=n < PACKAGED,
        )
        for n in range(ITEMS)
    ]
    session.add_all(items)
    session.flush()
    packages = [
        ItemPackage(item_id=item.id, name=f"Pack {size}", size=size)
        for item in items[:PACKAGED]
        for size in (50, 305)
    ]
    session.add_all(packages)
    stores = [Location(name=f"Store {n}", kind="store") for n in range(STORES)]
    session.add_all(stores)
    session.flush()
    places = [
        *stores,
        *(
            Location(name=f"Shelf {n}", kind="area", parent_id=stores[n % STORES].id)
            for n in range(AREAS)
        ),
        *(Location(name=f"Van {n}", kind="vehicle") for n in range(VANS)),
    ]
    session.add_all(places)
    session.flush()
    labels = [
        *(Label(item_id=item.id) for item in items),
        *(Label(item_id=p.item_id, package_id=p.id) for p in packages),
        *(Label(location_id=place.id) for place in places),
    ]
    revoked = [
        Label(
            item_id=label.item_id,
            package_id=label.package_id,
            location_id=label.location_id,
            revoked_at=func.now(),
        )
        for label in labels[::REVOKED_EVERY]
    ]
    session.add_all(labels + revoked)
    session.add_all(Volunteer(name=f"Volunteer {n}") for n in range(VOLUNTEERS))


def _plans(engine: Engine, ask: Callable[[], object]) -> list[str]:
    """The statements reading labels that `ask` sends, each with its plan
    as run: EXPLAIN (ANALYZE, BUFFERS), with the parameters it was sent."""
    sent: list[tuple[str, dict[str, Any]]] = []

    def keep(
        _connection: object,
        _cursor: object,
        statement: str,
        parameters: dict[str, Any],
        *_: object,
    ) -> None:
        if "FROM label" in statement:
            sent.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", keep)
    try:
        ask()
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


def _timed(client: TestClient, path: str, params: dict[str, str]) -> list[float]:
    assert client.get(path, params=params).status_code == 200
    took = []
    for _ in range(TIMES):
        start = time.perf_counter()
        reply = client.get(path, params=params)
        took.append((time.perf_counter() - start) * 1000)
        assert reply.status_code == 200
    return took


def test_measure_the_labels(filled: str | None, own_database: str) -> None:
    app = create_app(
        Settings(database_url=own_database), totp_keys=TOTP_KEYS, startup_checks=False
    )
    engine = app.state.db.engine
    with engine.connect() as connection:
        sizes = connection.execute(
            text(
                "SELECT count(*), count(*) FILTER (WHERE revoked_at IS NULL),"
                " (SELECT count(*) FROM movement) FROM label"
            )
        ).one()
    print(f"\nLabels: {sizes[0]}, {sizes[1]} not revoked; {sizes[2]} movements")
    timings = []
    with TestClient(app, base_url="https://testserver") as client:
        sign_in_with(client, key=filled)
        for search in SEARCHES:
            for path in (LABELS, f"{LABELS}/suggest"):
                params = {"q": search} if search else {}
                plans = _plans(engine, partial(client.get, path, params=params))
                # None if signed out, as the sign-in page answers instead.
                assert plans or not search
                for plan in plans:
                    print(f"\n-- {path} q={search!r}\n{plan}")
                took = _timed(client, path, params)
                timings.append(
                    f"GET {path} q={search!r}: median"
                    f" {statistics.median(took):.0f} ms, slowest {max(took):.0f} ms"
                )
    print("\n" + "\n".join(timings))
