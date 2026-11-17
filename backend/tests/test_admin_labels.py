"""Labels: made from an item's or a place's page, found, reprinted and
revoked from the labels page (admin/labels.py)."""

from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest
import segno
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.accounts import qr_svg
from inventory_tng.admin.common import LABELS
from inventory_tng.admin.items import ITEMS
from inventory_tng.admin.labels import PICK_LABELS, PRINT
from inventory_tng.admin.listing import PER_PAGE
from inventory_tng.admin.locations import LOCATIONS
from inventory_tng.labels import labels
from inventory_tng.labels.labels import (
    INACTIVE_WORDS,
    Inactive,
    package_label,
    place_words,
)
from inventory_tng.models import (
    Category,
    Item,
    ItemPackage,
    Label,
    Location,
    Volunteer,
)
from inventory_tng.scan.catalogue_api import catalogue

from helpers.markup import (
    button,
    forms,
    hidden_fields,
    links,
    options,
    read,
    sorted_columns,
    text,
)
from helpers.rows import add, history, tag
from helpers.stock import stocked, write


@pytest.fixture
def cable(session: Session) -> Item:
    cables = add(session, Category(name="Cables"))
    return add(session, Item(name="Cat6 cable", category_id=cables.id, unit="m"))


@pytest.fixture
def reel(session: Session, cable: Item) -> ItemPackage:
    return add(session, ItemPackage(item_id=cable.id, name="Reel", size=Decimal(305)))


@pytest.fixture
def shelf(session: Session) -> Location:
    hub = add(session, Location(name="Hub", kind="store"))
    return add(session, Location(name="Shelf A", kind="area", parent_id=hub.id))


def made(
    client: TestClient, copies: str = "12", **names: object
) -> dict[str, list[str]]:
    """Makes labels from an item's or a place's page: the query of the print
    page it is sent on to."""
    form = {key: str(value) for key, value in names.items()} | {"copies": copies}
    done = client.post(LABELS, data=form)
    assert done.status_code == 303
    query = parse_qs(urlsplit(done.headers["location"]).query)
    assert urlsplit(done.headers["location"]).path == PRINT
    return query


def labels_of(session: Session, **where: object) -> list[Label]:
    query = select(Label).filter_by(**where).order_by(Label.id)
    return list(session.scalars(query))


def test_labels_for_an_item_are_copies_of_one_code(
    admin_client: TestClient, session: Session, cable: Item
) -> None:
    query = made(admin_client, item_id=cable.id, package_id="")  # As the page sends.
    (label,) = labels_of(session, item_id=cable.id)
    assert query == {"label": [str(label.id)], "copies": ["12"]}
    assert (history(session, "label", label.id)[0].actor or "").startswith("admin:")
    # Again: the same code, printed again.
    made(admin_client, copies="3", item_id=cable.id)
    assert labels_of(session, item_id=cable.id) == [label]
    page = admin_client.get(f"{ITEMS}/{cable.id}").text
    assert label.code in page
    assert ("post", LABELS) in forms(page)


def test_a_package_and_a_place_have_codes_of_their_own(
    admin_client: TestClient,
    session: Session,
    cable: Item,
    reel: ItemPackage,
    shelf: Location,
) -> None:
    made(admin_client, item_id=cable.id)
    made(admin_client, item_id=cable.id, package_id=reel.id)
    made(admin_client, location_id=shelf.id)
    loose, on_reel = labels_of(session, item_id=cable.id)
    assert (loose.package_id, on_reel.package_id) == (None, reel.id)
    (on_shelf,) = labels_of(session, location_id=shelf.id)
    page = text(admin_client.get(LABELS))
    assert package_label("Cat6 cable", "Reel", "305 m") in page
    assert place_words("Shelf A", "Hub") in page
    page = admin_client.get(f"{LOCATIONS}/{shelf.id}").text
    assert on_shelf.code in page
    assert ("post", LABELS) in forms(page)


def test_no_label_for_another_items_package(
    admin_client: TestClient, session: Session, cable: Item, reel: ItemPackage
) -> None:
    """A current package of another item: none of this one's."""
    other = add(session, Item(name="Radio", category_id=cable.category_id))
    sent = {"item_id": str(other.id), "package_id": str(reel.id), "copies": "1"}
    assert admin_client.post(LABELS, data=sent).status_code == 404
    assert labels_of(session) == []


def test_no_labels_for_what_is_retired_or_a_volunteers_custody(
    admin_client: TestClient,
    session: Session,
    cable: Item,
    reel: ItemPackage,
    shelf: Location,
) -> None:
    other = add(session, Item(name="Radio", category_id=cable.category_id))
    volunteer = add(session, Volunteer(name="Ada"))
    custody = add(
        session, Location(name="Ada", kind="custody", volunteer_id=volunteer.id)
    )
    reel.retired_at = shelf.retired_at = reel.created_at
    session.flush()
    for form in (
        {"item_id": cable.id, "package_id": reel.id},
        {"item_id": other.id, "package_id": reel.id},
        {"location_id": shelf.id},
        {"location_id": custody.id},
        {"location_id": shelf.id, "item_id": cable.id},
        {"item_id": 0},
        {},
    ):
        sent = {key: str(value) for key, value in form.items()} | {"copies": "1"}
        assert admin_client.post(LABELS, data=sent).status_code == 404, form
    assert labels_of(session) == []
    assert "Print labels" not in text(admin_client.get(f"{LOCATIONS}/{shelf.id}"))
    cable.retired_at = cable.created_at
    session.flush()
    retired = {"item_id": str(cable.id), "copies": "1"}
    assert admin_client.post(LABELS, data=retired).status_code == 404
    assert "Print labels" not in text(admin_client.get(f"{ITEMS}/{cable.id}"))


def test_the_sheet_prints_each_copy_twelve_to_a_page(
    admin_client: TestClient, session: Session, cable: Item, shelf: Location
) -> None:
    item_label = add(session, Label(item_id=cable.id))
    place_label = add(session, Label(location_id=shelf.id))
    query = {"label": [item_label.id, place_label.id, item_label.id], "copies": 7}
    page = admin_client.get(PRINT, params=query)
    assert page.status_code == 200
    assert page.text.count('class="print-sheet label-sheet"') == 2  # 14 labels
    assert page.text.count('class="print-label"') == 14
    assert "14 labels, on 2 pages" in " ".join(text(page).split())
    # A full QR code of the bare code: segno would make a Micro QR of it.
    assert segno.make(item_label.code, error="q").is_micro
    assert str(qr_svg(item_label.code, "q")) in page.text
    assert 'width="125px"' in page.text  # 21 modules and the border, at 5px.


def test_copies_and_picks_must_make_sense(
    admin_client: TestClient, session: Session, cable: Item
) -> None:
    label = add(session, Label(item_id=cable.id))
    gone = add(session, Label(item_id=cable.id, revoked_at=label.created_at))
    for copies in ("0", "121", "lots"):
        sent = admin_client.get(PRINT, params={"label": label.id, "copies": copies})
        assert sent.headers["location"] == f"{LABELS}?notice=copies"
        made_form = {"item_id": str(cable.id), "copies": copies}
        refused = admin_client.post(LABELS, data=made_form)
        assert refused.headers["location"] == f"{LABELS}?notice=copies"
    for picks in ({}, {"label": gone.id}, {"label": 0}):
        sent = admin_client.get(PRINT, params=picks)
        assert sent.headers["location"] == f"{LABELS}?notice=pick_labels"
    assert str(PICK_LABELS) in text(
        admin_client.get(LABELS, params={"notice": "pick_labels"})
    )


def test_revoking_asks_to_confirm_first(
    admin_client: TestClient, session: Session, cable: Item
) -> None:
    label = add(session, Label(item_id=cable.id))
    confirm = f"{LABELS}/{label.id}/revoke"
    listed = read(admin_client.get(LABELS).text)
    link = listed.one("a", f"Revoke {label.code}", href=confirm)
    assert link.one("span", class_="sr-only").text == label.code
    page = admin_client.get(confirm)
    assert page.status_code == 200
    words = " ".join(text(page).split())
    asked = read(page.text)
    assert asked.one("span", label.code, class_="key")
    assert f"{label.code}, names Cat6 cable." in asked.text
    assert "can't be undone" in words
    assert ("post", confirm) in forms(asked)
    assert "danger" in button(asked, "Revoke").classes
    assert ("Keep it", LABELS) in links(asked)
    session.refresh(label)
    assert label.revoked_at is None  # Only its button revokes.
    admin_client.post(confirm)
    assert admin_client.get(confirm).status_code == 404
    assert admin_client.get(f"{LABELS}/0/revoke").status_code == 404


def test_revoking_voids_the_code_and_the_next_labels_get_a_new_one(
    admin_client: TestClient, session: Session, cable: Item
) -> None:
    label = add(session, Label(item_id=cable.id))
    revoked = admin_client.post(f"{LABELS}/{label.id}/revoke")
    assert revoked.headers["location"] == f"{LABELS}?notice=label_revoked"
    session.refresh(label)
    assert label.revoked_at is not None
    assert admin_client.post(f"{LABELS}/{label.id}/revoke").status_code == 404
    made(admin_client, item_id=cable.id)
    old, new = labels_of(session, item_id=cable.id)
    assert old == label
    assert new.code != label.code
    assert new.revoked_at is None
    current = text(admin_client.get(LABELS))
    assert new.code in current
    assert label.code not in current
    inactive = text(admin_client.get(LABELS, params={"show": "inactive"}))
    assert label.code in inactive
    assert new.code not in inactive


def test_finding_labels_by_code_or_by_name(
    admin_client: TestClient, session: Session, cable: Item, shelf: Location
) -> None:
    item_label = add(session, Label(item_id=cable.id))
    place_label = add(session, Label(location_id=shelf.id))

    def found(search: str) -> set[str]:
        page = text(admin_client.get(LABELS, params={"q": search}))
        return {x.code for x in (item_label, place_label) if x.code in page}

    assert found(item_label.code.lower()) == {item_label.code}
    # Only a whole code: part of one is no match, whatever it shares.
    assert found(item_label.code[:4]) == set()
    assert found("cat6") == {item_label.code}
    assert found("shelf") == {place_label.code}
    assert found("100%") == set()
    assert "Nothing matches." in text(admin_client.get(LABELS, params={"q": "x"}))


def test_a_sheet_has_a_limit(
    admin_client: TestClient,
    session: Session,
    cable: Item,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    label = add(session, Label(item_id=cable.id))
    monkeypatch.setattr(labels, "MAX_LABELS", 4)
    four = admin_client.get(PRINT, params={"label": label.id, "copies": 4})
    assert four.status_code == 200
    five = admin_client.get(PRINT, params={"label": label.id, "copies": 5})
    assert five.headers["location"] == f"{LABELS}?notice=too_many"


def test_inactive_labels_say_why_and_agree_with_the_scan_page(
    admin_client: TestClient, session: Session, cable: Item
) -> None:
    place = stocked(session)  # Radios in its store.
    hub = add(session, Location(name="Hub", kind="store"))
    when = cable.created_at
    still_stocked = add(session, Label(item_id=place.item.id))
    rows: dict[Inactive, Label] = {
        "revoked": add(session, Label(location_id=hub.id, revoked_at=when)),
        "item retired": add(session, Label(item_id=cable.id)),
        "place retired": add(session, Label(location_id=place.van.id)),
    }
    radio_box = add(
        session, ItemPackage(item_id=place.item.id, name="Box", size=Decimal(4))
    )
    rows["package retired"] = add(
        session, Label(item_id=place.item.id, package_id=radio_box.id)
    )
    # A retired package with some still sealed scans too (inventory-tng-96a).
    crate = add(
        session, ItemPackage(item_id=place.item.id, name="Crate", size=Decimal(8))
    )
    write(
        session,
        place,
        kind="receipt",
        from_location_id=None,
        to_location_id=place.store.id,
        lines=[
            {
                "item_id": place.item.id,
                "quantity": "8",
                "package_id": crate.id,
                "package_size": "8",
            }
        ],
    )
    sealed = add(session, Label(item_id=place.item.id, package_id=crate.id))
    # Retired with stock left, the radios still scan: volunteers may take them.
    place.item.retired_at = cable.retired_at = place.van.retired_at = when
    radio_box.retired_at = crate.retired_at = when
    session.flush()
    scanned = {entry.code for entry in catalogue(session).labels}
    assert {still_stocked.code, sealed.code} <= scanned
    assert scanned.isdisjoint(label.code for label in rows.values())
    # The list's filter (labels.scanned, in SQL) agrees with each row's why.
    current = text(admin_client.get(LABELS))
    assert still_stocked.code in current
    assert sealed.code in current
    assert not any(label.code in current for label in rows.values())
    inactive = admin_client.get(LABELS, params={"show": "inactive"})
    page = " ".join(text(inactive).split())
    assert still_stocked.code not in page
    assert sealed.code not in page
    lines = read(inactive.text).all("tr")
    for why, label in rows.items():
        (row,) = [line for line in lines if label.code in line.text]
        assert row.all("span", str(INACTIVE_WORDS[why]), class_="tag"), why
        # Neither printed nor revoked again from here.
        assert not row.all("input", type="checkbox")
        assert not [to for _, to in links(row) + forms(row) if "/revoke" in to]
    assert "4 inactive labels" in page


def test_finding_labels_by_package_or_the_place_around(
    admin_client: TestClient,
    session: Session,
    cable: Item,
    reel: ItemPackage,
    shelf: Location,
) -> None:
    on_reel = add(session, Label(item_id=cable.id, package_id=reel.id))
    on_shelf = add(session, Label(location_id=shelf.id))

    def found(search: str) -> set[str]:
        page = text(admin_client.get(LABELS, params={"q": search}))
        return {x.code for x in (on_reel, on_shelf) if x.code in page}

    assert found("reel") == {on_reel.code}
    assert found("hub") == {on_shelf.code}


def codes_listed(page: str) -> list[str]:
    """The codes the labels list shows, in its order."""
    return [cell.text for cell in read(page).all("td", class_="key")]


def test_labels_list_current_inactive_and_all(
    admin_client: TestClient, session: Session, cable: Item, shelf: Location
) -> None:
    kept = add(session, Label(location_id=shelf.id))
    gone = add(session, Label(item_id=cable.id, revoked_at=kept.created_at))
    for show, expected in (
        ("", {kept.code}),
        ("inactive", {gone.code}),
        ("all", {kept.code, gone.code}),
        ("retired", {kept.code}),  # Not a filter here: the plain list.
    ):
        page = admin_client.get(LABELS, params={"show": show}).text
        assert {kept.code, gone.code} & set(codes_listed(page)) == expected, show
    every = admin_client.get(LABELS, params={"show": "all"})
    every_link = links(every.text)
    assert ("Inactive", f"{LABELS}?show=inactive") in every_link
    assert not [words for words, _ in every_link if words.endswith("Retired")]
    assert "The scan page doesn't read a label tagged here." in text(every)
    assert "doesn't read a label tagged" not in text(admin_client.get(LABELS))


def test_label_suggestions_name_it_and_narrow_the_list(
    admin_client: TestClient, session: Session, cable: Item, reel: ItemPackage
) -> None:
    loose = add(session, Label(item_id=cable.id))
    on_reel = add(session, Label(item_id=cable.id, package_id=reel.id))
    offered = admin_client.get(f"{LABELS}/suggest", params={"q": "reel"}).json()
    assert offered == [
        {
            "name": package_label("Cat6 cable", "Reel", "305 m"),
            "hint": on_reel.code,
            "href": f"{LABELS}?pick={on_reel.id}",
        }
    ]
    page = admin_client.get(LABELS, params={"pick": str(on_reel.id)}).text
    assert codes_listed(page) == [on_reel.code]
    assert loose.code not in page
    reel_words = package_label("Cat6 cable", "Reel", "305 m")
    assert f"matching “{reel_words}”" in " ".join(page.split())
    revoked = admin_client.get(
        f"{LABELS}/suggest", params={"q": "reel", "show": "inactive"}
    )
    assert revoked.json() == []


def test_labels_come_in_pages_of_fifty_each_printed_from_its_own(
    admin_client: TestClient, session: Session, cable: Item
) -> None:
    hub = add(session, Location(name="Paged hub", kind="store"))
    for number in range(PER_PAGE + 5):
        area = add(
            session,
            Location(name=f"Paged {number:03}", kind="area", parent_id=hub.id),
        )
        add(session, Label(location_id=area.id))
    shown = admin_client.get(LABELS, params={"q": "Paged"})
    first = text(shown)
    assert len(codes_listed(shown.text)) == PER_PAGE
    assert first.count('form="print-labels"') == PER_PAGE + 2  # And copies, Print.
    assert "Page 1 of 2" in first
    second = admin_client.get(LABELS, params={"q": "Paged", "page": "2"}).text
    assert len(codes_listed(second)) == 5


def test_printing_refused_goes_back_to_the_list_as_it_was(
    admin_client: TestClient, session: Session, cable: Item
) -> None:
    add(session, Label(item_id=cable.id))
    page = read(admin_client.get(LABELS, params={"q": "cat6", "show": "all"}).text)
    assert ("q", "cat6") in hidden_fields(page)
    assert ("show", "all") in hidden_fields(page)
    sent = admin_client.get(PRINT, params={"q": "cat6", "show": "all", "page": "2"})
    assert (
        sent.headers["location"]
        == f"{LABELS}?q=cat6&show=all&page=2&notice=pick_labels"
    )


def test_labels_sort_by_each_heading_either_way(
    admin_client: TestClient, session: Session
) -> None:
    """By what they name unless a heading sorts them; pressed again, the
    other way (Ali, 2026-11-15, inventory-tng-qwb)."""
    mark = tag()
    stuff = add(session, Category(name=f"Sorted {mark}"))
    bravo, alpha = (
        add(
            session,
            Label(
                item_id=add(session, Item(name=name, category_id=stuff.id)).id,
                created_at=datetime(2026, 1, day, tzinfo=UTC),
            ),
        )
        for day, name in ((1, f"Sorted {mark} Bravo"), (2, f"Sorted {mark} Alpha"))
    )

    def order(**query: str) -> list[str]:
        params = {"q": f"Sorted {mark}", "per": "all"} | query
        return codes_listed(admin_client.get(LABELS, params=params).text)

    assert order(sort="names") == [alpha.code, bravo.code]
    assert order(sort="names", order="desc") == [bravo.code, alpha.code]
    assert order(sort="made") == [alpha.code, bravo.code]
    assert order(sort="made", order="asc") == [bravo.code, alpha.code]
    assert order(sort="code") == sorted([alpha.code, bravo.code])
    assert order(sort="code", order="desc") == sorted(
        [alpha.code, bravo.code], reverse=True
    )
    page = read(admin_client.get(LABELS).text)
    assert sorted_columns(page) == [
        (
            "ascending",
            "Names",
            "/admin/labels?sort=names&order=desc",
        )
    ]
    assert ("Made", "/admin/labels?sort=made") in links(page)


def test_labels_show_25_50_100_or_all(
    admin_client: TestClient, session: Session, shelf: Location
) -> None:
    mark = tag()
    for number in range(30):
        area = add(
            session,
            Location(name=f"Sized {mark} {number:03}", kind="area", parent_id=shelf.id),
        )
        add(session, Label(location_id=area.id))
    query = {"q": f"Sized {mark}"}
    assert len(codes_listed(admin_client.get(LABELS, params=query).text)) == 30
    shown = admin_client.get(LABELS, params=query | {"per": "25"})
    sized = text(shown)
    assert len(codes_listed(shown.text)) == 25
    assert "Page 1 of 2" in sized
    every = admin_client.get(LABELS, params=query | {"per": "all"}).text
    assert len(codes_listed(every)) == 30
    assert read(every).one("a", "All", aria_current="page")


def test_labels_filter_by_kind_and_item(
    admin_client: TestClient, session: Session, cable: Item, shelf: Location
) -> None:
    """Items' labels or places', and one item's (Ali, 2026-11-15)."""
    on_cable = add(session, Label(item_id=cable.id))
    on_shelf = add(session, Label(location_id=shelf.id))
    other = add(session, Item(name="Patch cord", category_id=cable.category_id))
    on_other = add(session, Label(item_id=other.id))

    def listed(**query: str) -> set[str]:
        page = admin_client.get(LABELS, params={"per": "all"} | query).text
        return set(codes_listed(page))

    mine = {on_cable.code, on_shelf.code, on_other.code}
    assert listed(kind="item") & mine == {on_cable.code, on_other.code}
    assert listed(kind="place") & mine == {on_shelf.code}
    assert listed(item=str(cable.id)) == {on_cable.code}
    assert listed(kind="place", item=str(cable.id)) == set()
    # A kind not offered, or an item not a number, is no filter.
    assert listed(kind="box", item="x") >= mine
    page = read(admin_client.get(LABELS, params={"item": str(cable.id)}).text)
    assert (str(cable.id), "Cat6 cable", True) in options(page)
    assert ("Clear", LABELS) in links(page)
    # Printing keeps the filter, to come back to.
    assert "item" in dict(hidden_fields(page))
    sent = admin_client.get(PRINT, params={"item": str(cable.id), "per": "25"})
    assert sent.headers["location"] == (
        f"{LABELS}?item={cable.id}&per=25&notice=pick_labels"
    )
    # The search box's suggestions come from the filter too.
    offered = admin_client.get(
        f"{LABELS}/suggest", params={"q": "cable", "kind": "place"}
    ).json()
    assert on_cable.code not in [each["hint"] for each in offered]
