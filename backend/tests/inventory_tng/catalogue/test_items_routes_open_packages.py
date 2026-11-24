"""An item's open packages on its page (catalogue/items_routes.py): sorted
oldest first, the first reported used up before the rest, or by a heading:
package and place, what is left, or when opened, either way (Ali,
2026-11-18, inventory-tng-t150)."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from inventory_tng.catalogue.paths import ITEMS
from inventory_tng.models.stock import OpenPackage
from inventory_tng.stock import open_packages
from inventory_tng.stock.ledger import Entry, Form

from helpers.markup import forms, read, sorted_columns
from helpers.stock import Spools, cut, write

FIRST = datetime(2026, 11, 1, 9, tzinfo=UTC)


def three_open(session: Session, spools: Spools) -> list[OpenPackage]:
    """Three open spools, in the order they were opened: the first moved
    whole to the van, 305 m left; the second cut to 205 m in the store;
    the last cut to 255 m in the store and reported used up."""
    spools.receive(uuid.uuid4())
    made = [spools.opened(FIRST + timedelta(days=days)) for days in range(3)]
    first, second, last = made
    write(session, spools.place, lines=[cut(first, "305") | {"whole": True}])
    spools.move(Entry(spools.cable.id, Decimal(100), Form(open_package_id=second.id)))
    spools.move(Entry(spools.cable.id, Decimal(50), Form(open_package_id=last.id)))
    open_packages.report(
        session, [last.id], spools.place.volunteer.id, FIRST + timedelta(days=5)
    )
    session.flush()
    return made


def listed(client: TestClient, item_id: int, **query: str) -> list[int]:
    """The open packages the item's page lists, by id, in its order."""
    page = client.get(f"{ITEMS}/{item_id}", params=query).text
    closing = f"{ITEMS}/{item_id}/open-packages/"
    return [
        int(action.removeprefix(closing).split("/")[0])
        for _, action in forms(page)
        if action.startswith(closing)
    ]


def test_open_packages_come_oldest_first_the_reported_before_the_rest(
    admin_client: TestClient, session: Session, spools: Spools
) -> None:
    """As before they could be sorted: no heading says it sorts them."""
    first, second, last = three_open(session, spools)
    assert listed(admin_client, spools.cable.id) == [last.id, first.id, second.id]
    page = admin_client.get(f"{ITEMS}/{spools.cable.id}").text
    assert sorted_columns(page) == []
    # A sort not offered, or an order not asc or desc, is the page's own.
    odd = listed(admin_client, spools.cable.id, sort="nonsense", order="sideways")
    assert odd == [last.id, first.id, second.id]


@pytest.mark.parametrize(
    ("sort", "order", "shown"),
    [
        # Store before Van; within the store, the reported used up first, as
        # listed by default.
        ("package", "asc", (2, 1, 0)),
        ("package", "desc", (0, 2, 1)),
        ("left", "asc", (1, 2, 0)),
        ("left", "desc", (0, 2, 1)),
        ("opened", "asc", (0, 1, 2)),
        ("opened", "desc", (2, 1, 0)),
    ],
)
def test_a_heading_sorts_the_open_packages_either_way(
    admin_client: TestClient,
    session: Session,
    spools: Spools,
    sort: str,
    order: str,
    shown: tuple[int, ...],
) -> None:
    made = three_open(session, spools)
    found = listed(admin_client, spools.cable.id, sort=sort, order=order)
    assert found == [made[each].id for each in shown]


HEADINGS = {
    "package": "Package and place",
    "left": "What is left",
    "opened": "When opened",
}


@pytest.mark.parametrize("sort", HEADINGS)
def test_a_heading_says_it_sorts_and_keeps_the_admin_at_the_section(
    admin_client: TestClient, session: Session, spools: Spools, sort: str
) -> None:
    """Each heading's link sorts by it, then the other way, back to the
    open packages, not the top of the page."""
    three_open(session, spools)
    here = f"{ITEMS}/{spools.cable.id}"
    page = read(admin_client.get(here).text)
    section = page.one("section", id="open-packages")
    headings = [
        (a.spoken, a.attrs["href"]) for th in section.all("th") for a in th.all("a")
    ]
    assert headings == [
        (words, f"{here}?sort={column}#open-packages")
        for column, words in HEADINGS.items()
    ]
    asked = admin_client.get(here, params={"sort": sort}).text
    assert sorted_columns(asked) == [
        ("ascending", HEADINGS[sort], f"{here}?sort={sort}&order=desc#open-packages")
    ]
    back = admin_client.get(here, params={"sort": sort, "order": "desc"}).text
    assert sorted_columns(back) == [
        ("descending", HEADINGS[sort], f"{here}?sort={sort}#open-packages")
    ]


def test_the_last_closed_leaves_its_notice_and_no_headings(
    admin_client: TestClient, spools: Spools
) -> None:
    """Shown for its notice once none is open, the section sorts nothing."""
    here = f"{ITEMS}/{spools.cable.id}"
    params = {"notice": "open_package_closed", "section": "open-packages"}
    page = read(admin_client.get(here, params=params).text)
    section = page.one("section", id="open-packages")
    assert section.all("th") == []
