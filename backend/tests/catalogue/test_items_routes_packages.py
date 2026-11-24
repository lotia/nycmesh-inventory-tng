"""An item's packages (catalogue/items_routes.py): adding, renaming and
resizing them until they move, retiring and bringing them back, and
whether opened ones are followed (docs/decisions/0020, Amendment)."""

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.catalogue import items, queries
from inventory_tng.catalogue.forms import PACKAGE_TAKEN, SHORTER_THAN_WHOLE
from inventory_tng.catalogue.items_routes import PACKAGE_RETIRED
from inventory_tng.catalogue.paths import ITEMS
from inventory_tng.core.db_errors import RuleBroken, attempt
from inventory_tng.labels import labels
from inventory_tng.labels.paths import LABELS
from inventory_tng.models.catalogue import Category, Item, ItemPackage
from inventory_tng.models.labels import Label
from inventory_tng.stock import open_packages

from helpers.items import NOW, new_item, same_size_twins, shown_item_form
from helpers.markup import box_values, checked, forms, read, text, words_on
from helpers.rows import add, history, shown
from helpers.stock import Spools, retire


def test_packages(admin_client: TestClient, session: Session, radios: Category) -> None:
    item = new_item(session, radios)
    path = f"{ITEMS}/{item.id}/packages"
    done = admin_client.post(path, data={"package_name": "Box", "size": "4"})
    assert done.status_code == 303
    (box,) = session.scalars(select(ItemPackage)).all()
    assert (box.name, box.size) == ("Box", Decimal(4))
    assert "4 each" in text(admin_client.get(f"{ITEMS}/{item.id}"))
    taken = admin_client.post(path, data={"package_name": "box", "size": "2"})
    assert str(PACKAGE_TAKEN) in text(taken)
    for size in ("0", "-1", "lots", "1.2345", ""):
        refused = admin_client.post(path, data={"package_name": "Bag", "size": size})
        assert refused.status_code == 400, size
    admin_client.post(f"{path}/{box.id}/retire")
    assert box.retired_at is not None


def test_a_package_is_renamed_and_resized_until_it_moves(
    admin_client: TestClient, session: Session
) -> None:
    """Its name, as items' are compared, is its own among its item's,
    retired ones too; its size is fixed once some has moved or been opened
    (inventory-tng-f4f)."""
    spools = Spools(session)
    box = add(session, ItemPackage(item_id=spools.cable.id, name="Box", size=50))
    add(
        session,
        ItemPackage(item_id=spools.cable.id, name="Old reel", size=1, retired_at=NOW),
    )
    page = f"{ITEMS}/{spools.cable.id}"

    def send(package: ItemPackage, name: str, size: str) -> Response:
        return admin_client.post(
            f"{page}/packages/{package.id}",
            data={
                "package_name": name,
                "size": size,
                "package_version": shown(package),
            },
        )

    assert send(box, "Carton", "25").status_code == 303
    assert (box.name, box.size) == ("Carton", 25)
    last = history(session, "item_package", box.id)[-1].new_row
    assert last is not None
    assert last["name"] == "Carton"
    # A retired package's name, spelt another way, is taken.
    taken = send(box, "old-REEL", "25")
    assert taken.status_code == 400
    assert str(PACKAGE_TAKEN) in text(taken)
    assert "old-REEL" in box_values(taken.text, "package_name")
    # Moved in its size: renamed, never resized.
    fixed = send(spools.spool, "Spool", "300")
    assert "Its size is fixed" in text(fixed)
    assert send(spools.spool, "Spool 305 m", "305").status_code == 303
    # Saved as typed (Ali, 2026-11-15, inventory-tng-c0c.3).
    assert (spools.spool.name, spools.spool.size) == ("Spool 305 m", 305)
    # A form shown before another's save is refused, as it was changed.
    stale = admin_client.post(
        f"{page}/packages/{box.id}",
        data={"package_name": "Bag", "size": "25", "package_version": "1"},
    )
    assert "someone else saved this" in text(stale)
    for name, size in (("", "25"), ("Bag", "lots"), ("--", "25")):
        assert send(box, name, size).status_code == 400
    # Never as short as its too-short length.
    box.short_below = Decimal(20)
    session.flush()
    short = send(box, "Carton", "20")
    assert "Larger than its too-short length" in text(short)
    # Labelled, its size is said on boxes: fixed, and offered no more.
    add(session, Label(item_id=spools.cable.id, package_id=box.id))
    assert "Its size is fixed" in text(send(box, "Carton", "30"))
    assert send(box, "Crate", "25").status_code == 303


def test_a_packages_labels_fix_its_size_until_revoked(
    admin_client: TestClient, session: Session
) -> None:
    """Labelled, a package's size is fixed, and its Holds links to the
    item's labels, to revoke them; revoked, it may be resized. Moved or
    opened, it is fixed for good, labels revoked or not, and links to none
    (Ali, 2026-11-23, inventory-tng-37dj)."""
    spools = Spools(session)
    box = add(session, ItemPackage(item_id=spools.cable.id, name="Box", size=50))
    page = f"{ITEMS}/{spools.cable.id}"
    wording = (
        "Holds 50 m: fixed, as a label has been printed for it. "
        "Revoke its labels to change it."
    )
    label = labels.make_for_item(session, spools.cable.id, box.id)
    assert label is not None
    assert items.sizes_fixed(session, [box.id, spools.spool.id]) == {
        box.id: "labelled",
        spools.spool.id: "used",
    }
    shown_page = admin_client.get(page).text
    assert wording in words_on(shown_page)
    # The box's alone: the spool has moved, and so has the item's unit.
    read(shown_page).one(
        "a", "Revoke its labels", href=f"{LABELS}?item={spools.cable.id}"
    )
    _, broken = attempt(
        session, lambda: items.save_package(session, box, "Box", Decimal(25), seen=None)
    )
    assert broken == "package_size_fixed"
    assert labels.revoke(session, label.id, NOW)
    assert items.sizes_fixed(session, [box.id]) == {}
    assert wording not in words_on(admin_client.get(page).text)
    items.save_package(session, box, "Box", Decimal(25), seen=None)
    assert box.size == 25
    # The spool has moved: fixed for good, its labels revoked too.
    spool_label = labels.make_for_item(session, spools.cable.id, spools.spool.id)
    assert spool_label is not None
    assert labels.revoke(session, spool_label.id, NOW)
    assert items.sizes_fixed(session, [spools.spool.id]) == {spools.spool.id: "used"}


def test_a_package_opened_keeps_its_size(session: Session) -> None:
    spools = Spools(session, tracked=True)
    box = add(session, ItemPackage(item_id=spools.cable.id, name="Box", size=50))
    open_packages.make(session, uuid.uuid4(), box.id, spools.store.id)
    _, broken = attempt(
        session, lambda: items.save_package(session, box, "Box", Decimal(25), seen=None)
    )
    assert broken == "package_size_fixed"


def test_a_retired_package_is_brought_back(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios)
    box = add(session, ItemPackage(item_id=item.id, name="Box", size=Decimal(4)))
    path = f"{ITEMS}/{item.id}/packages/{box.id}"
    # Its item is counted each: a package holds a whole number.
    part = admin_client.post(
        path, data={"package_name": "Box", "size": "2.5", "package_version": "1"}
    )
    assert "whole units" in text(part)
    retired = admin_client.post(f"{path}/retire")
    assert box.retired_at is not None
    # The notice offers it back, as the row does.
    offered = admin_client.get(retired.headers["location"])
    assert ("post", f"{path}/restore") in forms(offered.text)
    assert str(PACKAGE_RETIRED) in text(offered)
    assert admin_client.post(f"{path}/restore").status_code == 303
    assert box.retired_at is None
    # A current one is not brought back.
    assert admin_client.post(f"{path}/restore").status_code == 404
    # Under a retired item, its packages are read only.
    admin_client.post(f"{path}/retire")
    retire(session, item)
    refused = admin_client.post(f"{path}/restore")
    assert "Bring it back first" in text(refused)
    assert box.retired_at is not None
    renamed = admin_client.post(
        f"{path}", data={"package_name": "Bag", "size": "4", "package_version": "1"}
    )
    assert renamed.status_code == 404


# Whether opened packages are followed (docs/decisions/0020, Amendment).


def test_an_items_opened_packages_are_followed_when_ticked(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios, "Cat6 cable")
    assert item.track_opened is False  # A new item's are not.
    page = admin_client.get(f"{ITEMS}/{item.id}").text
    assert box_values(page, "track_opened") == [None]
    assert checked(page, "track_opened") == []
    path = f"{ITEMS}/{item.id}"
    admin_client.post(path, data=shown_item_form(item, track_opened="on"))
    session.refresh(item)
    assert item.track_opened is True
    assert checked(admin_client.get(path).text, "track_opened") == [None]
    # A box left unticked is not sent: the form says off.
    admin_client.post(path, data=shown_item_form(item))
    session.refresh(item)
    assert item.track_opened is False


def test_a_package_says_whether_its_opened_ones_are_followed(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios, "Cat6 cable")
    spool = add(session, ItemPackage(item_id=item.id, name="Spool", size=Decimal(305)))
    path = f"{ITEMS}/{item.id}/packages/{spool.id}/tracking"
    assert "Opened ones loose\n        (as the item)" in text(
        admin_client.get(f"{ITEMS}/{item.id}")
    )
    for sent, kept in (("on", True), ("off", False), ("", None)):
        done = admin_client.post(path, data={"track": sent})
        assert done.headers["location"] == (
            f"{ITEMS}/{item.id}?notice=package_tracking&section=packages#packages"
        )
        session.refresh(spool)
        assert spool.track_opened is kept
    admin_client.post(path, data={"track": "on"})
    assert "Opened ones followed\n        (set on this package)" in text(
        admin_client.get(f"{ITEMS}/{item.id}")
    )
    assert admin_client.post(path, data={"track": "maybe"}).status_code == 404
    other = new_item(session, radios, "Other")
    wrong = f"{ITEMS}/{other.id}/packages/{spool.id}/tracking"
    assert admin_client.post(wrong, data={"track": "on"}).status_code == 404
    item.retired_at = NOW
    session.flush()
    refused = admin_client.post(path, data={"track": "off"})
    assert refused.status_code == 409
    session.refresh(spool)
    assert spool.track_opened is True


def test_items_ticked_are_set_together_and_packages_keep_their_own(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    cable = new_item(session, radios, "Cat6 cable")
    rope = new_item(session, radios, "Rope")
    gone = new_item(session, radios, "Old cable")
    gone.retired_at = NOW
    own = add(
        session,
        ItemPackage(item_id=cable.id, name="Box", size=Decimal(10), track_opened=False),
    )
    page = admin_client.get(ITEMS).text
    assert str(cable.id) in box_values(page, "item")
    assert str(gone.id) not in box_values(page, "item")
    # The list as shown comes in the form's address, sorted and sized too.
    assert ("post", f"{ITEMS}/tracking") in forms(page)
    sorted_page = admin_client.get(ITEMS, params={"sort": "unit", "per": "25"}).text
    assert ("post", f"{ITEMS}/tracking?sort=unit&per=25") in forms(sorted_page)
    done = admin_client.post(
        f"{ITEMS}/tracking?q=cable&sort=unit&per=25",
        data={"item": [str(cable.id), str(gone.id)], "track": "on"},
    )
    assert done.headers["location"] == (
        f"{ITEMS}?q=cable&sort=unit&per=25&notice=tracking_on"
    )
    for row in (cable, rope, gone, own):
        session.refresh(row)
    assert (cable.track_opened, rope.track_opened, gone.track_opened) == (
        True,
        False,
        False,
    )
    assert own.track_opened is False
    listed = read(admin_client.get(ITEMS).text)
    assert listed.all("span", "Opened followed", class_="tag")
    # Ticked again, or kept: nothing to change, and it says so.
    again = admin_client.post(
        f"{ITEMS}/tracking?q=cable&pick={cable.id}",
        data={"item": [str(cable.id)], "track": "on"},
    )
    assert again.headers["location"] == (
        f"{ITEMS}?q=cable&pick={cable.id}&notice=tracking_same"
    )
    retired = text(admin_client.get(ITEMS, params={"show": "retired"}))
    assert "Track opened packages" not in retired
    none = admin_client.post(f"{ITEMS}/tracking", data={"track": "off"})
    assert none.headers["location"] == f"{ITEMS}?notice=tracking_none"
    bad = admin_client.post(f"{ITEMS}/tracking", data={"item": [str(cable.id)]})
    assert bad.status_code == 404


def test_a_refused_package_keeps_the_items_setting_shown(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Else Save on the item's form, after, would turn it off unasked."""
    item = new_item(session, radios, "Cat6 cable")
    item.track_opened = True
    session.flush()
    refused = admin_client.post(
        f"{ITEMS}/{item.id}/packages", data={"package_name": "Spool", "size": "lots"}
    )
    assert refused.status_code == 400
    assert checked(refused.text, "track_opened") == [None]


def test_a_package_shows_its_own_setting_only_where_it_differs(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios, "Cat6 cable")
    spool = add(
        session,
        ItemPackage(
            item_id=item.id, name="Spool", size=Decimal(305), track_opened=True
        ),
    )
    page = f"{ITEMS}/{item.id}"
    assert "(set on this package)" in text(admin_client.get(page))
    item.track_opened = True
    session.flush()
    assert "(set on this package)" not in text(admin_client.get(page))
    spool.retired_at = NOW
    session.flush()
    path = f"{page}/packages/{spool.id}/tracking"
    assert admin_client.post(path, data={"track": "off"}).status_code == 404


def test_a_package_of_a_fixed_size_is_renamed_whatever_its_unit_became(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Its size is not on the form once fixed: kept as it is, though its
    item is counted each now and the size is a part of one."""
    item = new_item(session, radios)
    item.unit = "m"
    box = add(session, ItemPackage(item_id=item.id, name="Coil", size=Decimal("2.5")))
    add(session, Label(item_id=item.id, package_id=box.id))
    item.unit = "each"
    session.flush()
    page = admin_client.get(f"{ITEMS}/{item.id}").text
    assert ("post", f"{ITEMS}/{item.id}/packages/{box.id}") in forms(page)
    done = admin_client.post(
        f"{ITEMS}/{item.id}/packages/{box.id}",
        data={"package_name": "Reel", "package_version": shown(box)},
    )
    assert done.status_code == 303
    assert (box.name, box.size) == ("Reel", Decimal("2.5"))


def test_a_too_short_length_refused_as_resized_meanwhile_says_so_beside_it(
    admin_client: TestClient,
    session: Session,
    radios: Category,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = new_item(session, radios)
    box = add(session, ItemPackage(item_id=item.id, name="Box", size=Decimal(10)))

    def resized_meanwhile(*_: object) -> None:
        raise RuleBroken("ck_item_package_short_below_under_size")

    monkeypatch.setattr(items, "package_settings", resized_meanwhile)
    refused = admin_client.post(
        f"{ITEMS}/{item.id}/packages/{box.id}/settings",
        data={"short_below": "5", "minimum_sealed": ""},
    )
    assert refused.status_code == 400
    assert str(SHORTER_THAN_WHOLE) in text(refused)


def test_packages_are_listed_current_first_then_smallest_ties_by_id(
    session: Session, radios: Category
) -> None:
    item = new_item(session, radios)
    old = add(session, ItemPackage(item_id=item.id, name="Old", size=1, retired_at=NOW))
    first, second = same_size_twins(session, item)
    small = add(session, ItemPackage(item_id=item.id, name="Small", size=Decimal(2)))
    listed = queries.packages_of_item(session, item.id)
    assert listed == [small, first, second, old]


def test_an_items_amounts_read_grouped_but_its_boxes_hold_them_plainly(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Shown as the locale writes numbers; a box holds what its form reads
    back (inventory-tng-31q3)."""
    item = add(
        session,
        Item(name="Cable", category_id=radios.id, unit="m", minimum=Decimal("1234.5")),
    )
    add(
        session,
        ItemPackage(
            item_id=item.id,
            name="Spool",
            size=Decimal("2000.000"),
            short_below=Decimal("1234.500"),
        ),
    )
    shown = admin_client.get(f"{ITEMS}/{item.id}")
    page = read(shown.text)
    minimum, short_below = box_values(page, "minimum"), box_values(page, "short_below")
    assert minimum == short_below == ["1234.5"]
    # The spool's, then an empty one to add a package.
    assert box_values(page, "size") == ["2000", ""]
    assert "2,000 m" in text(shown)
