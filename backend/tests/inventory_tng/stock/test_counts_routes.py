"""Counts and adjustments (stock/counts_routes.py), and their reasons
(site_settings/site_settings_routes.py)."""

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

import pytest
from babel.messages.catalog import Catalog
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.catalogue import items
from inventory_tng.core.db_errors import RuleBroken
from inventory_tng.core.local_time import DEFAULT_ZONE, show
from inventory_tng.core.phrases import compiled, speaking
from inventory_tng.models.admins import Admin
from inventory_tng.models.base import INTEGER_MAX
from inventory_tng.models.catalogue import Item
from inventory_tng.models.places import Location
from inventory_tng.models.stock import AdjustmentReason, LedgerTransaction
from inventory_tng.places import locations
from inventory_tng.site_settings.forms import REASON_TAKEN
from inventory_tng.site_settings.paths import SETTINGS
from inventory_tng.stock.counts import record_adjustment, record_count
from inventory_tng.stock.counts_routes import COUNT_KIND
from inventory_tng.stock.forms import (
    FOUND_OR_OFF,
    OUT_OF_DATE,
    PICK_A_PLACE,
    PICK_A_REASON,
    PICK_AN_ITEM,
    UNITS,
    now_counted_in,
    shown_field,
)
from inventory_tng.stock.moments import Moment, expected, moment
from inventory_tng.stock.paths import ADJUSTMENTS, COUNTS, STOCK
from inventory_tng.web.forms import ABOVE_ZERO, RULES, WHOLE_UNITS
from inventory_tng.web.navigation import retired_holding

from helpers.concurrency import Committed
from helpers.markup import (
    Element,
    addresses,
    button,
    hidden_fields,
    links,
    options,
    read,
    text,
    words_on,
)
from helpers.rows import item_fields, shown, violates
from helpers.sign_in import new_admin, sign_in_with
from helpers.stock import (
    Place,
    adjustment_form,
    at_retired_van,
    badge,
    badge_of,
    committed_place,
    count_at,
    count_form,
    counts_written,
    netted_retired,
    retire,
    stocked,
    write,
)


def key_on(response: Response) -> str:
    return dict(hidden_fields(response.text))["key"]


def test_the_reasons_come_seeded(session: Session) -> None:
    names = session.scalars(select(AdjustmentReason.name).order_by(AdjustmentReason.id))
    assert list(names) == [
        "Damaged",
        "Lost or stolen",
        "Found",
        "Used for testing",
        "Records were wrong",
        # The app's own: closing an open package's, and correcting one
        # recorded more than moved.
        "Used up",
        "Recorded more than moved",
    ]


def test_count_a_place_goes_to_the_place_picked(admin_client: TestClient) -> None:
    """The "Count a place" form, sent as a GET, goes to that place's count;
    anything but a number goes back to the list."""
    picked = admin_client.get(f"{COUNTS}/place", params={"location_id": "7"})
    assert picked.headers["location"] == f"{COUNTS}/7"
    odd = admin_client.get(f"{COUNTS}/place", params={"location_id": "x"})
    assert odd.headers["location"] == COUNTS


def test_found_and_written_off(admin_client: TestClient, session: Session) -> None:
    place = stocked(session)
    form = adjustment_form(
        session,
        place.store,
        place.item,
        key=key_on(admin_client.get(COUNTS)),
        amount="2",
    )
    done = admin_client.post(ADJUSTMENTS, data=form)
    assert done.headers["location"] == f"{COUNTS}?notice=adjusted&section=adjust#adjust"
    admin_client.post(ADJUSTMENTS, data=form)  # Twice: written once.
    (written,) = counts_written(session)
    assert (written.kind, written.reason_id) == ("adjustment", int(form["reason_id"]))
    assert expected(session, place.store.id) == {place.item.id: Decimal(3)}
    assert "Damaged: -2 each" in words_on(admin_client.get(COUNTS).text)


@pytest.mark.parametrize("unit", ["m", "ft"])
def test_how_much_is_followed_by_the_unit_of_the_item_picked(
    admin_client: TestClient, session: Session, unit: str
) -> None:
    """Each item offered carries its unit, which the browser shows right
    after How much once it is picked, inside its label, so a screen reader
    says it too (frontend/src/admin/hints.ts, followUnits;
    inventory-tng-mdvz)."""
    place = Place(session, cable_unit=unit)
    page = read(admin_client.get(COUNTS).text)
    units = {
        option.attrs["value"]: option.attrs.get("data-unit")
        for option in page.one("select", name="item_id").all("option")
    }
    assert units[str(place.cable.id)] == unit
    assert units[str(place.item.id)] == "each"
    after = page.one("input", name="amount").parent
    assert isinstance(after, Element)
    assert "with-unit" in after.classes
    assert after.find_parent("label") is not None
    assert after.one("span", data_unit_of="item_id").text == ""


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"location_id": ""}, PICK_A_PLACE),
        ({"item_id": "999999"}, PICK_AN_ITEM),
        # A digit int() cannot read.
        ({"item_id": "²"}, PICK_AN_ITEM),
        ({"amount": "0"}, ABOVE_ZERO),
        ({"amount": "1.5"}, WHOLE_UNITS),
        ({"direction": "sideways"}, FOUND_OR_OFF),
        ({"reason_id": ""}, PICK_A_REASON),
        ({"key": "not a key"}, OUT_OF_DATE),
    ],
)
def test_an_adjustment_says_what_is_missing(
    admin_client: TestClient, session: Session, change: dict[str, str], problem: str
) -> None:
    place = stocked(session)
    form = adjustment_form(session, place.store, place.item) | change
    refused = admin_client.post(ADJUSTMENTS, data=form)
    assert refused.status_code == 400
    assert str(problem) in text(refused)


def test_the_database_holds_counts_to_their_rules(session: Session) -> None:
    """A count needs its admin; an adjustment, its reason too."""
    place = Place(session)
    with violates(session, "ck_ledger_transaction_admin_counts"):
        session.add(
            LedgerTransaction(
                time_zone=DEFAULT_ZONE,
                idempotency_key=uuid.uuid4(),
                kind="count",
                to_location_id=place.store.id,
            )
        )
    admin = session.scalars(select(Admin)).first() or Admin(username="ada")
    session.add(admin)
    session.flush()
    with violates(session, "ck_ledger_transaction_adjustment_reason"):
        session.add(
            LedgerTransaction(
                time_zone=DEFAULT_ZONE,
                idempotency_key=uuid.uuid4(),
                kind="adjustment",
                to_location_id=place.store.id,
                admin_id=admin.id,
            )
        )


def test_reasons_can_be_added_renamed_retired_and_brought_back(
    admin_client: TestClient, session: Session
) -> None:
    admin_client.post(f"{SETTINGS}/reasons", data={"name": "Given away"})
    given = session.scalars(
        select(AdjustmentReason).where(AdjustmentReason.name == "Given away")
    ).one()
    path = f"{SETTINGS}/reasons/{given.id}"
    form = {"name": "Donated", "version": shown(given)}
    admin_client.post(path, data=form | {"action": "rename"})
    assert given.name == "Donated"
    admin_client.post(path, data=form | {"action": "retire"})
    assert given.retired_at is not None
    why = read(admin_client.get(COUNTS).text).one("select", name="reason_id")
    assert "Donated" not in [words for _, words, _ in options(why)]
    admin_client.post(path, data=form | {"action": "restore"})
    assert given.retired_at is None
    taken = admin_client.post(f"{SETTINGS}/reasons", data={"name": "damaged"})
    assert str(REASON_TAKEN) in text(taken)
    too_long = form | {"name": "x" * 1000, "version": shown(given), "action": "rename"}
    assert admin_client.post(path, data=too_long).status_code == 400
    assert given.name == "Donated"
    unnamed = admin_client.post(f"{SETTINGS}/reasons", data={"name": "  "})
    assert unnamed.status_code == 400
    assert "Give it a name" in text(unnamed)
    assert admin_client.post(f"{SETTINGS}/reasons/999999").status_code == 404


def test_leftovers_of_a_retired_item_can_be_written_off(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    retire(session, place.item)
    page = text(admin_client.get(COUNTS))
    assert place.item.name in page
    form = adjustment_form(
        session, place.store, place.item, reason="Lost or stolen", amount="5"
    )
    assert admin_client.post(ADJUSTMENTS, data=form).status_code == 303
    assert expected(session, place.store.id) == {}


def _retired_and_taken(session: Session, held: str) -> Item:
    """A retired item taken from a store after it was retired, leaving it
    `held` there: short (less than nothing), zero (all of it), or netted
    (loose units taken from sealed spools: nothing in all, but not by
    form)."""
    if held == "netted":
        spools = netted_retired(session)
        assert expected(session, spools.store.id) == {}
        return spools.cable
    place = Place(session) if held == "short" else stocked(session, "2")
    retire(session, place.item)
    line = {"item_id": place.item.id, "quantity": "2", "unit": "each"}
    write(session, place, kind="install", to_location_id=None, lines=[line])
    return place.item


@pytest.mark.parametrize(
    ("held", "offered"), [("short", True), ("zero", False), ("netted", True)]
)
def test_a_retired_item_is_offered_while_its_balance_is_not_zero(
    admin_client: TestClient, session: Session, held: str, offered: bool
) -> None:
    """Taken after it was retired, the store holds less than nothing of it,
    or nothing in all but not by form: offered to adjust, so it can be put
    right; at zero, not (Ali, 2026-11-24, inventory-tng-a97r)."""
    item = _retired_and_taken(session, held)
    page = read(admin_client.get(COUNTS).text)
    choices = [value for value, _, _ in options(page.one("select", name="item_id"))]
    assert (str(item.id) in choices) is offered


@pytest.mark.end_to_end
@pytest.mark.parametrize("kind", ["count", "adjustment"])
def test_a_unit_changed_to_each_as_the_page_writes_is_asked_again(
    own_committed: Committed, make_client: Callable[..., TestClient], kind: str
) -> None:
    """The page finds 1.5 m of cable fine, then waits for the cable, held by
    a change of its unit to each; once that commits, the save finds it shown
    in m, and asks again in each beside the box, writing nothing
    (inventory-tng-8i4.104, inventory-tng-57u8). On a database of its own,
    as it would write the ledger if it were wrong."""
    place = committed_place(own_committed)
    setup = own_committed.session()
    _, totp = new_admin(setup)
    setup.commit()
    page = own_committed.session()
    client = make_client(on=page)
    assert sign_in_with(client, key=totp).status_code == 303
    if kind == "count":
        path = f"{COUNTS}/{place.store.id}"
        form = count_form(client.get(path)) | {
            "extra_item_id": str(place.cable.id),
            "extra_quantity": "1.5",
        }
    else:
        path = ADJUSTMENTS
        form = adjustment_form(
            setup,
            place.store,
            place.cable,
            key=key_on(client.get(COUNTS)),
            amount="1.5",
            direction="found",
        )
    changing = own_committed.session()
    cable = changing.get_one(Item, place.cable.id)
    items.save(changing, cable, item_fields(cable, unit="each"), seen=None)
    changing.flush()
    worker = own_committed.blocked(page, lambda: client.post(path, data=form))
    changing.commit()
    answer = worker.finished().result
    assert isinstance(answer, Response)
    assert answer.status_code == 400
    assert now_counted_in("each") in text(answer)
    assert counts_written(own_committed.session()) == []


# Stock at a retired place (docs/decisions/0003)


def signals(client: TestClient, place: Place) -> dict[str, bool]:
    """Where the van shows as retired and holding stock: each is shown by
    its page's own tests, so these only check that each clears."""
    stock = client.get(STOCK).text
    return {
        "home": f"{COUNTS}/{place.van.id}" in addresses(client.get("/admin").text),
        "badge": badge(stock) == badge_of(1),
        "stock": retired_holding(1) in stock,
        "counts": f"{place.van.name} (retired)" in text(client.get(COUNTS)),
        "form": client.get(f"{COUNTS}/{place.van.id}").status_code == 200,
    }


def test_a_retired_place_counted_to_nothing_clears_every_signal(
    admin_client: TestClient, session: Session
) -> None:
    place = at_retired_van(session)
    every = dict.fromkeys(("home", "badge", "stock", "counts", "form"), True)
    assert signals(admin_client, place) == every
    path = f"{COUNTS}/{place.van.id}"
    shown = admin_client.get(path)
    assert "is retired, but the records say something" in text(shown)
    form = count_form(shown) | {f"counted_{place.item.id}": "0"}
    done = admin_client.post(path, data=form)
    assert done.headers["location"] == f"{COUNTS}?notice=counted&section=count#count"
    assert expected(session, place.van.id) == {}
    assert signals(admin_client, place) == dict.fromkeys(every, False)
    # Sent again, it is answered as the first send was, writing nothing.
    again = admin_client.post(path, data=form)
    assert again.headers["location"] == done.headers["location"]
    assert len(counts_written(session)) == 1


def test_stock_at_a_retired_place_can_be_written_off(
    admin_client: TestClient, session: Session
) -> None:
    place = at_retired_van(session)
    form = adjustment_form(
        session, place.van, place.item, reason="Lost or stolen", amount="3"
    )
    assert admin_client.post(ADJUSTMENTS, data=form).status_code == 303
    assert expected(session, place.van.id) == {}
    assert not any(signals(admin_client, place).values())
    # Sent again, it is answered as the first send was, writing nothing.
    assert admin_client.post(ADJUSTMENTS, data=form).status_code == 303
    assert len(counts_written(session)) == 1


def test_a_retired_place_holding_nothing_is_not_offered(
    admin_client: TestClient, session: Session
) -> None:
    place = stocked(session)
    retire(session, place.van)
    assert place.van.name not in text(admin_client.get(COUNTS))
    assert admin_client.get(f"{COUNTS}/{place.van.id}").status_code == 404


def test_a_form_for_a_retired_place_holding_nothing_writes_nothing(
    admin_client: TestClient, session: Session
) -> None:
    """A stale or hand-made form, with a key not yet written: the ledger
    refuses it (location_countable), a count as for a place there is not."""
    place = stocked(session)
    retire(session, place.van)
    counted = admin_client.post(
        f"{COUNTS}/{place.van.id}",
        data={
            "key": str(uuid.uuid4()),
            "as_of": "0",
            "seen": "0",
            UNITS: shown_field({place.item.id: place.item.unit}),
            f"counted_{place.item.id}": "2",
        },
    )
    assert counted.status_code == 404
    form = adjustment_form(session, place.van, place.item, direction="found")
    adjusted = admin_client.post(ADJUSTMENTS, data=form)
    assert adjusted.status_code == 400
    assert str(RULES["location_countable"]) in text(adjusted)
    assert counts_written(session) == []


@pytest.mark.concurrency
@pytest.mark.parametrize("kind", ["count", "adjustment"])
def test_a_place_retired_as_it_is_counted_is_refused(
    committed: Committed, kind: str
) -> None:
    """The page found the store current; a retire of it, holding nothing,
    commits while the count or adjustment waits for the place (lock_place),
    which then finds it may not be counted, and writes nothing."""
    place = committed_place(committed)
    setup = committed.session()
    admin, _ = new_admin(setup)
    reason_id = setup.scalars(select(AdjustmentReason.id)).first()
    setup.commit()
    retiring = committed.session()
    store = retiring.get_one(Location, place.store.id)
    locations.retire(retiring, store, store.created_at)
    retiring.flush()
    counting = committed.session()
    key = uuid.uuid4()
    if kind == "count":
        worker = committed.blocked(
            counting,
            lambda: record_count(
                counting,
                key,
                admin.id,
                place.store.id,
                [(place.item.id, Decimal(1))],
                Moment(0, 0),
            ),
        )
    else:
        assert reason_id is not None
        worker = committed.blocked(
            counting,
            lambda: record_adjustment(
                counting,
                key,
                admin.id,
                place.store.id,
                place.item.id,
                Decimal(1),
                found=True,
                reason_id=reason_id,
            ),
        )
    retiring.commit()
    error = worker.finished().error
    assert isinstance(error, RuleBroken)
    assert error.rule == "location_countable"
    counting.rollback()


def test_a_count_shows_in_the_zone_of_its_place(
    admin_client: TestClient, session: Session
) -> None:
    """Recorded in its place's zone, shown in it: CST beside the site's EST."""
    place = stocked(session)
    place.store.time_zone = "America/Chicago"
    session.flush()
    count_at(admin_client, place.store.id, {f"counted_{place.item.id}": "5"})
    (counted,) = counts_written(session)
    assert counted.time_zone == "America/Chicago"
    assert show(counted.created_at, "America/Chicago") in text(admin_client.get(COUNTS))


def test_the_counts_page_says_its_links_start_a_task(admin_client: TestClient) -> None:
    """Only the button that changes the records promises a change
    (docs/decisions/0019)."""
    page = admin_client.get(COUNTS).text
    assert button(page, "Start a count").classes == []
    assert button(page, "Record it").classes == ["primary"]
    assert "Count it" not in page


# The counts list (Ali, 2026-11-15, inventory-tng-qwb): newest first unless
# a heading sorts them; filtered by place, kind and administrator; in pages
# of 25, 50, 100 or all, not only the newest 30.


@dataclass
class Listed:
    """A store's Damaged by Ada, a shelf's Found by Bob, then the store's
    Count by Bob: three rows the list sorts, filters and pages."""

    session: Session
    client: TestClient
    place: Place
    shelf: Location
    ada: Admin
    bob: Admin
    found_reason_id: int

    def __call__(self, **query: str) -> list[str]:
        """Each row's kind, as the list asked for by `query` shows it."""
        return kinds(self.client.get(COUNTS, params=query).text)


def kinds(page: str, words: str = "Count|Damaged|Found") -> list[str]:
    """Each row's kind, from its first words, in the page's order."""
    return [
        found[1]
        for cell in read(page).all("td")
        if (found := re.match(rf"({words}):", cell.text))
    ]


@pytest.fixture
def listed(admin_client: TestClient, session: Session) -> Listed:
    place = stocked(session)
    shelf = Location(name="Aa shelf", kind="store")
    session.add(shelf)
    ada = session.scalars(select(Admin)).one()
    bob = Admin(username="bob")
    session.add(bob)
    session.flush()
    reasons = {
        name: id_
        for name, id_ in session.execute(
            select(AdjustmentReason.name, AdjustmentReason.id)
        )
    }
    for who, where, found, reason in (
        (ada, place.store, False, "Damaged"),
        (bob, shelf, True, "Found"),
    ):
        record_adjustment(
            session,
            uuid.uuid4(),
            who.id,
            where.id,
            place.item.id,
            Decimal(1),
            found=found,
            reason_id=reasons[reason],
        )
    session.commit()
    as_of = moment(session, place.store.id)
    record_count(
        session,
        uuid.uuid4(),
        bob.id,
        place.store.id,
        [(place.item.id, Decimal(4))],
        as_of,
    )
    session.commit()
    return Listed(session, admin_client, place, shelf, ada, bob, reasons["Found"])


def test_counts_are_newest_first_and_sort_by_each_heading(listed: Listed) -> None:
    assert listed() == ["Count", "Found", "Damaged"]
    assert listed(sort="when", order="asc") == ["Damaged", "Found", "Count"]
    assert listed(sort="what") == ["Count", "Damaged", "Found"]
    assert listed(sort="what", order="desc") == ["Found", "Damaged", "Count"]
    assert listed(sort="where") == ["Found", "Count", "Damaged"]
    assert listed(sort="by") == ["Damaged", "Count", "Found"]


def test_counts_sort_by_what_in_the_language_spoken(listed: Listed) -> None:
    """By its words in the language spoken, not English."""
    catalogue = Catalog()
    catalogue.add(str(COUNT_KIND), "Inventur")
    with speaking(compiled(catalogue)):
        page = listed.client.get(COUNTS, params={"sort": "what"}).text
    assert kinds(page, "Inventur|Damaged|Found") == ["Damaged", "Found", "Inventur"]


def test_counts_filter_by_place_kind_and_administrator(listed: Listed) -> None:
    assert listed(place=str(listed.shelf.id)) == ["Found"]
    assert listed(kind="count") == ["Count"]
    assert listed(kind="adjustment") == ["Found", "Damaged"]
    assert listed(admin=str(listed.bob.id)) == ["Count", "Found"]
    assert listed(admin=str(listed.bob.id), kind="adjustment") == ["Found"]


def test_a_counts_filter_that_names_nothing_is_left_out(listed: Listed) -> None:
    assert listed(place="x", kind="audit", admin="") == listed()


def test_the_counts_filter_shows_its_choices_and_a_way_to_clear_it(
    listed: Listed,
) -> None:
    response = listed.client.get(COUNTS, params={"admin": str(listed.bob.id)})
    page = read(response.text)
    admins = options(page.one("select", name="admin"))
    assert (str(listed.bob.id), "bob", True) in admins
    places = options(page.one("select", name="place"))
    assert (str(listed.shelf.id), "Aa shelf", False) in places
    assert ("Clear", COUNTS) in links(page)


def test_a_counts_filter_matching_nothing_says_so(listed: Listed) -> None:
    assert "None is in the filter." in text(
        listed.client.get(COUNTS, params={"place": str(INTEGER_MAX)})
    )


def test_counts_come_in_pages(listed: Listed) -> None:
    for _ in range(28):
        record_adjustment(
            listed.session,
            uuid.uuid4(),
            listed.ada.id,
            listed.shelf.id,
            listed.place.item.id,
            Decimal(1),
            found=True,
            reason_id=listed.found_reason_id,
        )
    listed.session.commit()
    assert len(listed()) == 31
    assert len(listed(per="25")) == 25
    assert len(listed(per="25", page="2")) == 6
    assert "Page 2 of 2" in text(
        listed.client.get(COUNTS, params={"per": "25", "page": "2"})
    )
