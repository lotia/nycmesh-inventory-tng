"""A count of a store and its areas on one form (stock/counts_routes.py,
inventory-tng-8i4.51.4): every area at any depth, grouped by area, each
line written at the place it was found in."""

import uuid
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.models.places import Location
from inventory_tng.models.stock import LedgerTransaction, OpenPackage
from inventory_tng.stock.forms import PICK_A_PLACE
from inventory_tng.stock.ledger import Entry
from inventory_tng.stock.moments import at_place, expected
from inventory_tng.stock.paths import COUNTS

from helpers.markup import hidden_fields, options, read, text
from helpers.rows import add
from helpers.stock import Place, Spools, count_form, retire, stocked, write


class Store:
    """A store holding five radios, with Shelf A (three radios) and in it
    Bin 1 (two metres of cable), and Shelf B, empty."""

    def __init__(self, session: Session) -> None:
        self.place = stocked(session)
        store = self.place.store
        self.store = store
        self.shelf = add(session, area("Shelf A", store))
        self.bin = add(session, area("Bin 1", self.shelf))
        self.empty = add(session, area("Shelf B", store))
        self.receive(session, self.shelf, self.place.item.id, "3")
        self.receive(session, self.bin, self.place.cable.id, "2")
        self.path = f"{COUNTS}/{store.id}"

    def receive(self, session: Session, where: Location, item_id: int, n: str) -> None:
        write(
            session,
            self.place,
            kind="receipt",
            from_location_id=None,
            to_location_id=where.id,
            lines=[{"item_id": item_id, "quantity": n}],
        )

    def move(
        self, session: Session, here: Location, there: Location
    ) -> LedgerTransaction:
        """One radio moved from `here` to `there`."""
        return write(
            session,
            self.place,
            from_location_id=here.id,
            to_location_id=there.id,
            lines=[{"item_id": self.place.item.id, "quantity": "1"}],
        )


def area(name: str, parent: Location) -> Location:
    return Location(
        name=f"{name} {uuid.uuid4().hex[:6]}", kind="area", parent_id=parent.id
    )


def field(place: Location, store: Location, name: str) -> str:
    """The count form's name for a place's field."""
    return name if place.id == store.id else f"at_{place.id}_{name}"


def counts_at(session: Session, place: Location) -> list[LedgerTransaction]:
    return list(
        session.scalars(
            select(LedgerTransaction)
            .where(LedgerTransaction.kind == "count", at_place(place.id))
            .order_by(LedgerTransaction.id)
        )
    )


def test_a_store_count_lists_every_area_at_any_depth_grouped(
    admin_client: TestClient, session: Session
) -> None:
    """The store, then its areas by name, each nested one under its path,
    the empty one marked, every line named for the place it is at."""
    made = Store(session)
    page = admin_client.get(made.path)
    shown = read(page.text)
    headings = [th.text for th in shown.all("th", scope="rowgroup")]
    assert headings == [
        made.store.name,
        made.shelf.name,
        f"{made.shelf.name} \u203a {made.bin.name}",
        made.empty.name,
    ]
    empty = shown.one("th", scope="rowgroup", text=made.empty.name).parent
    assert empty is not None
    assert "text-muted" in (empty.attrs.get("class") or "")
    item, cable = made.place.item.id, made.place.cable.id
    assert shown.one("input", name=f"counted_{item}").attrs["value"] == "5"
    assert (
        shown.one("input", name=f"at_{made.shelf.id}_counted_{item}").attrs["value"]
        == "3"
    )
    assert (
        shown.one("input", name=f"at_{made.bin.id}_counted_{cable}").attrs["value"]
        == "2"
    )
    places = dict(hidden_fields(page.text))["places"].split()
    assert sorted(places) == sorted(
        str(each.id) for each in (made.store, made.shelf, made.bin, made.empty)
    )


def test_a_store_count_writes_one_count_at_each_place(
    admin_client: TestClient, session: Session
) -> None:
    """Each place's differences are written there, under its own key; an
    empty area, as found, is written as counted with no lines."""
    made = Store(session)
    item, cable = made.place.item.id, made.place.cable.id
    form = count_form(admin_client.get(made.path)) | {
        f"counted_{item}": "4",
        f"at_{made.shelf.id}_counted_{item}": "3",
        f"at_{made.bin.id}_counted_{cable}": "5",
    }
    done = admin_client.post(made.path, data=form)
    assert done.status_code == 303
    assert done.headers["location"].startswith(f"{made.path}?notice=counted")
    assert expected(session, made.store.id) == {item: Decimal(4)}
    assert expected(session, made.shelf.id) == {item: Decimal(3)}
    assert expected(session, made.bin.id) == {cable: Decimal(5)}
    key = uuid.UUID(form["key"])
    for place in (made.store, made.shelf, made.bin, made.empty):
        written = counts_at(session, place)
        assert len(written) == 1, place.name
    (lost,) = counts_at(session, made.store)
    assert lost.idempotency_key == uuid.uuid5(
        uuid.uuid5(key, str(made.store.id)), "lost"
    )
    (unchanged,) = counts_at(session, made.empty)
    assert unchanged.idempotency_key == uuid.uuid5(key, str(made.empty.id))
    # Sent again: nothing more.
    assert admin_client.post(made.path, data=form).status_code == 303
    assert len(counts_at(session, made.bin)) == 1


def test_something_found_goes_to_the_area_picked_which_comes_back_picked(
    admin_client: TestClient, session: Session
) -> None:
    """Cable found on the empty shelf is written there, and the form,
    shown again after the save, has that shelf picked."""
    made = Store(session)
    cable = made.place.cable.id
    page = admin_client.get(made.path)
    picker = read(page.text).one("select", name="extra_location_id")
    assert next((value, chosen) for value, _, chosen in options(picker)) == (
        str(made.store.id),
        True,
    )
    form = count_form(page) | {
        "extra_location_id": str(made.empty.id),
        "extra_item_id": str(cable),
        "extra_quantity": "7",
    }
    done = admin_client.post(made.path, data=form)
    assert done.status_code == 303
    assert expected(session, made.empty.id) == {cable: Decimal(7)}
    again = admin_client.get(done.headers["location"])
    assert "Counted. The records now match what you found." in text(again)
    picker = read(again.text).one("select", name="extra_location_id")
    chosen = [value for value, _, chosen in options(picker) if chosen]
    assert chosen == [str(made.empty.id)]


def test_a_retired_area_holding_stock_is_shown_retired_and_counted(
    admin_client: TestClient, session: Session
) -> None:
    """A save brought radios to a shelf after it was retired: it is shown
    in the tree, marked retired, never offered for what is found, and a
    count of it as empty writes them off. A retired one holding nothing is
    left out."""
    made = Store(session)
    item = made.place.item.id
    retire(session, made.empty)
    gone = add(session, area("Shelf C", made.store))
    retire(session, gone)
    made.move(session, made.store, made.empty)
    page = admin_client.get(made.path)
    shown = read(page.text)
    heading = shown.one(
        "th",
        scope="rowgroup",
        text=made.empty.name
        + " Retired: count what is really here, and put nothing here again.",
    )
    assert heading is not None
    assert gone.name not in text(page)
    picker = shown.one("select", name="extra_location_id")
    assert str(made.empty.id) not in [value for value, _, _ in options(picker)]
    form = count_form(page) | {f"at_{made.empty.id}_counted_{item}": "0"}
    assert admin_client.post(made.path, data=form).status_code == 303
    assert expected(session, made.empty.id) == {}


def test_a_move_between_two_areas_during_a_count_is_counted_again_at_both(
    admin_client: TestClient, session: Session
) -> None:
    """A radio moved from the shelf to the bin while the store was counted:
    the radios of both are withheld and asked for again, place by place;
    the rest is written, and the recount writes them."""
    made = Store(session)
    item, cable = made.place.item.id, made.place.cable.id
    made.receive(session, made.bin, item, "1")
    shown = count_form(admin_client.get(made.path))
    made.move(session, made.shelf, made.bin)
    asked = admin_client.post(
        made.path,
        data=shown
        | {
            f"counted_{item}": "5",
            f"at_{made.shelf.id}_counted_{item}": "3",
            f"at_{made.bin.id}_counted_{item}": "1",
            f"at_{made.bin.id}_counted_{cable}": "2",
        },
    )
    assert asked.status_code == 409
    assert "Saved 2 lines. These moved while you counted" in text(asked)
    again = count_form(asked)
    assert again[f"at_{made.shelf.id}_items"] == str(item)
    assert again[f"at_{made.bin.id}_items"] == str(item)
    assert sorted(again["places"].split()) == sorted(
        [str(made.shelf.id), str(made.bin.id)]
    )
    boxes = {
        name
        for box in read(asked.text).all("input", inputmode="decimal")
        if "counted" in (name := box.attrs["name"])
    }
    assert boxes == {
        f"at_{made.shelf.id}_counted_{item}",
        f"at_{made.bin.id}_counted_{item}",
    }
    assert expected(session, made.shelf.id) == {item: Decimal(2)}
    assert expected(session, made.bin.id) == {item: Decimal(2), cable: Decimal(2)}
    done = admin_client.post(
        made.path,
        data=again
        | {
            f"at_{made.shelf.id}_counted_{item}": "1",
            f"at_{made.bin.id}_counted_{item}": "3",
        },
    )
    assert done.status_code == 303
    assert expected(session, made.shelf.id) == {item: Decimal(1)}
    assert expected(session, made.bin.id) == {item: Decimal(3), cable: Decimal(2)}
    # The store itself, not on the recount, was counted once.
    assert len(counts_at(session, made.store)) == 1


def test_a_save_at_an_area_as_the_form_was_shown_refuses_the_whole_count(
    admin_client: TestClient, session: Session
) -> None:
    """The moment is read across every place: one more transaction at an
    area up to it than the form saw refuses the count, keeping what was
    typed."""
    made = Store(session)
    item = made.place.item.id
    shown = count_form(admin_client.get(made.path))
    moved = made.move(session, made.shelf, made.bin)
    # As if that save committed with an id inside the moment.
    form = shown | {
        "as_of": str(moved.id),
        f"at_{made.shelf.id}_counted_{item}": "9",
    }
    refused = admin_client.post(made.path, data=form)
    assert refused.status_code == 409
    assert (
        read(refused.text)
        .one("input", name=f"at_{made.shelf.id}_counted_{item}")
        .attrs["value"]
        == "9"
    )
    assert counts_at(session, made.shelf) == []


def test_an_area_retired_since_the_form_was_shown_refuses_it_whole(
    admin_client: TestClient, session: Session
) -> None:
    """The empty shelf retired while the store was counted: nothing is
    written, and the form comes back without it, keeping what was typed."""
    made = Store(session)
    item = made.place.item.id
    form = count_form(admin_client.get(made.path)) | {
        f"at_{made.shelf.id}_counted_{item}": "1"
    }
    retire(session, made.empty)
    refused = admin_client.post(made.path, data=form)
    assert refused.status_code == 409
    page = text(refused)
    assert f"Not saved: {made.empty.name} was retired while you counted" in page
    assert made.empty.id not in [
        int(each) for each in count_form(refused)["places"].split()
    ]
    assert (
        read(refused.text)
        .one("input", name=f"at_{made.shelf.id}_counted_{item}")
        .attrs["value"]
        == "1"
    )
    assert counts_at(session, made.shelf) == []


def test_a_filtered_store_count_counts_only_the_areas_typed_in(
    admin_client: TestClient, session: Session
) -> None:
    """Showing only the cable, the store and the shelves have nothing to
    show and are not said to be counted; the bin is."""
    made = Store(session)
    cable = made.place.cable.id
    path = f"{made.path}?item={cable}"
    page = admin_client.get(path)
    headings = [th.text for th in read(page.text).all("th", scope="rowgroup")]
    assert made.empty.name not in headings
    assert made.shelf.name not in headings
    form = count_form(page) | {f"at_{made.bin.id}_counted_{cable}": "2"}
    assert admin_client.post(path, data=form).status_code == 303
    assert len(counts_at(session, made.bin)) == 1
    assert counts_at(session, made.empty) == []
    assert counts_at(session, made.shelf) == []
    assert counts_at(session, made.store) == []


def test_a_recount_at_an_area_retired_since_asks_no_more_there(
    admin_client: TestClient, session: Session
) -> None:
    """A radio moved from the shelf to the empty shelf while the store was
    counted, then out again, and the empty shelf retired: the recount is
    refused once, and comes back asking only at the shelf, which then
    saves."""
    made = Store(session)
    item = made.place.item.id
    shown = count_form(admin_client.get(made.path))
    made.move(session, made.shelf, made.empty)
    asked = admin_client.post(
        made.path,
        data=shown
        | {
            f"at_{made.shelf.id}_counted_{item}": "2",
            "extra_location_id": str(made.empty.id),
            "extra_item_id": str(item),
            "extra_quantity": "1",
        },
    )
    assert asked.status_code == 409
    again = count_form(asked)
    assert sorted(again["places"].split()) == sorted(
        [str(made.shelf.id), str(made.empty.id)]
    )
    made.move(session, made.empty, made.shelf)
    retire(session, made.empty)
    recount = again | {
        f"at_{made.shelf.id}_counted_{item}": "3",
        f"at_{made.empty.id}_counted_{item}": "0",
    }
    refused = admin_client.post(made.path, data=recount)
    assert refused.status_code == 409
    assert f"Not saved: {made.empty.name} was retired" in text(refused)
    last = count_form(refused)
    assert last["places"] == str(made.shelf.id)
    done = admin_client.post(
        made.path, data=last | {f"at_{made.shelf.id}_counted_{item}": "3"}
    )
    assert done.status_code == 303
    assert expected(session, made.shelf.id) == {item: Decimal(3)}


def test_a_place_without_areas_is_counted_as_before(
    admin_client: TestClient, session: Session
) -> None:
    """No headings, no place to pick, and back to the counts after."""
    place = Place(session)
    page = admin_client.get(f"{COUNTS}/{place.van.id}")
    shown = read(page.text)
    assert shown.all("th", scope="rowgroup") == []
    assert shown.all("select", name="extra_location_id") == []
    done = admin_client.post(f"{COUNTS}/{place.van.id}", data=count_form(page))
    assert done.headers["location"].startswith(f"{COUNTS}?notice=counted_same")


def test_an_open_package_nobody_recorded_is_made_in_the_area_picked(
    admin_client: TestClient, session: Session
) -> None:
    spools = Spools(session)
    shelf = add(session, area("Shelf", spools.store))
    path = f"{COUNTS}/{spools.store.id}"
    form = count_form(admin_client.get(path)) | {
        "found_location_id": str(shelf.id),
        "found_package_id": str(spools.spool.id),
        "found_left": "40",
    }
    assert admin_client.post(path, data=form).status_code == 303
    found = session.scalars(
        select(OpenPackage).where(
            OpenPackage.key == uuid.uuid5(uuid.UUID(form["key"]), "found")
        )
    ).one()
    assert found.location_id == shelf.id
    assert expected(session, shelf.id) == {spools.cable.id: Decimal(40)}


def test_an_open_package_found_in_an_area_its_item_moved_into_is_asked_again(
    admin_client: TestClient, session: Session
) -> None:
    """Found in an empty area that cable came into while it was counted,
    and nothing else typed of cable there: not kept, and cable is asked
    for again there, so the package can be found again."""
    spools = Spools(session)
    shelf = add(session, area("Shelf", spools.store))
    cable = spools.cable.id
    path = f"{COUNTS}/{spools.store.id}"
    form = count_form(admin_client.get(path)) | {
        "found_location_id": str(shelf.id),
        "found_package_id": str(spools.spool.id),
        "found_left": "40",
    }
    spools.write(
        {
            "kind": "move",
            "from_location_id": spools.store.id,
            "to_location_id": shelf.id,
        },
        Entry(cable, Decimal(5)),
    )
    asked = admin_client.post(path, data=form)
    assert asked.status_code == 409
    assert "These moved while you counted" in text(asked)
    assert count_form(asked)[f"at_{shelf.id}_items"] == str(cable)
    made = select(OpenPackage).where(
        OpenPackage.key == uuid.uuid5(uuid.UUID(form["key"]), "found")
    )
    assert session.scalars(made).first() is None


def test_something_found_at_a_place_not_on_the_form_is_refused(
    admin_client: TestClient, session: Session
) -> None:
    made = Store(session)
    form = count_form(admin_client.get(made.path)) | {
        "extra_location_id": str(made.place.van.id),
        "extra_item_id": str(made.place.cable.id),
        "extra_quantity": "1",
    }
    refused = admin_client.post(made.path, data=form)
    assert refused.status_code == 400
    picker = read(refused.text).one("select", name="extra_location_id")
    problem = read(refused.text).one("span", id="extra_location_id-problem")
    assert problem.text == str(PICK_A_PLACE)
    assert picker is not None
    assert counts_at(session, made.store) == []


def test_an_area_moved_out_while_counted_stays_on_the_form(
    admin_client: TestClient, session: Session
) -> None:
    """The form lists its places: the shelf, moved to another store as the
    first was counted, is still shown, under its own name, when the form
    comes back with a problem, and is counted when it is sent right."""
    made = Store(session)
    item = made.place.item.id
    form = count_form(admin_client.get(made.path))
    other = add(session, Location(name=f"Other {uuid.uuid4().hex[:6]}", kind="store"))
    made.shelf.parent_id = other.id
    session.flush()
    typed = f"at_{made.shelf.id}_counted_{item}"
    back = admin_client.post(made.path, data=form | {typed: "lots"})
    assert back.status_code == 400
    headings = [th.text for th in read(back.text).all("th", scope="rowgroup")]
    assert headings[-1] == made.shelf.name
    assert admin_client.post(made.path, data=form | {typed: "2"}).status_code == 303
    assert expected(session, made.shelf.id) == {item: Decimal(2)}


def test_a_form_listing_a_place_that_is_no_id_is_not_found(
    admin_client: TestClient, session: Session
) -> None:
    made = Store(session)
    form = count_form(admin_client.get(made.path)) | {"places": f"{made.store.id} x"}
    assert admin_client.post(made.path, data=form).status_code == 404
