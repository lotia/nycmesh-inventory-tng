"""A count of a store and its areas when an area on it is retired, or a
retired one emptied, while it is counted (stock/counts_routes.py, Ali,
2026-11-22, inventory-tng-ygk5): refused whole, the form shown again
saying where its items went, with a way to bring it back."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy.orm import Session

from inventory_tng.models.places import Location
from inventory_tng.stock import queries
from inventory_tng.stock.count_groups import PATH_BETWEEN
from inventory_tng.stock.moments import expected

from helpers.markup import Element, button, options, read, text
from helpers.rows import add
from helpers.stock import count_form, retire, write
from helpers.store_areas import Store, area, counts_at, field


class Emptied:
    """Store, and in it Shelf C, retired, holding two radios a save brought
    after it was retired; its count form as shown then."""

    def __init__(self, session: Session, client: TestClient) -> None:
        self.made = made = Store(session)
        self.item = made.place.item.id
        self.gone = add(session, area("Shelf C", made.store))
        retire(session, self.gone)
        self.send(session, made.store, self.gone, "2")
        self.shown = count_form(client.get(made.path))
        assert str(self.gone.id) in self.shown["places"].split()

    def send(self, session: Session, here: Location, there: Location, n: str) -> None:
        """`n` radios moved from `here` to `there`."""
        write(
            session,
            self.made.place,
            from_location_id=here.id,
            to_location_id=there.id,
            lines=[{"item_id": self.item, "quantity": n}],
        )

    def counted(self, place: Location) -> str:
        """The count form's name for the radios counted at `place`."""
        return field(place, self.made.store, f"counted_{self.item}")

    def arrived(self, place: Location, area: Location | None = None) -> str:
        """The count form's name for how many radios arrived at `place`
        from `area`, Shelf C if none."""
        name = f"arrived_{(area or self.gone).id}_loose_{self.item}"
        return field(place, self.made.store, name)


def emptied_words(place: str) -> str:
    return (
        f"Not saved: {place} was emptied while you counted, so the records show "
        "no items there now. Move details are shown below, and your other counts "
        "are kept. Confirm and save again."
    )


def heading(page: Response, words: str) -> Element:
    return read(page.text).one("th", scope="rowgroup", text=words)


def rows(page: Response) -> list[Element]:
    return read(page.text).all("tr")


def heading_row(page: Response, words: str) -> Element:
    """The row of the heading with these words."""
    (row,) = [tr for tr in rows(page) if tr.all("th", scope="rowgroup", text=words)]
    return row


def cells_by(page: Response, name: str) -> list[str]:
    """The words of the cells of the row of the box named `name`."""
    (row,) = [tr for tr in rows(page) if tr.all("input", name=name)]
    return [cell.text for cell in row.all("td")]


def found_at(page: Response) -> list[str | None]:
    """The places Something else here's Found at offers."""
    picker = read(page.text).one("select", name="extra_location_id")
    return [value for value, _, _ in options(picker)]


def bring_back(
    client: TestClient, made: Store, page: Response, typed: dict[str, str], area_id: int
) -> Response:
    """The Bring it back button pressed on `page`, with `typed`."""
    pressed = button(page.text, "Bring it back")
    assert pressed.attrs["formaction"] == f"{made.path}/bring-back"
    assert pressed.attrs["name"] == "area_id"
    data = count_form(page) | typed | {"area_id": str(area_id)}
    return client.post(pressed.attrs["formaction"], data=data)


def test_a_retired_area_emptied_while_counted_says_where_its_items_went(
    admin_client: TestClient, session: Session
) -> None:
    """Shelf C's two radios moved to Shelf B while the store was counted: the
    count is refused whole, and the form comes back without Shelf C, the
    radios under "Moved to Shelf B", "From Shelf C", with the amount moved
    and Fix amount, its box holding that amount; Shelf B's own lines as
    before the move (it held none); what was typed kept, and Shelf C not
    offered for what is found but there to bring back."""
    setup = Emptied(session, admin_client)
    made, gone = setup.made, setup.gone
    setup.send(session, gone, made.empty, "2")
    typed = {setup.counted(made.shelf): "3"}
    refused = admin_client.post(made.path, data=setup.shown | typed)
    assert refused.status_code == 409
    words = text(refused)
    assert emptied_words(gone.name) in words
    again = count_form(refused)
    assert str(gone.id) not in again["places"].split()
    assert again["gone"] == str(gone.id)
    assert again["gone_since"] == setup.shown["as_of"]
    shown = read(refused.text)
    assert heading(refused, f"Moved to {made.empty.name}")
    assert shown.one("strong", text=f"From {gone.name}")
    arrived = shown.one("input", name=setup.arrived(made.empty))
    assert arrived.attrs["value"] == "2"
    assert arrived.attrs["inputmode"] == "numeric"
    item = made.place.item.name_shown
    assert cells_by(refused, setup.arrived(made.empty))[0] == f"{item} · 2 moved"
    assert shown.one("summary", text="Fix amount")
    assert "How many arrived?" in words
    assert shown.all("input", name=setup.counted(made.empty)) == []
    # Shown only in the browser, which keeps it up to date.
    total = shown.one("tr", **{"data-total": f"{made.empty.id}-{setup.item}"})
    assert "hidden" in total.attrs
    assert (
        total.one("span", role="status").text
        == f"{made.empty.name} after saving: 2 each"
    )
    assert shown.one("input", name=setup.counted(made.shelf)).attrs["value"] == "3"
    assert str(gone.id) not in found_at(refused)
    assert f"{gone.name} isn't offered: it's retired." in words
    assert f"Things still at {gone.name}?" in words
    # Save first, so Enter saves, though shown after Bring it back.
    first = shown.one("form", method="post", action=made.path).all("button")[0]
    assert first.text == "Save the count"
    assert "order-last" in first.classes
    assert (
        f"Brings it back and shows this form again with {gone.name} listed and "
        "everything you typed kept."
    ) in words
    for place in (*made.every_place, gone):
        assert counts_at(session, place) == [], place.name


def test_items_moved_to_two_areas_get_a_section_each(
    admin_client: TestClient, session: Session
) -> None:
    """One radio to Shelf A, one to Shelf B: a section for each, and Shelf
    A's own line as it was before the move."""
    setup = Emptied(session, admin_client)
    made, gone = setup.made, setup.gone
    setup.send(session, gone, made.shelf, "1")
    setup.send(session, gone, made.empty, "1")
    refused = admin_client.post(made.path, data=setup.shown)
    for there in (made.shelf, made.empty):
        assert heading(refused, f"Moved to {there.name}")
    # Shelf A's own three radios, without the one moved there.
    assert cells_by(refused, setup.counted(made.shelf))[1] == "3 each"
    shown = read(refused.text)
    total = shown.one("tr", **{"data-total": f"{made.shelf.id}-{setup.item}"})
    assert (
        total.one("span", role="status").text
        == f"{made.shelf.name} after saving: 4 each"
    )


def test_items_taken_out_of_the_store_are_listed_with_no_box(
    admin_client: TestClient, session: Session
) -> None:
    """Shelf C's radios moved to a van: listed under "Taken out of" the
    store, with nothing to type."""
    setup = Emptied(session, admin_client)
    made = setup.made
    setup.send(session, setup.gone, made.place.van, "2")
    refused = admin_client.post(made.path, data=setup.shown)
    assert refused.status_code == 409
    assert heading(refused, f"Taken out of {made.store.name}")
    row = next(
        tr
        for tr in read(refused.text).all("tr")
        if [td.text for td in tr.all("td")][:2]
        == [made.place.item.name_shown, "2 each"]
    )
    assert row.all("input") == []


def test_bring_it_back_restores_the_area_and_shows_the_form_again(
    admin_client: TestClient, session: Session
) -> None:
    """Bring it back: Shelf C is current again, and the same form comes back
    with what was typed, Shelf C listed with nothing recorded and offered
    for what is found, and the move still shown. Found there and the move
    put at none, Shelf C holds them again."""
    setup = Emptied(session, admin_client)
    made, gone = setup.made, setup.gone
    setup.send(session, gone, made.empty, "2")
    refused = admin_client.post(made.path, data=setup.shown)
    typed = {setup.counted(made.shelf): "3", setup.arrived(made.empty): "0"}
    back = bring_back(admin_client, made, refused, typed, gone.id)
    assert back.status_code == 200
    session.refresh(gone)
    assert gone.retired_at is None
    alert = read(back.text).one("p", class_="warning", role="status")
    assert alert.text == (
        f"{gone.name} is back, and what you typed is kept. Add what's at "
        f"{gone.name} with Something else here, then save the count."
    )
    form = count_form(back)
    assert form["key"] == count_form(refused)["key"]
    assert str(gone.id) in form["places"].split()
    every = rows(back)
    after = every[every.index(heading_row(back, gone.name)) + 1]
    assert after.text == "Nothing recorded here."
    assert str(gone.id) in found_at(back)
    words = text(back)
    assert "isn't offered" not in words
    assert "Things still at" not in words
    assert heading(back, f"Moved to {made.empty.name}")
    shown = read(back.text)
    for name, value in typed.items():
        assert shown.one("input", name=name).attrs["value"] == value
    found = {
        "extra_location_id": str(gone.id),
        "extra_item_id": str(setup.item),
        "extra_quantity": "2",
    }
    saved = admin_client.post(made.path, data=form | typed | found)
    assert saved.status_code == 303
    assert expected(session, gone.id) == {setup.item: Decimal(2)}
    assert expected(session, made.empty.id) == {}


def test_bring_it_back_of_an_area_back_already_shows_the_form(
    admin_client: TestClient, session: Session
) -> None:
    """Brought back meanwhile on its own page: the form, as if just now."""
    setup = Emptied(session, admin_client)
    made, gone = setup.made, setup.gone
    setup.send(session, gone, made.empty, "2")
    refused = admin_client.post(made.path, data=setup.shown)
    gone.retired_at = None
    session.flush()
    back = bring_back(admin_client, made, refused, {}, gone.id)
    assert back.status_code == 200
    assert str(gone.id) in count_form(back)["places"].split()


def test_bring_it_back_refused_by_a_rule_says_so(
    admin_client: TestClient, session: Session
) -> None:
    """A bin in a retired shelf can't come back while its shelf is retired:
    the form again, saying so, the bin still gone."""
    setup = Emptied(session, admin_client)
    made = setup.made
    shelf = add(session, area("Shelf D", made.store))
    inner = add(session, area("Bin X", shelf))
    retire(session, shelf, inner)
    setup.send(session, made.store, inner, "1")
    shown = count_form(admin_client.get(made.path))
    setup.send(session, inner, made.empty, "1")
    refused = admin_client.post(made.path, data=shown)
    label = PATH_BETWEEN.join([shelf.name, inner.name])
    assert emptied_words(label) in text(refused)
    back = bring_back(admin_client, made, refused, {}, inner.id)
    assert back.status_code == 409
    assert "Bring it back" in back.text
    session.refresh(inner)
    assert inner.retired_at is not None


def test_bring_it_back_of_an_area_not_gone_from_the_form_is_not_found(
    admin_client: TestClient, session: Session
) -> None:
    setup = Emptied(session, admin_client)
    made = setup.made
    setup.send(session, setup.gone, made.empty, "2")
    refused = admin_client.post(made.path, data=setup.shown)
    form = count_form(refused)
    for area_id in (str(made.empty.id), "x"):
        sent = form | {"area_id": area_id}
        assert admin_client.post(f"{made.path}/bring-back", data=sent).status_code == (
            404
        )
    unread = form | {"area_id": str(setup.gone.id), "gone_since": "x"}
    assert admin_client.post(f"{made.path}/bring-back", data=unread).status_code == 404


def test_an_empty_area_retired_while_counted_is_said_removed(
    admin_client: TestClient, session: Session
) -> None:
    """The empty Shelf B retired while the store was counted, nothing moved:
    refused whole, saying so, its heading dimmed, what was typed kept."""
    made = Store(session)
    item = made.place.item.id
    typed = {f"at_{made.shelf.id}_counted_{item}": "1"}
    form = count_form(admin_client.get(made.path)) | typed
    retire(session, made.empty)
    refused = admin_client.post(made.path, data=form)
    assert refused.status_code == 409
    assert (
        f"Not saved: {made.empty.name} was retired while you counted, so it is no "
        "longer on this form. What you typed for the other areas is kept: check "
        "it and save again."
    ) in text(refused)
    removed = heading_row(
        refused, f"{made.empty.name} (retired): removed from this form."
    )
    assert "text-muted" in removed.classes
    assert made.empty.id not in [
        int(each) for each in count_form(refused)["places"].split()
    ]
    shown = read(refused.text).one("input", name=f"at_{made.shelf.id}_counted_{item}")
    assert shown.attrs["value"] == "1"
    assert "Bring it back" not in refused.text
    for place in made.every_place:
        assert counts_at(session, place) == [], place.name


def test_an_area_emptied_and_another_retired_are_named_together(
    admin_client: TestClient, session: Session
) -> None:
    """Shelf C emptied and the empty Shelf B retired in one count: refused
    with both alerts, one under the other (Ali, 2026-11-22), Shelf B's
    heading dimmed, and both named as not offered for what is found."""
    setup = Emptied(session, admin_client)
    made, gone = setup.made, setup.gone
    setup.send(session, gone, made.shelf, "2")
    retire(session, made.empty)
    refused = admin_client.post(made.path, data=setup.shown)
    words = text(refused)
    alerts = [each.text for each in read(refused.text).all("p", role="alert")]
    assert alerts == [
        emptied_words(gone.name),
        (
            f"Not saved: {made.empty.name} was retired while you counted, so it "
            "is no longer on this form. What you typed for the other areas is "
            "kept: check it and save again."
        ),
    ]
    assert heading(refused, f"{made.empty.name} (retired): removed from this form.")
    first, second = sorted([gone.name, made.empty.name], key=str.lower)
    assert f"{first} and {second} aren't offered: they're retired." in words
    assert sorted(count_form(refused)["gone"].split()) == sorted(
        [str(gone.id), str(made.empty.id)]
    )


def test_a_moved_item_the_filter_leaves_out_is_not_shown(
    admin_client: TestClient, session: Session
) -> None:
    """A count of cable only, Shelf C holding a metre of it too: of all
    Shelf C sent to Shelf B meanwhile, only the cable is shown."""
    setup = Emptied(session, admin_client)
    made, gone = setup.made, setup.gone
    cable = made.place.cable.id
    lines = [{"item_id": cable, "quantity": "1"}]
    write(
        session,
        made.place,
        from_location_id=made.bin.id,
        to_location_id=gone.id,
        lines=lines,
    )
    path = f"{made.path}?item={cable}"
    shown = count_form(admin_client.get(path))
    setup.send(session, gone, made.empty, "2")
    write(
        session,
        made.place,
        from_location_id=gone.id,
        to_location_id=made.empty.id,
        lines=lines,
    )
    typed = {f"at_{gone.id}_counted_{cable}": "1"}
    refused = admin_client.post(path, data=shown | typed)
    assert refused.status_code == 409
    assert emptied_words(gone.name) in text(refused)
    assert heading(refused, f"Moved to {made.empty.name}")
    page = read(refused.text)
    cable_arrived = f"at_{made.empty.id}_arrived_{gone.id}_loose_{cable}"
    assert page.all("input", name=cable_arrived)
    assert page.all("input", name=setup.arrived(made.empty)) == []


def test_a_recount_at_an_area_emptied_since_lists_what_left_it(
    admin_client: TestClient, session: Session
) -> None:
    """One of Shelf C's radios moved to Shelf A while the store was counted:
    asked again at both. Then its last went to a van: the recount is
    refused, asking again only at Shelf A, the radio listed as taken out."""
    setup = Emptied(session, admin_client)
    made, gone = setup.made, setup.gone
    setup.send(session, gone, made.shelf, "1")
    typed = {setup.counted(made.shelf): "3", setup.counted(gone): "2"}
    asked = admin_client.post(made.path, data=setup.shown | typed)
    assert asked.status_code == 409
    recount = count_form(asked)
    assert sorted(recount["places"].split()) == sorted(
        [str(made.shelf.id), str(gone.id)]
    )
    setup.send(session, gone, made.place.van, "1")
    refused = admin_client.post(made.path, data=recount | typed)
    assert refused.status_code == 409
    assert emptied_words(gone.name) in text(refused)
    assert count_form(refused)["places"] == str(made.shelf.id)
    assert heading(refused, f"Taken out of {made.store.name}")


def test_an_item_moved_from_two_areas_to_one_place_has_a_part_each(
    admin_client: TestClient, session: Session
) -> None:
    """Radios moved to Shelf B from Shelf C and from Shelf D, both retired:
    one section, a part for each area, each move with its own box."""
    setup = Emptied(session, admin_client)
    made, gone = setup.made, setup.gone
    other = add(session, area("Shelf D", made.store))
    retire(session, other)
    setup.send(session, made.store, other, "1")
    shown = count_form(admin_client.get(made.path))
    setup.send(session, gone, made.empty, "2")
    setup.send(session, other, made.empty, "1")
    refused = admin_client.post(made.path, data=shown)
    assert refused.status_code == 409
    item = made.place.item.name_shown
    assert cells_by(refused, setup.arrived(made.empty))[0] == f"{item} · 2 moved"
    assert cells_by(refused, setup.arrived(made.empty, other))[0] == (
        f"{item} · 1 moved"
    )
    assert heading(refused, f"Moved to {made.empty.name}")
    page = read(refused.text)
    parts = [each.text for each in page.all("strong") if each.text.startswith("From")]
    assert parts == [f"From {gone.name}", f"From {other.name}"]


def test_bring_it_back_of_a_place_not_in_the_store_is_not_found(
    admin_client: TestClient, session: Session
) -> None:
    """A hand-made form naming as gone the store itself, or a retired area
    of another store: nothing is brought back."""
    setup = Emptied(session, admin_client)
    made = setup.made
    elsewhere = add(session, area("Shelf Z", Store(session).store))
    retire(session, elsewhere, made.store)
    setup.send(session, setup.gone, made.empty, "2")
    form = count_form(admin_client.post(made.path, data=setup.shown))
    for place in (elsewhere, made.store):
        sent = form | {"gone": f"{setup.gone.id} {place.id}", "area_id": str(place.id)}
        assert admin_client.post(f"{made.path}/bring-back", data=sent).status_code == (
            404
        )
        session.refresh(place)
        assert place.retired_at is not None


def test_an_area_brought_back_since_the_refusal_is_still_said_gone(
    admin_client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shelf C brought back between the refusal and the form shown again
    (none found that may not be counted): still named, as the save was
    refused for it."""
    setup = Emptied(session, admin_client)
    made, gone = setup.made, setup.gone
    setup.send(session, gone, made.empty, "2")
    monkeypatch.setattr(queries, "not_countable", lambda *_: set())
    refused = admin_client.post(made.path, data=setup.shown)
    assert refused.status_code == 409
    assert emptied_words(gone.name) in text(refused)
    assert count_form(refused)["gone"] == str(gone.id)


def test_a_recount_shows_what_went_to_an_area_it_does_not_ask_at(
    admin_client: TestClient, session: Session
) -> None:
    """Asked again at Shelf A and Shelf C, Shelf C's last radio then went to
    Shelf B, still in the store: the recount refused asks at Shelf B too,
    the radio under "Moved to Shelf B", not taken out of the store."""
    setup = Emptied(session, admin_client)
    made, gone = setup.made, setup.gone
    setup.send(session, gone, made.shelf, "1")
    typed = {setup.counted(made.shelf): "3", setup.counted(gone): "2"}
    recount = count_form(admin_client.post(made.path, data=setup.shown | typed))
    setup.send(session, gone, made.empty, "1")
    refused = admin_client.post(made.path, data=recount | typed)
    assert refused.status_code == 409
    again = count_form(refused)
    assert sorted(again["places"].split()) == sorted(
        [str(made.shelf.id), str(made.empty.id)]
    )
    assert heading(refused, f"Moved to {made.empty.name}")
    assert read(refused.text).all("th", text=f"Taken out of {made.store.name}") == []
    # Saved as shown, the move left alone: Shelf B keeps the radio.
    saved = admin_client.post(made.path, data=again)
    assert saved.status_code == 303
    assert expected(session, made.empty.id) == {setup.item: Decimal(1)}
