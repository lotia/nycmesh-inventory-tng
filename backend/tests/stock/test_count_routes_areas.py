"""A count of a store and its areas on one form (stock/count_routes.py,
inventory-tng-8i4.51.4): every area at any depth, grouped by area, each
line written at the place it was found in."""

import uuid
from decimal import Decimal
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.core.phrases import PATH_BETWEEN
from inventory_tng.models.catalogue import Item
from inventory_tng.models.places import Location
from inventory_tng.models.stock import OpenPackage
from inventory_tng.stock.count_forms import ON_THE_FORM_TWICE, TYPE_IT_HERE
from inventory_tng.stock.forms import PICK_A_PLACE
from inventory_tng.stock.ledger import Entry
from inventory_tng.stock.moments import expected
from inventory_tng.stock.paths import COUNTS

from helpers.markup import forms, hidden_fields, options, read, text
from helpers.rows import add, tag
from helpers.stock import Place, Spools, count_form, retire, write
from helpers.store_areas import Store, area, counts_at, field, found_on


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
    assert sorted(places) == sorted(str(each.id) for each in made.every_place)


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
    for place in made.every_place:
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


def test_something_else_here_listed_in_the_area_picked_is_refused(
    admin_client: TestClient, session: Session
) -> None:
    """Radios found on Shelf A, which lists radios: nothing is written, the
    page says to type them on their line there, marks both fields, and
    keeps everything typed (Ali, 2026-11-21, inventory-tng-fh0e)."""
    made = Store(session)
    item = made.place.item.id
    page = admin_client.get(made.path)
    picker = read(page.text).one("select", name="extra_item_id")
    assert str(item) in [value for value, _, _ in options(picker)]
    line = f"at_{made.shelf.id}_counted_{item}"
    form = count_form(page) | {
        f"counted_{item}": "6",
        line: "4",
        "extra_location_id": str(made.shelf.id),
        "extra_item_id": str(item),
        "extra_quantity": "2",
    }
    refused = admin_client.post(made.path, data=form)
    assert refused.status_code == 400
    name = made.place.item.name_shown
    assert (
        f"{name} is already listed under {made.shelf.name}. Type what you found "
        "on its line there. Nothing was saved, and what you typed is kept."
    ) in text(refused)
    assert "more than once" not in text(refused)
    shown = read(refused.text)
    assert shown.one("span", id=f"{line}-problem").text == str(TYPE_IT_HERE)
    assert (
        shown.one("span", id="extra_item_id-problem").text
        == f"Listed under {made.shelf.name} already."
    )
    assert shown.one("input", name=line).attrs["value"] == "4"
    assert shown.one("input", name=f"counted_{item}").attrs["value"] == "6"
    assert shown.one("input", name="extra_quantity").attrs["value"] == "2"
    for picker_name, value in (
        ("extra_location_id", made.shelf.id),
        ("extra_item_id", item),
    ):
        chosen = [
            each
            for each, _, picked in options(shown.one("select", name=picker_name))
            if picked
        ]
        assert chosen == [str(value)], picker_name
    for place in made.every_place:
        assert counts_at(session, place) == []
    # Typed on its line, the same form saves.
    done = admin_client.post(
        made.path,
        data=form | {line: "6", "extra_item_id": "", "extra_quantity": ""},
    )
    assert done.status_code == 303
    assert expected(session, made.shelf.id) == {item: Decimal(6)}


@pytest.mark.parametrize("where", ["store", "bin"])
def test_something_else_here_listed_is_said_as_the_form_names_the_place(
    admin_client: TestClient, session: Session, where: str
) -> None:
    """Radios found in the store itself, on its unprefixed line, named by
    its name; cable found in Bin 1, inside Shelf A, named by its path below
    the store, as its heading names it. Nothing is written."""
    made = Store(session)
    place, item, named = {
        "store": (made.store, made.place.item, made.store.name),
        "bin": (
            made.bin,
            made.place.cable,
            PATH_BETWEEN.join((made.shelf.name, made.bin.name)),
        ),
    }[where]
    # Its line is sent empty, as a browser sends it.
    line = field(place, made.store, f"counted_{item.id}")
    form = count_form(admin_client.get(made.path)) | {
        line: "",
        "extra_location_id": str(place.id),
        "extra_item_id": str(item.id),
        "extra_quantity": "1",
    }
    refused = admin_client.post(made.path, data=form)
    assert refused.status_code == 400
    assert f"{item.name_shown} is already listed under {named}." in text(refused)
    shown = read(refused.text)
    assert shown.one("span", id=f"{line}-problem").text == str(TYPE_IT_HERE)
    assert (
        shown.one("span", id="extra_item_id-problem").text
        == f"Listed under {named} already."
    )
    assert counts_at(session, place) == []


def test_something_else_here_listed_in_another_place_is_counted_where_picked(
    admin_client: TestClient, session: Session
) -> None:
    """Radios, listed in the store and on Shelf A, found on Shelf B, which
    lists none: counted there."""
    made = Store(session)
    item = made.place.item.id
    form = count_form(admin_client.get(made.path)) | {
        "extra_location_id": str(made.empty.id),
        "extra_item_id": str(item),
        "extra_quantity": "2",
    }
    assert admin_client.post(made.path, data=form).status_code == 303
    assert expected(session, made.empty.id) == {item: Decimal(2)}


def test_an_item_on_an_area_twice_and_found_there_too_is_said_twice(
    admin_client: TestClient, session: Session
) -> None:
    """A hand-made form naming Shelf A's radios on two lines and found
    there besides: that it is twice is said above the form."""
    made = Store(session)
    item = made.place.item.id
    form = count_form(admin_client.get(made.path)) | {
        f"at_{made.shelf.id}_counted_{item}": "3",
        f"at_{made.shelf.id}_counted_0{item}": "4",
        "extra_location_id": str(made.shelf.id),
        "extra_item_id": str(item),
        "extra_quantity": "2",
    }
    refused = admin_client.post(made.path, data=form)
    assert refused.status_code == 400
    assert "An item is on this form more than once." in text(refused)
    assert str(ON_THE_FORM_TWICE) in text(refused)
    assert counts_at(session, made.shelf) == []


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
        for box in read(asked.text).all("input", inputmode="numeric")
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


def test_a_recount_of_areas_refused_for_a_bad_number_keeps_their_figures(
    admin_client: TestClient, session: Session
) -> None:
    """A radio moved from the shelf to the bin while the store was counted;
    the recount, refused for a bad number in the bin, still says what
    each was typed, came in and went out (inventory-tng-up4h)."""
    made = Store(session)
    item = made.place.item.id
    made.receive(session, made.bin, item, "1")
    shown = count_form(admin_client.get(made.path))
    made.move(session, made.shelf, made.bin)
    asked = admin_client.post(
        made.path,
        data=shown
        | {
            f"at_{made.shelf.id}_counted_{item}": "3",
            f"at_{made.bin.id}_counted_{item}": "1",
        },
    )
    again = count_form(asked)
    refused = admin_client.post(
        made.path, data=again | {f"at_{made.bin.id}_counted_{item}": "x"}
    )
    assert refused.status_code == 400
    page = text(refused)
    assert "0 in, 1 out; you typed 3" in page
    assert "1 in, 0 out; you typed 1" in page
    assert count_form(refused) == again


def test_something_else_here_held_in_an_area_but_not_recounted_is_refused(
    admin_client: TestClient, session: Session
) -> None:
    """The recount asks for radios on Shelf A and in Bin 1; something else
    here names the cable Bin 1 holds: refused, its line added under Bin 1,
    nothing written. Found on Shelf A, which holds none, it is counted there
    (Ali, 2026-11-22, inventory-tng-oazl)."""
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
    again = count_form(asked)
    assert again[f"at_{made.bin.id}_items"] == str(item)
    recounted = {
        f"at_{made.shelf.id}_counted_{item}": "1",
        f"at_{made.bin.id}_counted_{item}": "3",
        "extra_item_id": str(cable),
        "extra_quantity": "1",
    }
    refused = admin_client.post(
        made.path,
        data=again | recounted | {"extra_location_id": str(made.bin.id)},
    )
    assert refused.status_code == 400
    where = PATH_BETWEEN.join([made.shelf.name, made.bin.name])
    name = made.place.cable.name_shown
    assert f"{name} is already listed under {where}." in text(refused)
    line = f"at_{made.bin.id}_counted_{cable}"
    page = read(refused.text)
    assert page.one("span", id=f"{line}-problem").text == str(TYPE_IT_HERE)
    assert (
        page.one("span", id="extra_item_id-problem").text
        == f"Listed under {where} already."
    )
    assert sorted(count_form(refused)[f"at_{made.bin.id}_items"].split()) == sorted(
        [str(item), str(cable)]
    )
    assert expected(session, made.bin.id) == {item: Decimal(2), cable: Decimal(2)}
    # Found on Shelf A, which holds no cable, it is counted there.
    done = admin_client.post(
        made.path,
        data=again | recounted | {"extra_location_id": str(made.shelf.id)},
    )
    assert done.status_code == 303
    assert expected(session, made.shelf.id) == {item: Decimal(1), cable: Decimal(1)}
    assert expected(session, made.bin.id) == {item: Decimal(3), cable: Decimal(2)}


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


def test_something_else_here_offers_only_what_a_place_it_may_be_found_lacks(
    admin_client: TestClient, session: Session
) -> None:
    """Radios listed at the store and every current area, but not at a
    retired shelf holding cable: no place Found at offers lacks them, so
    they are not offered; cable, which the store lacks, is."""
    made = Store(session)
    item, cable = made.place.item.id, made.place.cable.id
    made.move(session, made.store, made.bin)
    made.move(session, made.store, made.empty)
    retire(session, made.empty)
    write(
        session,
        made.place,
        from_location_id=made.bin.id,
        to_location_id=made.empty.id,
        lines=[{"item_id": cable, "quantity": "1", "unit": made.place.cable.unit}],
    )
    made.move(session, made.empty, made.store)
    page = read(admin_client.get(made.path).text)
    picker = page.one("select", name="extra_item_id")
    offered = [value for value, _, _ in options(picker)]
    assert str(item) not in offered
    assert str(cable) in offered


def test_a_store_filtered_to_an_item_in_an_area_shows_no_line_of_it_at_the_store(
    admin_client: TestClient, session: Session
) -> None:
    """Asked for the cable, which only the bin holds: the store gets no
    line of it, so a browser sends nothing there; asked for an item no
    place holds, the store does, to count it at."""
    made = Store(session)
    cable = made.place.cable.id
    page = read(admin_client.get(f"{made.path}?item={cable}").text)
    assert page.one("input", name=f"at_{made.bin.id}_counted_{cable}") is not None
    assert not page.all("input", name=f"counted_{cable}")
    spare = add(
        session, Item(name=f"Spare {tag()}", category_id=made.place.item.category_id)
    )
    page = read(admin_client.get(f"{made.path}?item={spare.id}").text)
    assert page.one("input", name=f"counted_{spare.id}").attrs["value"] == "0"


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


@pytest.mark.parametrize("by", ["item", "q"])
def test_a_filtered_store_count_with_nothing_typed_is_shown_again(
    admin_client: TestClient, session: Session, by: str
) -> None:
    """Showing only the cable, by id or by search, every line left blank
    and something else here picked but not typed: nothing is written at
    any place, and the form comes back, filtered the same and with the
    place picked, saying nothing was counted (inventory-tng-fxsy)."""
    made = Store(session)
    cable = made.place.cable
    query = urlencode({by: cable.id if by == "item" else cable.name})
    path = f"{made.path}?{query}"
    form = count_form(admin_client.get(path)) | {
        "extra_location_id": str(made.bin.id),
        "extra_item_id": "",
        "extra_quantity": "",
        f"at_{made.bin.id}_counted_{cable.id}": "",
    }
    refused = admin_client.post(path, data=form)
    assert refused.status_code == 400
    assert "Nothing was typed, so nothing was counted." in text(refused)
    assert ("post", path) in forms(refused.text)
    picker = read(refused.text).one("select", name="extra_location_id")
    assert [value for value, _, chosen in options(picker) if chosen] == [
        str(made.bin.id)
    ]
    assert count_form(refused)["key"] == form["key"]
    for place in made.every_place:
        assert counts_at(session, place) == [], place.name


def test_a_store_count_filtered_to_nothing_and_sent_is_shown_again(
    admin_client: TestClient, session: Session
) -> None:
    """A search matching nothing shows no lines; sent so, nothing is
    written and the form says nothing was counted (inventory-tng-fxsy)."""
    made = Store(session)
    path = f"{made.path}?q=nothing-{tag()}"
    refused = admin_client.post(path, data=count_form(admin_client.get(path)))
    assert refused.status_code == 400
    assert "Nothing was typed, so nothing was counted." in text(refused)
    for place in made.every_place:
        assert counts_at(session, place) == [], place.name


def test_a_filtered_store_count_with_only_something_else_here_is_counted(
    admin_client: TestClient, session: Session
) -> None:
    """Showing only the cable, every line blank but something else here
    typed: that is something typed, so the place picked is counted and
    no other (inventory-tng-fxsy)."""
    made = Store(session)
    path = f"{made.path}?item={made.place.cable.id}"
    form = count_form(admin_client.get(path)) | {
        "extra_location_id": str(made.empty.id),
        "extra_item_id": str(made.place.item.id),
        "extra_quantity": "1",
    }
    assert admin_client.post(path, data=form).status_code == 303
    assert len(counts_at(session, made.empty)) == 1
    for place in (made.store, made.shelf, made.bin):
        assert counts_at(session, place) == [], place.name


def test_an_unfiltered_store_count_with_nothing_typed_is_still_counted(
    admin_client: TestClient, session: Session
) -> None:
    """Showing everything, a send with every line blank counts every
    place, as a count of one place does."""
    made = Store(session)
    done = admin_client.post(made.path, data=count_form(admin_client.get(made.path)))
    assert done.status_code == 303
    for place in made.every_place:
        assert len(counts_at(session, place)) == 1, place.name


def test_a_recount_at_an_area_retired_since_asks_no_more_there(
    admin_client: TestClient, session: Session
) -> None:
    """A radio moved from the shelf to the empty shelf while the store was
    counted, then out again, and the empty shelf retired: the recount is
    refused once, as the shelf emptied, and comes back asking only at the
    shelf, which then saves."""
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
    assert f"Not saved: {made.empty.name} was emptied while you counted" in text(
        refused
    )
    # The shelf's figures as first shown; none of the empty shelf's.
    assert "0 in, 1 out; you typed 2" in text(refused)
    assert f"at_{made.empty.id}_while_{item}" not in count_form(refused)
    # The radio it sent back is under the move to the shelf, whose own line
    # counts what it held before (inventory-tng-ygk5).
    moved = f"Moved to {made.shelf.name}"
    assert read(refused.text).one("th", scope="rowgroup", text=moved) is not None
    last = count_form(refused)
    assert last["places"] == str(made.shelf.id)
    done = admin_client.post(
        made.path, data=last | {f"at_{made.shelf.id}_counted_{item}": "2"}
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
    form = count_form(admin_client.get(path)) | found_on(shelf, spools.spool)
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
    form = count_form(admin_client.get(path)) | found_on(shelf, spools.spool)
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
    other = add(session, Location(name=f"Other {tag()}", kind="store"))
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
