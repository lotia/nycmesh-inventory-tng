"""Labels: made from an item's or a place's page, found, reprinted and
revoked from the labels page (labels/labels_routes.py)."""

from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
import segno
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.catalogue.paths import ITEMS
from inventory_tng.core.phrases import PATH_BETWEEN
from inventory_tng.labels import labels
from inventory_tng.labels.labels import (
    INACTIVE_WORDS,
    Inactive,
    package_label,
)
from inventory_tng.labels.labels_routes import PICK_LABELS
from inventory_tng.labels.paths import LABELS, PRINT, item_labels
from inventory_tng.models.catalogue import Category, Item, ItemPackage
from inventory_tng.models.labels import Label
from inventory_tng.models.places import Location
from inventory_tng.models.volunteers import Volunteer
from inventory_tng.places.paths import LOCATIONS
from inventory_tng.scan.catalogue_api import catalogue
from inventory_tng.web.listing import PER_PAGE
from inventory_tng.web.qr_codes import qr_svg

from helpers.items import shown_item_form
from helpers.markup import (
    button,
    checked,
    forms,
    hidden_fields,
    links,
    options,
    read,
    sorted_columns,
    text,
    words_on,
)
from helpers.rows import add, history, tag
from helpers.stock import retire, stocked, write


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
    # The place it is part of, then it, as a count form's path (Ali,
    # 2026-11-22): no preposition chosen by the kind of place.
    assert PATH_BETWEEN.join(("Hub", "Shelf A")) in page
    page = admin_client.get(f"{LOCATIONS}/{shelf.id}").text
    assert on_shelf.code in page
    assert ("post", LABELS) in forms(page)


def test_a_labels_size_has_a_point_and_no_grouping_in_any_locale(
    admin_client: TestClient, session: Session, cable: Item
) -> None:
    """One fixed style for printed amounts, whatever the browser's locale
    (Ali, 2026-11-23, inventory-tng-qnwz)."""
    drum = add(
        session, ItemPackage(item_id=cable.id, name="Drum", size=Decimal("1500.5"))
    )
    made(admin_client, item_id=cable.id, package_id=drum.id)
    for language in ("en", "de"):
        page = text(admin_client.get(LABELS, headers={"Accept-Language": language}))
        assert package_label("Cat6 cable", "Drum", "1500.5 m") in page, language


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
    shown = read(page.text)
    assert len(shown.all("*", class_="print-sheet label-sheet")) == 2  # 14 labels
    assert len(shown.all("*", class_="print-label")) == 14
    assert "14 labels, on 2 pages" in words_on(shown)
    # A full QR code of the bare code: segno would make a Micro QR of it.
    assert segno.make(item_label.code, error="q").is_micro
    # Exact markup on purpose: the page holds this very code, as drawn.
    assert str(qr_svg(item_label.code, "q")) in page.text
    # 21 modules and the border, at 5px.
    assert shown.all("svg", width="125px")


def test_a_printed_place_label_names_the_place_it_is_part_of_apart(
    admin_client: TestClient, session: Session, cable: Item, shelf: Location
) -> None:
    """The place it is part of is printed apart from its own name, and only
    it yields lines (styles.css), so a long one never hides the place's."""
    place_label = add(session, Label(location_id=shelf.id))
    item_label = add(session, Label(item_id=cable.id))
    query = {"label": [place_label.id, item_label.id], "copies": 1}
    shown = read(admin_client.get(PRINT, params=query).text)
    on_shelf, on_cable = shown.all("*", class_="print-words")
    assert on_shelf.text == PATH_BETWEEN.join(("Hub", "Shelf A"))
    assert on_shelf.one("p", class_="print-within")
    assert on_shelf.all("p")[-1].text == "Shelf A"
    assert not on_cable.all("p", class_="print-within")
    assert [p.text for p in on_cable.all("p")] == ["Cat6 cable"]


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
    words = words_on(page.text)
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
                "unit": place.item.unit,
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
    page = words_on(inactive.text)
    assert still_stocked.code not in page
    assert sealed.code not in page
    lines = read(inactive.text).all("tr")
    for why, label in rows.items():
        (row,) = [line for line in lines if label.code in line.text]
        assert row.all("span", str(INACTIVE_WORDS[why]), class_="tag"), why
        # Not printed from here; revoked, unless it is already (Ali,
        # 2026-11-23, inventory-tng-x30g).
        assert not row.all("input", type="checkbox")
        revoking = [to for _, to in links(row) + forms(row) if "/revoke" in to]
        confirm = f"{LABELS}/{label.id}/revoke?show=inactive"
        offered = [] if why == "revoked" else [confirm]
        assert revoking == offered, why
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
    assert f"matching “{reel_words}”" in words_on(page)
    revoked = admin_client.get(
        f"{LABELS}/suggest", params={"q": "reel", "show": "inactive"}
    )
    assert revoked.json() == []


def test_labels_come_in_pages_of_fifty_each_printed_from_its_own(
    admin_client: TestClient, session: Session
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
    in_form = read(shown.text).all("*", form="print-labels")
    assert len(in_form) == PER_PAGE + 2  # And copies, Print.
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


def test_an_items_labels_its_packages_too_are_each_offered_to_revoke(
    admin_client: TestClient, session: Session, cable: Item
) -> None:
    """Where an item page sends one to revoke the labels fixing its unit or
    a package's size (Ali, 2026-11-23, inventory-tng-37dj)."""
    reel = add(session, ItemPackage(item_id=cable.id, name="Reel", size=Decimal(305)))
    own = add(session, Label(item_id=cable.id))
    reels = add(session, Label(item_id=cable.id, package_id=reel.id))
    page = read(admin_client.get(item_labels(cable.id)).text)
    for label in (own, reels):
        (link,) = page.all("a", f"Revoke {label.code}")
        assert address(link.attrs["href"])[0] == f"{LABELS}/{label.id}/revoke"


def test_a_retired_packages_label_is_revoked_from_its_items_labels(
    admin_client: TestClient, session: Session, cable: Item, reel: ItemPackage
) -> None:
    """A retired package's label, inactive, still fixes its item's unit;
    the item page's link leads to it, offered to revoke, and revoked, the
    unit may change (Ali, 2026-11-23, inventory-tng-x30g)."""
    label = add(session, Label(item_id=cable.id, package_id=reel.id))
    retire(session, reel)
    item_page = read(admin_client.get(f"{ITEMS}/{cable.id}").text)
    (to_labels,) = item_page.all("a", "Revoke its labels")
    assert to_labels.attrs["href"] == item_labels(cable.id)
    listed = read(admin_client.get(item_labels(cable.id)).text)
    (link,) = listed.all("a", f"Revoke {label.code}")
    revoke = link.attrs["href"]
    asked = words_on(admin_client.get(revoke).text)
    assert "Its printed copies don't scan now" in asked
    done = admin_client.post(revoke).headers["location"]
    path, query = address(item_labels(cable.id))
    assert address(done) == (path, query | {"notice": ["label_revoked"]})
    assert label.revoked_at is not None
    form = shown_item_form(cable, unit="ft")
    assert admin_client.post(f"{ITEMS}/{cable.id}", data=form).status_code == 303
    assert cable.unit == "ft"
    # Revoked, it is offered to revoke no more, nor listed there.
    assert admin_client.get(revoke).status_code == 404
    assert codes_listed(admin_client.get(item_labels(cable.id)).text) == []


def address(link: str) -> tuple[str, dict[str, list[str]]]:
    """A link's path and query, its names in any order."""
    split = urlsplit(link)
    return split.path, parse_qs(split.query)


def test_revoking_an_items_labels_goes_back_to_its_labels_each_time(
    admin_client: TestClient, session: Session, cable: Item, reel: ItemPackage
) -> None:
    """Two labels fix the item's unit, one a retired package's: the item's
    list shows both and no revoked one, and revoking or keeping each goes
    back to that list, not the whole one (Ali, 2026-11-24,
    inventory-tng-9t9y)."""
    old = add(session, Label(item_id=cable.id, revoked_at=datetime.now(UTC)))
    own = add(session, Label(item_id=cable.id))
    reels = add(session, Label(item_id=cable.id, package_id=reel.id))
    retire(session, reel)
    its_labels = address(item_labels(cable.id))
    query = its_labels[1]
    assert query == {"item": [str(cable.id)], "show": ["all"], "revoked": ["no"]}
    listed = admin_client.get(item_labels(cable.id)).text
    assert sorted(codes_listed(listed)) == sorted([own.code, reels.code])
    assert old.code not in listed
    # The filter is said, and kept by printing.
    assert checked(listed, "revoked") == ["no"]
    assert ("revoked", "no") in hidden_fields(listed)
    form = shown_item_form(cable, unit="ft")
    for label, left in ((own, [reels.code]), (reels, [])):
        (link,) = read(listed).all("a", f"Revoke {label.code}")
        confirm = link.attrs["href"]
        assert address(confirm) == (f"{LABELS}/{label.id}/revoke", query)
        asked = read(admin_client.get(confirm).text)
        (keep,) = [to for words, to in links(asked) if words == "Keep it"]
        assert address(keep) == its_labels
        assert ("post", confirm) in forms(asked)
        done = admin_client.post(confirm)
        assert address(done.headers["location"]) == (
            LABELS,
            query | {"notice": ["label_revoked"]},
        )
        listed = admin_client.get(done.headers["location"]).text
        assert codes_listed(listed) == left
        changed = admin_client.post(f"{ITEMS}/{cable.id}", data=form)
        assert changed.status_code == (303 if not left else 400)
    assert cable.unit == "ft"


def test_an_items_revoked_labels_never_push_its_others_off_the_first_page(
    admin_client: TestClient, session: Session, cable: Item
) -> None:
    gone = datetime.now(UTC)
    for _ in range(PER_PAGE + 1):
        add(session, Label(item_id=cable.id, revoked_at=gone))
    own = add(session, Label(item_id=cable.id))
    every = {"item": str(cable.id), "show": "all"}
    assert own.code not in codes_listed(admin_client.get(LABELS, params=every).text)
    page = admin_client.get(item_labels(cable.id)).text
    assert codes_listed(page) == [own.code]
    assert "Page 1 of" not in words_on(page)


def test_revoking_a_picked_label_goes_back_to_the_search_not_the_pick(
    admin_client: TestClient, session: Session, cable: Item
) -> None:
    """Revoked, a label picked from the suggestions is shown no more, so
    the list goes back to what it was picked from; kept, to the pick."""
    label = add(session, Label(item_id=cable.id))
    view = {"q": "cat6", "pick": str(label.id), "kind": "item"}
    page = admin_client.get(LABELS, params=view).text
    (link,) = read(page).all("a", f"Revoke {label.code}")
    confirm = link.attrs["href"]
    asked = read(admin_client.get(confirm).text)
    (keep,) = [to for words, to in links(asked) if words == "Keep it"]
    picked = {name: [value] for name, value in view.items()}
    assert address(keep) == (LABELS, picked)
    done = admin_client.post(confirm).headers["location"]
    assert address(done) == (
        LABELS,
        {"q": ["cat6"], "kind": ["item"], "notice": ["label_revoked"]},
    )


def test_revoking_keeps_the_lists_page_and_drops_what_it_does_not_know(
    admin_client: TestClient, session: Session, shelf: Location
) -> None:
    """The view comes back as the list reads it: its search, size and page,
    and nothing else sent, so no address of another site's."""
    mark = tag()
    for number in range(27):
        area = add(
            session,
            Location(name=f"Paged {mark} {number:03}", kind="area", parent_id=shelf.id),
        )
        add(session, Label(location_id=area.id))
    view = {"q": f"Paged {mark}", "per": "25", "page": "2"}
    page = admin_client.get(LABELS, params=view).text
    first, *_ = codes_listed(page)
    (label,) = labels_of(session, code=first)
    (link,) = read(page).all("a", f"Revoke {label.code}")
    confirm = link.attrs["href"]
    expected = {name: [value] for name, value in view.items()}
    assert address(confirm) == (f"{LABELS}/{label.id}/revoke", expected)
    odd = {"next": "https://example.com/", "kind": "box", "show": "x"}
    oddly = f"{confirm}&{urlencode(odd)}"
    asked = admin_client.get(oddly).text
    (keep,) = [to for words, to in links(asked) if words == "Keep it"]
    assert address(keep) == (LABELS, expected)
    done = admin_client.post(oddly)
    assert address(done.headers["location"]) == (
        LABELS,
        expected | {"notice": ["label_revoked"]},
    )
    after = admin_client.get(done.headers["location"]).text
    assert label.code not in codes_listed(after)
    assert "Page 2 of 2" in words_on(after)


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
