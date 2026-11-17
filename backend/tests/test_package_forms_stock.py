"""Stock by form (8i4.133.7): short ends, minimum sealed and reorder on
the stock pages, and a package's settings on its item's page."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import open_packages
from inventory_tng.admin import items as items_page
from inventory_tng.admin import stock
from inventory_tng.admin.items import SHORTER_THAN_WHOLE
from inventory_tng.ledger import Entry, Form
from inventory_tng.models import Item, ItemPackage
from inventory_tng.stock import balance

from helpers.markup import read, text
from helpers.package_forms import BOX, FRESH, GONE, OPEN, SPOOL
from helpers.stock import Spools

# Stock by form, short ends, minimum sealed and reorder (8i4.133.7).


def test_stock_splits_into_forms_and_short_ends_are_not_usable() -> None:
    packages = {
        SPOOL: open_packages.PackageSize("Spool", Decimal(305), Decimal(15)),
        BOX: open_packages.PackageSize("Box", Decimal(100), None),
    }
    rows = [
        (SPOOL, None, Decimal(915)),
        (None, OPEN, Decimal(120)),
        (None, GONE, Decimal(12)),
        (None, FRESH, Decimal(3)),
        (None, None, Decimal(85)),
    ]
    forms = open_packages.split(rows, packages, {OPEN: SPOOL, GONE: SPOOL, FRESH: BOX})
    assert forms.sealed == [("Spool", Decimal(3), Decimal(915))]
    assert (forms.open, forms.short_ends) == ([Decimal(120), Decimal(3)], [Decimal(12)])
    assert (forms.loose, forms.total, forms.usable) == (85, 1135, 1123)
    # Less than nothing left is the records', not a short end.
    below = open_packages.split([(None, OPEN, Decimal(-8))], packages, {OPEN: SPOOL})
    assert (below.open, below.short_ends) == ([Decimal(-8)], [])


def test_reorder_on_usable_stock_and_sealed_minimums() -> None:
    cable = Item(id=1, name="Cable", minimum=Decimal(400))
    rope = Item(id=2, name="Rope", minimum=None)
    spool = ItemPackage(
        id=10, item_id=1, name="Spool", name_shown="Spool", minimum_sealed=3
    )
    coil = ItemPackage(id=11, item_id=2, name="Coil", minimum_sealed=1)
    usable = {1: Decimal(500), 2: Decimal(50)}
    sealed = {1: {10: Decimal(2)}, 2: {11: Decimal(1)}}
    loose = ItemPackage(id=12, item_id=1, name="Box", minimum_sealed=None)
    (row,) = stock.reorder_rows(
        [cable, rope], usable, sealed, [spool, coil, loose], {1}
    )
    assert (row.item, row.in_stores, row.count_first) == (cable, 500, True)
    assert row.low_sealed == [("Spool", Decimal(2), 3)]
    assert (
        stock.reorder_rows([cable], {1: Decimal(400)}, {}, [], set())[0].item is cable
    )


def test_in_stores_sums_an_items_stock_across_the_stores(
    session: Session, spools: Spools
) -> None:
    """Whatever its form, and whether any store holds less than nothing; not
    what is elsewhere, as in the van."""
    cable = spools.cable.id
    query = balance.in_stores(select(Item.id).where(Item.id == cable))
    assert session.execute(query).all() == [(cable, 610, False)]
    spools.move(Entry(cable, Decimal(700)))
    assert session.execute(query).all() == [(cable, -90, True)]


def test_stock_and_reorder_pages_show_forms(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    spools.spool.short_below, spools.spool.minimum_sealed = Decimal(15), 2
    spools.cable.minimum = Decimal(400)
    made = spools.opened()
    spools.move(Entry(spools.cable.id, Decimal(295), Form(open_package_id=made.id)))
    session.flush()
    page = read(admin_client.get("/admin/stock").text)
    for line in ("1 sealed Spool", "short end: 10 m", "usable: 305 m"):
        assert page.all("span", line, class_="hint block")
    reorder = " ".join(text(admin_client.get("/admin/stock/reorder")).split())
    assert spools.cable.name in reorder
    assert "1 sealed Spool, of 2 to keep" in reorder


def test_a_packages_settings_are_set_on_its_items_page(
    admin_client: TestClient,
    registered_client: TestClient,
    session: Session,
    spools: Spools,
) -> None:
    path = f"/admin/items/{spools.cable.id}/packages/{spools.spool.id}/settings"
    done = admin_client.post(path, data={"short_below": "15", "minimum_sealed": "2"})
    assert done.headers["location"].endswith(
        "notice=package_settings&section=packages#packages"
    )
    session.refresh(spools.spool)
    assert (spools.spool.short_below, spools.spool.minimum_sealed) == (15, 2)
    # An open one below it is a short end, as the scan page is told.
    made = spools.opened()
    spools.move(Entry(spools.cable.id, Decimal(295), Form(open_package_id=made.id)))
    listed = registered_client.get("/api/catalogue").json()["open_packages"]
    assert [entry["short_below"] for entry in listed if entry["id"] == made.id] == [
        "15.000"
    ]
    admin_client.post(path, data={"short_below": "", "minimum_sealed": ""})
    session.refresh(spools.spool)
    assert (spools.spool.short_below, spools.spool.minimum_sealed) == (None, None)
    for bad, words in (
        ({"short_below": "-1"}, None),
        ({"short_below": "305"}, SHORTER_THAN_WHOLE),
        ({"minimum_sealed": "lots"}, items_page.WHOLE_PACKAGES),
    ):
        refused = admin_client.post(path, data=bad)
        assert refused.status_code == 400
        assert words is None or str(words) in text(refused)
    other = f"/admin/items/{spools.place.item.id}/packages/{spools.spool.id}/settings"
    assert admin_client.post(other, data={}).status_code == 404
    # Its item retired: read only.
    spools.cable.retired_at = datetime.now(UTC)
    session.flush()
    assert admin_client.post(path, data={}).status_code == 409
    spools.cable.retired_at = None
    spools.spool.retired_at = datetime.now(UTC)
    session.flush()
    assert admin_client.post(path, data={}).status_code == 404


# What only both batches together show (the combined review).


def test_a_place_holding_forms_that_net_to_nothing_holds_stock(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    """A sealed spool there, and as much taken loose: not empty."""
    store = spools.store.id
    spools.move(Entry(spools.cable.id, Decimal(610)))  # Loose, though sealed.
    assert balance.holds_stock(session, store)
    page = text(admin_client.get("/admin/stock"))
    assert "count it" in page


@pytest.mark.parametrize("amount", ["-2.000", "-0.500"])
def test_an_open_package_showing_less_than_nothing_is_listed(
    registered_client: TestClient, session: Session, spools: Spools, amount: str
) -> None:
    made = spools.opened()
    spools.move(
        Entry(
            spools.cable.id,
            Decimal(305) - Decimal(amount),
            Form(open_package_id=made.id),
        )
    )
    listed = registered_client.get("/api/catalogue").json()["open_packages"]
    assert [entry["left"] for entry in listed if entry["id"] == made.id] == [amount]
