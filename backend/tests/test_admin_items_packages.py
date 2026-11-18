"""An item's packages (admin/items.py): adding, renaming and resizing them
until they move, retiring and bringing them back, and whether opened ones
are followed (docs/decisions/0020, Amendment)."""

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import open_packages
from inventory_tng.admin.items import ITEMS, PACKAGE_RETIRED
from inventory_tng.catalogue import items
from inventory_tng.catalogue.forms import PACKAGE_TAKEN, SHORTER_THAN_WHOLE
from inventory_tng.core.db import RuleBroken, attempt
from inventory_tng.models import Category, Item, ItemPackage, Label

from helpers.items import NOW, new_item
from helpers.markup import read, text
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
    assert 'value="old-REEL"' in taken.text
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
    assert f'action="{path}/restore"' in offered.text
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


def saving(item: Item, **fields: str) -> dict[str, str]:
    """The item's form as shown, with `fields` changed."""
    return {
        "name": item.name,
        "category_id": str(item.category_id),
        "unit": item.unit,
        "version": shown(item),
    } | fields


def test_an_items_opened_packages_are_followed_when_ticked(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios, "Cat6 cable")
    assert item.track_opened is False  # A new item's are not.
    page = text(admin_client.get(f"{ITEMS}/{item.id}"))
    assert 'name="track_opened" >' in page or 'name="track_opened">' in page
    path = f"{ITEMS}/{item.id}"
    admin_client.post(path, data=saving(item, track_opened="on"))
    session.refresh(item)
    assert item.track_opened is True
    assert 'name="track_opened" checked' in text(admin_client.get(path))
    # A box left unticked is not sent: the form says off.
    admin_client.post(path, data=saving(item))
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
    page = text(admin_client.get(ITEMS))
    assert f'name="item" value="{cable.id}"' in page
    assert f'name="item" value="{gone.id}"' not in page
    # The list as shown comes in the form's address, sorted and sized too.
    assert f'action="{ITEMS}/tracking"' in page
    sorted_page = admin_client.get(ITEMS, params={"sort": "unit", "per": "25"}).text
    assert f'action="{ITEMS}/tracking?sort=unit&amp;per=25"' in sorted_page
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
    assert 'name="track_opened" checked' in text(refused)


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
    assert f'action="{ITEMS}/{item.id}/packages/{box.id}"' in page
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
