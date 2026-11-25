"""Stock by form (8i4.133.7): short ends, minimum sealed and reorder on
the stock pages, and a package's settings on its item's page."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.catalogue.forms import SHORTER_THAN_WHOLE, WHOLE_PACKAGES
from inventory_tng.models.catalogue import Item, ItemPackage
from inventory_tng.stock import balance, package_forms, stock_routes
from inventory_tng.stock.ledger import Entry, Form

from helpers.markup import read, text, words_on
from helpers.package_forms import BOX, FRESH, GONE, OPEN, SPOOL
from helpers.stock import Spools

# Stock by form, short ends, minimum sealed and reorder (8i4.133.7).


def test_stock_splits_into_forms_and_short_ends_are_not_usable() -> None:
    packages = {
        SPOOL: package_forms.PackageSize("Spool", Decimal(305), Decimal(15)),
        BOX: package_forms.PackageSize("Box", Decimal(100), None),
    }
    rows = [
        (SPOOL, None, Decimal(915)),
        (None, OPEN, Decimal(120)),
        (None, GONE, Decimal(12)),
        (None, FRESH, Decimal(3)),
        (None, None, Decimal(85)),
    ]
    forms = package_forms.split(rows, packages, {OPEN: SPOOL, GONE: SPOOL, FRESH: BOX})
    assert forms.sealed == [("Spool", Decimal(3), Decimal(915))]
    assert (forms.open, forms.short_ends) == ([Decimal(3), Decimal(120)], [Decimal(12)])
    assert (forms.loose, forms.total, forms.usable) == (85, 1135, 1123)
    # Less than nothing left is the records', not a short end.
    below = package_forms.split([(None, OPEN, Decimal(-8))], packages, {OPEN: SPOOL})
    assert (below.open, below.short_ends) == ([Decimal(-8)], [])


def test_an_items_open_packages_are_ordered_fewest_left_first() -> None:
    """As on the scan page: fewest left first, ties by open package id, and
    those showing nothing or less last (Ali, 2026-11-18, inventory-tng-qskv),
    whatever order the balances arrive in."""
    opened = [(9, Decimal(40)), (4, Decimal(-8)), (7, Decimal(40)), (5, Decimal(120))]
    assert sorted(opened, key=lambda each: package_forms.fewest_left_first(*each)) == [
        (7, Decimal(40)),
        (9, Decimal(40)),
        (5, Decimal(120)),
        (4, Decimal(-8)),
    ]
    packages = {SPOOL: package_forms.PackageSize("Spool", Decimal(305), None)}
    rows = [(None, open_id, left) for open_id, left in opened]
    opened_from = {open_id: SPOOL for open_id, _ in opened}
    forms = package_forms.split(rows, packages, opened_from)
    assert forms.open == [Decimal(40), Decimal(40), Decimal(120), Decimal(-8)]


def test_an_items_sealed_packages_are_ordered_largest_first() -> None:
    """Largest package size first, ties by package id (Ali, 2026-11-18,
    inventory-tng-7mby), whatever order the balances arrive in."""
    drum, pack = 12, 9
    packages = {
        BOX: package_forms.PackageSize("Box", Decimal(100), None),
        pack: package_forms.PackageSize("Pack", Decimal(100), None),
        drum: package_forms.PackageSize("Drum", Decimal(305), None),
        SPOOL: package_forms.PackageSize("Spool", Decimal(305), None),
    }
    rows = [
        (BOX, None, Decimal(200)),
        (pack, None, Decimal(300)),
        (drum, None, Decimal(610)),
        (SPOOL, None, Decimal(915)),
    ]
    for arrived in (rows, rows[::-1], rows[1:] + rows[:1]):
        forms = package_forms.split(arrived, packages, {})
        assert forms.sealed == [
            ("Spool", 3, Decimal(915)),
            ("Drum", 2, Decimal(610)),
            ("Pack", 3, Decimal(300)),
            ("Box", 2, Decimal(200)),
        ]


def test_reorder_on_usable_stock_and_sealed_minimums() -> None:
    cable = Item(id=1, name="Cable", minimum=Decimal(400))
    rope = Item(id=2, name="Rope", minimum=None)
    spool = ItemPackage(
        id=10,
        item_id=1,
        name="Spool",
        name_shown="Spool",
        size=Decimal(305),
        size_in_items_unit=Decimal(305),
        minimum_sealed=3,
    )
    coil = ItemPackage(
        id=11,
        item_id=2,
        name="Coil",
        size=Decimal(50),
        size_in_items_unit=Decimal(50),
        minimum_sealed=1,
    )
    usable = {1: Decimal(500), 2: Decimal(50)}
    sealed = {1: {10: Decimal(2)}, 2: {11: Decimal(1)}}
    loose = ItemPackage(
        id=12,
        item_id=1,
        name="Box",
        size=Decimal(100),
        size_in_items_unit=Decimal(100),
        minimum_sealed=None,
    )
    (row,) = stock_routes.reorder_rows(
        [cable, rope], usable, sealed, [spool, coil, loose], {1}
    )
    assert (row.item, row.in_stores, row.count_first) == (cable, 500, True)
    assert row.low_sealed == [("Spool", Decimal(2), 3)]
    assert (
        stock_routes.reorder_rows([cable], {1: Decimal(400)}, {}, [], set())[0].item
        is cable
    )


def test_the_reorder_page_lists_low_sealed_packages_largest_first() -> None:
    """As the stock page lists sealed packages (inventory-tng-7mby),
    whatever order the packages arrive in."""
    cable = Item(id=1, name="Cable", minimum=None)
    sizes = {9: Decimal(100), 11: Decimal(100), 12: Decimal(305), 10: Decimal(305)}
    packages = [
        ItemPackage(
            id=package_id,
            item_id=1,
            name_shown=f"P{package_id}",
            size=size,
            size_in_items_unit=size,
            minimum_sealed=1,
        )
        for package_id, size in sizes.items()
    ]
    for arrived in (packages, packages[::-1], packages[1:] + packages[:1]):
        (row,) = stock_routes.reorder_rows([cable], {}, {}, arrived, set())
        assert [name for name, _, _ in row.low_sealed] == ["P10", "P12", "P9", "P11"]


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
    reorder = words_on(admin_client.get("/admin/stock/reorder").text)
    assert spools.cable.name in reorder
    assert "1 sealed Spool, of 2 to keep" in reorder


def test_the_stock_page_says_too_few_are_left_in_a_box_of_pieces(
    admin_client: TestClient, session: Session
) -> None:
    """A box of pieces below its too-short-to-use number is "too few left",
    a length's "short end" (Ali, 2026-11-24, inventory-tng-uwi3)."""
    spools = Spools(session, cable_unit="each")
    spools.spool.short_below = Decimal(15)
    made = spools.opened()
    spools.move(Entry(spools.cable.id, Decimal(295), Form(open_package_id=made.id)))
    session.flush()
    page = read(admin_client.get("/admin/stock").text)
    assert page.all("span", "too few left: 10 each", class_="hint block")
    assert not page.all("span", "short end: 10 each", class_="hint block")


def test_the_stock_page_lists_open_packages_fewest_left_first(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    """In a fixed order, so two loads of the same data read the same
    (inventory-tng-qskv), one showing less than nothing last."""
    spools.receive(uuid.uuid4(), spools=2)
    for cut in (Decimal(185), Decimal(0), Decimal(300), Decimal(313)):
        made = spools.opened()
        if cut:
            spools.move(Entry(spools.cable.id, cut, Form(open_package_id=made.id)))
    session.flush()
    page = read(admin_client.get("/admin/stock").text)
    lines = [
        each.text
        for each in page.all("span", class_="hint block")
        if each.text.startswith("open:")
    ]
    assert lines == [
        "open: 5 m left",
        "open: 120 m left",
        "open: 305 m left",
        "open: -8 m left",
    ]


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
        ({"minimum_sealed": "lots"}, WHOLE_PACKAGES),
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
    registered_client: TestClient, spools: Spools, amount: str
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
