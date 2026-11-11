"""Labels: made from an item's or a place's page, found, reprinted and
revoked from the labels page (admin/labels.py)."""

import re
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pytest
import segno
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import labels
from inventory_tng.accounts import qr_svg
from inventory_tng.admin.common import LABELS
from inventory_tng.admin.items import ITEMS
from inventory_tng.admin.labels import PRINT
from inventory_tng.admin.listing import PER_PAGE
from inventory_tng.admin.locations import LOCATIONS
from inventory_tng.catalogue import catalogue
from inventory_tng.models import (
    Category,
    Item,
    ItemPackage,
    Label,
    Location,
    Volunteer,
)

from helpers.rows import add, history
from helpers.sign_in import text
from helpers.stock import stocked


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
    page = text(admin_client.get(f"{ITEMS}/{cable.id}"))
    assert label.code in page
    assert f'<form method="post" action="{LABELS}"' in page


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
    assert "Cat6 cable: Reel of 305 m" in page
    assert "Shelf A, in Hub" in page
    page = text(admin_client.get(f"{LOCATIONS}/{shelf.id}"))
    assert on_shelf.code in page
    assert f'<form method="post" action="{LABELS}"' in page


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
    assert "Pick a current label" in text(
        admin_client.get(LABELS, params={"notice": "pick_labels"})
    )


def test_revoking_asks_to_confirm_first(
    admin_client: TestClient, session: Session, cable: Item
) -> None:
    label = add(session, Label(item_id=cable.id))
    confirm = f"{LABELS}/{label.id}/revoke"
    link = f'<a href="{confirm}">Revoke<span class="sr-only"> {label.code}</span></a>'
    assert link in admin_client.get(LABELS).text
    page = admin_client.get(confirm)
    assert page.status_code == 200
    words = " ".join(text(page).split())
    assert f'<span class="key">{label.code}</span>, names Cat6 cable.' in words
    assert "can't be undone" in words
    assert (
        f'<form method="post" action="{confirm}"><button class="danger">Revoke</button>'
        in page.text
    )
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
    rows = {
        "Revoked": add(session, Label(location_id=hub.id, revoked_at=when)),
        "Item retired": add(session, Label(item_id=cable.id)),
        "Place retired": add(session, Label(location_id=place.van.id)),
    }
    radio_box = add(
        session, ItemPackage(item_id=place.item.id, name="Box", size=Decimal(4))
    )
    rows["Package retired"] = add(
        session, Label(item_id=place.item.id, package_id=radio_box.id)
    )
    # Retired with stock left, the radios still scan: volunteers may take them.
    place.item.retired_at = cable.retired_at = place.van.retired_at = when
    radio_box.retired_at = when
    session.flush()
    scanned = {entry.code for entry in catalogue(session).labels}
    assert still_stocked.code in scanned
    assert scanned.isdisjoint(label.code for label in rows.values())
    # The list's filter (labels.scanned, in SQL) agrees with each row's why.
    current = text(admin_client.get(LABELS))
    assert still_stocked.code in current
    assert not any(label.code in current for label in rows.values())
    page = " ".join(text(admin_client.get(LABELS, params={"show": "inactive"})).split())
    assert still_stocked.code not in page
    for why, label in rows.items():
        row = page[page.index(label.code) :].split("</tr>")[0]
        assert f'<span class="tag">{why}</span>' in row, why
        # Neither printed nor revoked again from here.
        assert 'type="checkbox"' not in row
        assert "/revoke" not in row
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
    return re.findall(r'<td class="key">([A-Z0-9]+)</td>', page)


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
        page = text(admin_client.get(LABELS, params={"show": show}))
        assert {kept.code, gone.code} & set(codes_listed(page)) == expected, show
    page = text(admin_client.get(LABELS, params={"show": "all"}))
    assert f'href="{LABELS}?show=inactive">Inactive</a>' in page
    assert "Retired</a>" not in page
    assert "The scan page doesn't read a label tagged here." in page
    assert "doesn't read a label tagged" not in text(admin_client.get(LABELS))


def test_label_suggestions_name_it_and_narrow_the_list(
    admin_client: TestClient, session: Session, cable: Item, reel: ItemPackage
) -> None:
    loose = add(session, Label(item_id=cable.id))
    on_reel = add(session, Label(item_id=cable.id, package_id=reel.id))
    offered = admin_client.get(f"{LABELS}/suggest", params={"q": "reel"}).json()
    assert offered == [
        {
            "name": "Cat6 cable: Reel of 305 m",
            "hint": on_reel.code,
            "href": f"{LABELS}?pick={on_reel.id}",
        }
    ]
    page = text(admin_client.get(LABELS, params={"pick": str(on_reel.id)}))
    assert codes_listed(page) == [on_reel.code]
    assert loose.code not in page
    assert "matching “Cat6 cable: Reel of 305 m”" in " ".join(page.split())
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
    first = text(admin_client.get(LABELS, params={"q": "Paged"}))
    assert len(codes_listed(first)) == PER_PAGE
    assert first.count('form="print-labels"') == PER_PAGE + 2  # And copies, Print.
    assert "Page 1 of 2" in first
    second = text(admin_client.get(LABELS, params={"q": "Paged", "page": "2"}))
    assert len(codes_listed(second)) == 5


def test_printing_refused_goes_back_to_the_list_as_it_was(
    admin_client: TestClient, session: Session, cable: Item
) -> None:
    add(session, Label(item_id=cable.id))
    page = text(admin_client.get(LABELS, params={"q": "cat6", "show": "all"}))
    assert '<input type="hidden" name="q" value="cat6">' in page
    assert '<input type="hidden" name="show" value="all">' in page
    sent = admin_client.get(PRINT, params={"q": "cat6", "show": "all", "page": "2"})
    assert (
        sent.headers["location"]
        == f"{LABELS}?q=cat6&show=all&page=2&notice=pick_labels"
    )
