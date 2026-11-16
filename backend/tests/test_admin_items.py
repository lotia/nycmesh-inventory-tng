"""Items and their packages (admin/items.py)."""

import html
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from markupsafe import escape
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng import items, open_packages
from inventory_tng.accounts import name_up_to
from inventory_tng.admin.common import (
    ABOVE_ZERO,
    NAME_MAX,
    RULES,
    TOO_BIG,
    TYPE_A_NUMBER,
    ZERO_OR_MORE,
    at_most_places,
    quantity,
)
from inventory_tng.admin.items import (
    ALIAS_ITS_OWN,
    ALIAS_TAKEN,
    ASK_UNIT,
    IS_RETIRED,
    ITEMS,
    NO_LETTERS,
    PACKAGE_RETIRED,
    PACKAGE_TAKEN,
    PICK_A_CATEGORY,
    PICK_A_UNIT,
    READ_AS_ONE,
    RENAMED,
    SHORTER_THAN_WHOLE,
    TAKEN,
    TAKEN_FIELDS,
    read_as_one,
)
from inventory_tng.admin.listing import PER_PAGE
from inventory_tng.db import RuleBroken, attempt
from inventory_tng.local_time import DEFAULT_ZONE
from inventory_tng.models import (
    CURRENT_ALIAS,
    Category,
    Item,
    ItemAlias,
    ItemPackage,
    Label,
    LedgerTransaction,
    Location,
    Movement,
    ReadingRule,
)
from inventory_tng.reading_rules import NAMED, both

from helpers.rows import add, history, item_fields, named, shown, tag
from helpers.sign_in import text
from helpers.stock import Spools, retire

NOW = datetime(2026, 11, 1, tzinfo=UTC)


@pytest.fixture
def radios(session: Session) -> Category:
    return add(session, Category(name="Radios"))


def new_item(session: Session, category: Category, name: str = "LiteBeam") -> Item:
    return add(session, Item(name=name, category_id=category.id))


def test_adding_an_item(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    done = admin_client.post(
        ITEMS,
        data={
            "name": "LiteBeam 5AC",
            "category_id": str(radios.id),
            "unit": "each",
            "manufacturer": "Ubiquiti",
            "part_number": "LBE-5AC",
        },
    )
    item = named(session, Item, "LiteBeam 5AC")
    assert done.headers["location"] == f"{ITEMS}/{item.id}?notice=added"
    assert (item.manufacturer, item.part_number) == ("Ubiquiti", "LBE-5AC")
    assert (history(session, "item", item.id)[0].actor or "").startswith("admin:")
    assert "LiteBeam 5AC" in text(admin_client.get(ITEMS))


def test_an_item_name_needs_more_than_punctuation(
    admin_client: TestClient, radios: Category
) -> None:
    """Punctuation alone would be one name with every other such."""
    form = {"category_id": str(radios.id), "unit": "each"}
    refused = admin_client.post(ITEMS, data=form | {"name": "- ?"})
    assert refused.status_code == 400
    assert str(NO_LETTERS) in text(refused)
    assert admin_client.post(ITEMS, data=form | {"name": "Câble ½"}).status_code == 303
    assert admin_client.post(ITEMS, data=form | {"name": "1234"}).status_code == 303


def test_a_name_of_signs_beyond_ascii_needs_a_letter_or_digit(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """A key keeps an en dash, but a name needs a letter or a digit, an
    item's and a package's alike."""
    form = {"category_id": str(radios.id), "unit": "each"}
    item = new_item(session, radios, "Probe")
    path = f"{ITEMS}/{item.id}/packages"
    for name in ("\u2013", "\u201c \u2014 \u201d", "\u00b1\u00b0"):
        refused = admin_client.post(ITEMS, data=form | {"name": name})
        assert refused.status_code == 400
        assert str(NO_LETTERS) in text(refused)
        refused = admin_client.post(path, data={"package_name": name, "size": "1"})
        assert refused.status_code == 400
        assert str(NO_LETTERS) in text(refused)
    assert (
        admin_client.post(
            path, data={"package_name": "\u00b2", "size": "1"}
        ).status_code
        == 303
    )


def test_an_item_needs_a_name_and_a_current_category(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    radios.retired_at = radios.created_at
    session.flush()
    refused = admin_client.post(
        ITEMS, data={"name": "", "category_id": str(radios.id), "unit": "each"}
    )
    assert refused.status_code == 400
    page = text(refused)
    assert name_up_to(NAME_MAX) in page
    assert str(PICK_A_CATEGORY) in page
    assert "Add a" in page  # No current categories: add one first.


def test_names_and_part_numbers_are_unique_naming_the_other(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Said beside the field another item has, naming that item."""
    add(
        session,
        Item(
            name="Lite-Beam 5AC",
            category_id=radios.id,
            manufacturer="Ubiquiti",
            part_number="LBE",
        ),
    )
    for form, field, words in (
        (
            {"name": "litebeam 5ac"},
            "name",
            TAKEN_FIELDS["name"]("Lite-Beam 5AC"),
        ),
        (
            {"name": "Other", "manufacturer": "ubiquiti", "part_number": "lbe"},
            "part_number",
            TAKEN_FIELDS["part_number"]("Lite-Beam 5AC"),
        ),
    ):
        refused = admin_client.post(
            ITEMS, data={"category_id": str(radios.id), "unit": "each"} | form
        )
        assert refused.status_code == 400
        assert f'id="{field}-problem">{words}</span>' in refused.text


def test_a_name_pasted_with_look_alikes_clashes_naming_the_other(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """A non-breaking space, a curly quote or an en dash is read as the
    plain one, so the name clashes with the one typed plainly, which the
    form names; a name is kept as typed."""
    form = {"category_id": str(radios.id), "unit": "each"}
    for kept, pasted in (
        ("Tough cable", "Tough\u00a0Cable"),
        ("Ubiquiti's dish", "Ubiquiti\u2019s dish"),
        ("Patch\u2014cord", "Patch-cord"),
    ):
        assert admin_client.post(ITEMS, data=form | {"name": kept}).status_code == 303
        refused = admin_client.post(ITEMS, data=form | {"name": pasted})
        assert refused.status_code == 400
        taken = escape(TAKEN_FIELDS["name"](kept))
        assert f'id="name-problem">{taken}</span>' in refused.text
    assert named(session, Item, "Patch\u2014cord").name_key == "patchcord"


def test_a_saved_item_is_refused_what_another_has_its_own_respelt(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Respelling its own name is no clash; another's old code is, and a
    retired holder is said to be."""
    item = new_item(session, radios, "LiteBeam")
    other = add(
        session,
        Item(name="Omni", category_id=radios.id, old_code="NYCM-1", retired_at=NOW),
    )
    form = {
        "name": "Lite Beam",
        "category_id": str(radios.id),
        "unit": "each",
        "version": str(item.edit_version),
    }
    path = f"{ITEMS}/{item.id}"
    refused = admin_client.post(path, data=form | {"old_code": "NYCM-1"})
    assert refused.status_code == 400
    taken = TAKEN_FIELDS["old_code"](other.name)
    assert f'id="old_code-problem">{taken} {IS_RETIRED}</span>' in refused.text
    assert admin_client.post(path, data=form).status_code == 303
    session.refresh(item)
    assert item.name == "Lite Beam"


def test_a_name_is_checked_and_saved_in_canonical_spelling(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Typed with the micro sign, a name is compared and stored with μ
    (docs/decisions/0021, 2026-11-14): another's in that spelling is taken,
    naming it, and its own respelt so is no change asked about."""
    form = {"category_id": str(radios.id), "unit": "each"}
    assert (
        admin_client.post(ITEMS, data=form | {"name": "Cap 10\u00b5F"}).status_code
        == 303
    )
    item = named(session, Item, "Cap 10\u03bcF")
    refused = admin_client.post(ITEMS, data=form | {"name": "cap 10\u00b5f"})
    assert refused.status_code == 400
    assert TAKEN_FIELDS["name"]("Cap 10\u00a0\u03bcF") in refused.text
    path = f"{ITEMS}/{item.id}"
    edit = form | {"version": str(item.edit_version)}
    saved = admin_client.post(path, data=edit | {"name": "Cap 10\u00b5F"})
    assert saved.status_code == 303
    # Close names, asked about, then sent again as the form sends them.
    add(session, Item(name="Cap 10\u03bcF 50V", category_id=radios.id))
    asked = admin_client.post(ITEMS, data=form | {"name": "Cap 10\u00b5F 5OV"})
    assert "A different item?" in text(asked)
    again = {"name": "Cap 10\u00b5F 5OV", "different": "Cap 10\u00b5F 5OV"}
    assert admin_client.post(ITEMS, data=form | again).status_code == 303
    named(session, Item, "Cap 10\u03bcF 5OV")


def test_a_number_grouped_by_spaces_is_judged_as_search_reads_it(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Ali, 2026-11-16 (inventory-tng-t78, inventory-tng-uxo6): the form
    reads `1 000` before a unit as one number, as search does, so asks
    about `M` after it, and refuses a name reading alike with another's,
    spaces ignored, naming it."""
    form = {"category_id": str(radios.id), "unit": "each"}
    asked = admin_client.post(ITEMS, data=form | {"name": "Box 1 000 M"})
    assert asked.status_code == 400
    assert "<legend>\u201c1 000M\u201d is new. Is it a unit?</legend>" in text(asked)
    add(session, Item(name="Cap 1000 uF", category_id=radios.id))
    refused = admin_client.post(ITEMS, data=form | {"name": "Cap 1 000 \u03bcF"})
    assert refused.status_code == 400
    assert TAKEN_FIELDS["name"]("Cap 1000\u00a0uF") in refused.text


def test_a_unit_after_any_space_is_judged_as_search_reads_it(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """inventory-tng-vw0f: the form finds a unit after a number and a
    no-break space as after a plain one, so asks about `M` after it, and
    refuses a name reading alike with another's, naming it."""
    form = {"category_id": str(radios.id), "unit": "each"}
    asked = admin_client.post(ITEMS, data=form | {"name": "Box 1000\u00a0M"})
    assert asked.status_code == 400
    assert "<legend>\u201c1000M\u201d is new. Is it a unit?</legend>" in text(asked)
    add(session, Item(name="Cap 1000 uF", category_id=radios.id))
    refused = admin_client.post(ITEMS, data=form | {"name": "Cap 1000\u00a0\u03bcF"})
    assert refused.status_code == 400
    assert TAKEN_FIELDS["name"]("Cap 1000\u00a0uF") in refused.text


def test_a_package_name_is_checked_in_canonical_spelling(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """As stored: \u2103 alone is stored \u00b0C, which has a letter."""
    item = new_item(session, radios, "Probe")
    path = f"{ITEMS}/{item.id}/packages"
    added = admin_client.post(path, data={"package_name": "\u2103", "size": "1"})
    assert added.status_code == 303
    package = named(session, ItemPackage, "\u00b0C")
    resent = admin_client.post(
        f"{path}/{package.id}",
        data={
            "package_name": "\u2103",
            "size": "1",
            "package_version": shown(package),
        },
    )
    assert resent.status_code == 303


def test_a_clash_gone_meanwhile_is_said_in_general(
    admin_client: TestClient,
    session: Session,
    radios: Category,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other item renamed between the refusal and the look for it."""
    new_item(session, radios, "LiteBeam")
    monkeypatch.setattr(items, "taken_by", lambda *_: None)
    form = {"name": "litebeam", "category_id": str(radios.id), "unit": "each"}
    refused = admin_client.post(ITEMS, data=form)
    assert str(TAKEN) in text(refused)


def test_searching(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    new_item(session, radios, "LiteBeam 5AC")
    new_item(session, radios, "Patch cable")
    page = text(admin_client.get(ITEMS, params={"q": "litebeem"}))
    assert "LiteBeam 5AC" in page
    assert "Patch cable" not in page


def test_editing_every_field(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios)
    cables = add(session, Category(name="Cables"))
    form = {
        "name": "Cat6 cable",
        "category_id": str(cables.id),
        "unit": "m",
        "manufacturer": "",
        "part_number": "",
        "old_code": "NYCM-0042",
        "minimum": "305",
        "notes": "Outdoor",
        "ordering_notes": "Buy by the reel",
        "version": shown(item),
    }
    done = admin_client.post(f"{ITEMS}/{item.id}", data=form)
    # Renamed: its old name kept as an alias.
    assert done.headers["location"] == (
        f"{ITEMS}/{item.id}?notice=renamed&section=aliases#aliases"
    )
    assert (item.name, item.category_id, item.unit) == ("Cat6 cable", cables.id, "m")
    assert (item.old_code, item.minimum, item.manufacturer) == (
        "NYCM-0042",
        Decimal(305),
        None,
    )
    assert 'value="305"' in admin_client.get(f"{ITEMS}/{item.id}").text


def test_the_unit_is_fixed_once_stock_has_moved(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios)
    store = add(session, Location(name="Hub", kind="store"))
    batch = add(
        session,
        LedgerTransaction(
            time_zone=DEFAULT_ZONE,
            idempotency_key=uuid.uuid4(),
            kind="receipt",
            to_location_id=store.id,
        ),
    )
    add(session, Movement(transaction_id=batch.id, item_id=item.id, quantity=1))
    page = text(admin_client.get(f"{ITEMS}/{item.id}"))
    assert "fixed, as stock has moved in it" in page
    assert '<input type="hidden" name="unit" value="each">' in page
    form = {"name": item.name, "category_id": str(radios.id), "unit": "m"}
    form |= {"version": shown(item)}
    refused = admin_client.post(f"{ITEMS}/{item.id}", data=form)
    assert refused.status_code == 400
    assert str(RULES["item_unit_fixed"]) in text(refused)
    assert item.unit == "each"
    # The form sends the fixed unit back as it is, so the rest still saves.
    kept = admin_client.post(f"{ITEMS}/{item.id}", data=form | {"unit": "each"})
    assert kept.status_code == 303
    # A form without a unit is refused, rather than keeping the one stored.
    unitless = admin_client.post(f"{ITEMS}/{item.id}", data=form | {"unit": ""})
    assert str(PICK_A_UNIT) in text(unitless)
    negative = admin_client.post(f"{ITEMS}/{item.id}", data=form | {"minimum": "-1"})
    assert negative.status_code == 400


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


def test_retiring_and_bringing_back(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios)
    admin_client.post(f"{ITEMS}/{item.id}/retire")
    assert item.retired_at is not None
    assert "Retired" in text(admin_client.get(ITEMS, params={"show": "retired"}))
    # Its name stays its own while it is retired, so it can come back.
    taken = admin_client.post(
        ITEMS, data={"name": item.name, "category_id": str(radios.id), "unit": "each"}
    )
    assert taken.status_code == 400
    assert f"{TAKEN_FIELDS['name'](item.name)} {IS_RETIRED}" in text(taken)
    assert admin_client.post(f"{ITEMS}/{item.id}/restore").status_code == 303


def test_unknown_items_and_others_packages_are_not_found(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios)
    other = new_item(session, radios, "Other")
    theirs = add(session, ItemPackage(item_id=other.id, name="Box", size=4))
    assert admin_client.get(f"{ITEMS}/999999").status_code == 404
    assert admin_client.post(f"{ITEMS}/999999", data={}).status_code == 404
    assert admin_client.post(f"{ITEMS}/999999/packages").status_code == 404
    assert admin_client.post(f"{ITEMS}/999999/retire").status_code == 404
    assert admin_client.post(f"{ITEMS}/999999/restore").status_code == 404
    # A current item is not brought back.
    assert admin_client.post(f"{ITEMS}/{item.id}/restore").status_code == 404
    path = f"{ITEMS}/{item.id}/packages/{theirs.id}"
    for action in ("/retire", "/restore", ""):
        assert admin_client.post(f"{path}{action}").status_code == 404


@pytest.mark.parametrize(
    ("typed", "positive", "parsed"),
    [
        ("", True, None),
        (" 2.5 ", True, Decimal("2.5")),
        ("0", False, Decimal(0)),
        ("0", True, ABOVE_ZERO),
        ("-1", False, ZERO_OR_MORE),
        ("1.2345", False, at_most_places(3)),
        ("NaN", False, TOO_BIG),
        ("1e12", False, TOO_BIG),
        ("lots", False, TYPE_A_NUMBER),
    ],
)
def test_quantities_typed(typed: str, positive: bool, parsed: object) -> None:
    assert quantity(typed, positive=positive) == parsed


def test_a_retired_item_says_what_is_left(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios)
    store = add(session, Location(name="Hub", kind="store"))
    batch = add(
        session,
        LedgerTransaction(
            time_zone=DEFAULT_ZONE,
            idempotency_key=uuid.uuid4(),
            kind="receipt",
            to_location_id=store.id,
        ),
    )
    add(session, Movement(transaction_id=batch.id, item_id=item.id, quantity=3))
    admin_client.post(f"{ITEMS}/{item.id}/retire")
    page = text(admin_client.get(f"{ITEMS}/{item.id}"))
    assert "can't be received" in page
    assert "take the 3 each left, until it runs out" in page


def test_an_item_comes_back_only_into_a_current_category(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios)
    admin_client.post(f"{ITEMS}/{item.id}/retire")
    radios.retired_at = radios.created_at
    session.flush()
    refused = admin_client.post(f"{ITEMS}/{item.id}/restore")
    assert refused.status_code == 400
    assert "Its category is retired" in text(refused)
    assert item.retired_at is not None


def test_bringing_an_item_back(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios)
    admin_client.post(f"{ITEMS}/{item.id}/retire")
    done = admin_client.post(f"{ITEMS}/{item.id}/restore")
    assert done.status_code == 303
    session.refresh(item)
    assert item.retired_at is None


def test_an_edit_with_a_problem_changes_nothing(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios)
    refused = admin_client.post(
        f"{ITEMS}/{item.id}",
        data={"name": "", "category_id": str(radios.id), "unit": "each"},
    )
    assert refused.status_code == 400
    session.refresh(item)
    assert item.name == "LiteBeam"


def test_changes_are_logged_by_id(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        admin_client.post(
            ITEMS,
            data={"name": "Radio X", "category_id": str(radios.id), "unit": "each"},
        )
    (line,) = [line for line in logs if line["event"] == "item added"]
    assert line["item_id"] == named(session, Item, "Radio X").id
    assert "Radio X" not in str(line)


def items_named(page: str) -> list[str]:
    """The items the list shows, in its order."""
    return re.findall(r'<td><a href="/admin/items/\d+">([^<]+)</a>', page)


def test_a_search_forgives_typos_best_first(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    for name in ("LiteAP AC", "LiteBeam 5AC", "Patch cable", "NanoBeam"):
        new_item(session, radios, name)
    page = text(admin_client.get(ITEMS, params={"q": "litebeam"}))
    shown = items_named(page)
    assert shown[0] == "LiteBeam 5AC"
    assert "Patch cable" not in shown
    total = session.scalar(
        select(func.count()).select_from(Item).where(Item.retired_at.is_(None))
    )
    said = " ".join(page.split())
    assert f"{len(shown)} of {total} items, matching “litebeam”" in said
    assert 'href="/admin/items">Clear search</a>' in page


def test_suggestions_come_from_the_filter_the_list_shows(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    beam = new_item(session, radios, f"LiteBeam {uuid.uuid4().hex[:6]}")
    old = new_item(session, radios, f"LiteBeam {uuid.uuid4().hex[:6]}")
    admin_client.post(f"{ITEMS}/{old.id}/retire")
    current = admin_client.get(f"{ITEMS}/suggest", params={"q": "litebem"}).json()
    names = [each["name"] for each in current]
    assert beam.name in names
    assert old.name not in names
    assert current[names.index(beam.name)] == {
        "name": beam.name,
        "hint": radios.name,
        "href": f"{ITEMS}?pick={beam.id}",
    }
    retired = admin_client.get(
        f"{ITEMS}/suggest", params={"q": "litebem", "show": "retired"}
    ).json()
    assert [each["name"] for each in retired] == [old.name]
    assert retired[0]["href"] == f"{ITEMS}?show=retired&pick={old.id}"
    assert admin_client.get(f"{ITEMS}/suggest").json() == []
    # A page size kept, as Find keeps it (inventory-tng-qwb).
    page = admin_client.get(ITEMS, params={"per": "100", "sort": "unit"}).text
    assert f'data-suggest="{ITEMS}/suggest?per=100"' in page
    sized = admin_client.get(
        f"{ITEMS}/suggest", params={"q": "litebem", "per": "100"}
    ).json()
    assert sized[0]["href"] == f"{ITEMS}?pick={beam.id}&per=100"


def test_picking_a_suggestion_narrows_the_list_to_it(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    beam = new_item(session, radios, "LiteBeam 5AC")
    new_item(session, radios, "LiteBeam M5")
    page = text(admin_client.get(ITEMS, params={"pick": str(beam.id)}))
    assert items_named(page) == ["LiteBeam 5AC"]
    assert "matching “LiteBeam 5AC”" in " ".join(page.split())


def test_current_retired_and_all(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    kept = new_item(session, radios, "Kept")
    gone = new_item(session, radios, "Gone")
    admin_client.post(f"{ITEMS}/{gone.id}/retire")
    for show, expected in (
        ("", {"Kept"}),
        ("retired", {"Gone"}),
        ("all", {"Kept", "Gone"}),
    ):
        page = text(admin_client.get(ITEMS, params={"show": show}))
        assert {kept.name, gone.name} & set(items_named(page)) == expected
        chosen = show or "current"
        link = f'href="{ITEMS}{"?show=" + show if show else ""}" aria-current="page"'
        assert link in page, chosen
    assert '<span class="tag">Retired</span>' in text(
        admin_client.get(ITEMS, params={"show": "all"})
    )


def test_pages_of_fifty(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    for number in range(PER_PAGE + 5):
        new_item(session, radios, f"Paged {number:03}")
    first = text(admin_client.get(ITEMS, params={"q": "Paged"}))
    assert len(items_named(first)) == PER_PAGE
    assert "Page 1 of 2" in first
    assert (
        f'href="{ITEMS}?q=Paged&amp;page=2"'
        in admin_client.get(ITEMS, params={"q": "Paged"}).text
    )
    second = text(admin_client.get(ITEMS, params={"q": "Paged", "page": "2"}))
    assert len(items_named(second)) == 5
    # A page past the end, or none, is the last or the first.
    assert "Page 2 of 2" in text(
        admin_client.get(ITEMS, params={"q": "Paged", "page": "9"})
    )
    assert "Page 1 of 2" in text(
        admin_client.get(ITEMS, params={"q": "Paged", "page": "x"})
    )


def test_items_sort_by_each_heading_either_way(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """By name unless a heading sorts them; pressed again, the other way
    (Ali, 2026-11-15, inventory-tng-qwb)."""
    mark = tag()
    antennas = add(session, Category(name=f"Antennas {mark}"))
    for name, category, unit, maker in (
        ("Bravo", radios, "m", "Ubiquiti"),
        ("Alpha", antennas, "each", "Mikrotik"),
        ("Charlie", antennas, "ft", None),
    ):
        add(
            session,
            Item(
                name=f"Sorted {mark} {name}",
                category_id=category.id,
                unit=unit,
                manufacturer=maker,
            ),
        )

    def order(**query: str) -> list[str]:
        page = text(admin_client.get(ITEMS, params={"per": "all"} | query))
        return [n.rsplit(" ", 1)[1] for n in items_named(page) if mark in n]

    assert order() == ["Alpha", "Bravo", "Charlie"]
    assert order(sort="name", order="desc") == ["Charlie", "Bravo", "Alpha"]
    # Within a category, by name.
    assert order(sort="category") == ["Alpha", "Charlie", "Bravo"]
    assert order(sort="category", order="desc") == ["Bravo", "Alpha", "Charlie"]
    assert order(sort="unit") == ["Alpha", "Charlie", "Bravo"]
    assert order(sort="part") == ["Alpha", "Bravo", "Charlie"]
    assert order(sort="part", order="desc") == ["Charlie", "Bravo", "Alpha"]
    assert order(sort="nonsense", order="up") == ["Alpha", "Bravo", "Charlie"]
    page = admin_client.get(ITEMS).text
    assert (
        '<th aria-sort="ascending"><a href="/admin/items?sort=name&amp;order=desc">Item'
        in page
    )
    assert '<a href="/admin/items?sort=category">Category</a>' in page
    page = admin_client.get(ITEMS, params={"sort": "category"}).text
    assert (
        '<th class="hidden sm:table-cell" aria-sort="ascending">'
        '<a href="/admin/items?sort=category&amp;order=desc">Category'
    ) in page
    assert '<a href="/admin/items?sort=name">Item</a>' in page


def test_items_show_25_50_100_or_all(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    mark = tag()
    for number in range(30):
        new_item(session, radios, f"Sized {mark} {number:03}")
    query = {"q": f"Sized {mark}"}
    page = admin_client.get(ITEMS, params=query).text
    assert len(items_named(page)) == 30
    assert f'<a href="{ITEMS}?q=Sized+{mark}&amp;per=25">25</a>' in page
    assert f'<a href="{ITEMS}?q=Sized+{mark}" aria-current="page">50</a>' in page
    page = text(admin_client.get(ITEMS, params=query | {"per": "25"}))
    assert len(items_named(page)) == 25
    assert "Page 1 of 2" in page
    second = text(admin_client.get(ITEMS, params=query | {"per": "25", "page": "2"}))
    assert len(items_named(second)) == 5
    every = admin_client.get(ITEMS, params=query | {"per": "all"}).text
    assert len(items_named(every)) == 30
    assert '" aria-current="page">All</a>' in every
    assert "Page 1 of" not in every
    # A size not offered is 50; a new search keeps the size.
    assert len(items_named(text(admin_client.get(ITEMS, params={"per": "7"})))) <= 50
    assert (
        '<input type="hidden" name="per" value="25">'
        in admin_client.get(ITEMS, params={"per": "25"}).text
    )


def test_items_filter_by_category(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    mark = tag()
    antennas = add(session, Category(name=f"Antennas {mark}"))
    kept = new_item(session, antennas, f"Filtered {mark} dish")
    new_item(session, radios, f"Filtered {mark} radio")
    page = text(admin_client.get(ITEMS, params={"category": str(antennas.id)}))
    assert items_named(page) == [kept.name]
    assert f'<option value="{antennas.id}" selected>' in page
    assert f'<a href="{ITEMS}">Clear</a>' in page
    # Kept by a search, a sort and the pages; the search box's suggestions
    # come from it too.
    found = admin_client.get(
        ITEMS, params={"category": str(antennas.id), "q": f"Filtered {mark}"}
    ).text
    assert [kept.name] == items_named(html.unescape(found))
    assert f"sort=name&amp;order=desc&amp;category={antennas.id}" not in found
    assert f"category={antennas.id}&amp;sort=name" in found
    assert f'data-suggest="{ITEMS}/suggest?category={antennas.id}"' in found
    offered = admin_client.get(
        f"{ITEMS}/suggest", params={"q": f"Filtered {mark}", "category": str(radios.id)}
    ).json()
    assert [each["name"] for each in offered] == [f"Filtered {mark} radio"]
    # None in it: it says so; one not a number is no filter.
    empty = add(session, Category(name=f"Empty {mark}"))
    page = text(admin_client.get(ITEMS, params={"category": str(empty.id)}))
    assert "None is in the filter." in page
    assert kept.name in text(
        admin_client.get(ITEMS, params={"category": "x", "per": "all", "q": kept.name})
    )


def test_a_search_sorted_by_a_heading_goes_back_to_best_match(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Best match first while searching; a heading sorts the matches, and
    Best match puts their order back (Ali, 2026-11-15, inventory-tng-qwb)."""
    mark = tag()
    # The closest last by name: a typo's one first.
    for name in ("LiteBeam", "Aaa LiteBeem"):
        new_item(session, radios, f"{name} {mark}")
    query = {"q": f"LiteBeam {mark}"}
    best = admin_client.get(ITEMS, params=query).text
    assert items_named(html.unescape(best))[0] == f"LiteBeam {mark}"
    assert "aria-sort" not in best
    assert '" aria-current="page">Best match</a>' in best
    by_name = admin_client.get(ITEMS, params=query | {"sort": "name"}).text
    assert items_named(html.unescape(by_name)) == sorted(
        items_named(html.unescape(by_name)), key=str.lower
    )
    assert items_named(html.unescape(by_name))[0] == f"Aaa LiteBeem {mark}"
    assert f'<a href="{ITEMS}?q=LiteBeam+{mark}">Best match</a>' in by_name
    # A new search from the box is best match first again: no sort sent.
    assert 'name="sort"' not in by_name.split('role="search"', 1)[1].split("</form>")[0]
    # Without a search, no Best match.
    assert "Best match" not in admin_client.get(ITEMS).text


def test_an_empty_list_says_what_to_do_next(admin_client: TestClient) -> None:
    page = text(admin_client.get(ITEMS, params={"q": "zzzqqqxxx"}))
    assert "Nothing matches. Check the spelling" in page


def test_taken_by_skips_the_item_itself(session: Session, radios: Category) -> None:
    item = add(
        session,
        Item(name="LiteBeam", category_id=radios.id, old_code="NYCM-1"),
    )
    asked = items.Fields.new(name="Lite beam", category_id=radios.id, old_code="NYCM-1")
    # Its own name and old code are no clash with itself.
    assert items.taken_by(session, asked, item.id) is None
    assert items.taken_by(session, asked) == ("name", item)


def test_a_name_close_to_others_asks_first(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Similar items are listed, retired ones too, and the form sent again
    adds it: a close name may be a different item (docs/decisions/0021)."""
    gen1 = new_item(session, radios, "LiteBeam 5AC Gen1")
    old = add(
        session, Item(name="LiteBeam 5AC Gen0", category_id=radios.id, retired_at=NOW)
    )
    new_item(session, radios, "Patch cable")
    form = {"name": "LiteBeam 5AC Gen2", "category_id": str(radios.id), "unit": "each"}
    asked = admin_client.post(ITEMS, data=form)
    assert asked.status_code == 200
    page = text(asked)
    found = re.search(r'<p class="warning" role="status">(.*?)</p>', page, re.DOTALL)
    assert found is not None
    said = found[1]
    assert f'<a href="{ITEMS}/{gen1.id}">LiteBeam 5AC Gen1</a>' in said
    assert (
        f'<a href="{ITEMS}/{old.id}">LiteBeam 5AC Gen0</a> <span class="tag">Retired'
        in said
    )
    assert "Patch cable" not in said
    assert "A different item? Add it again." in said
    assert 'value="LiteBeam 5AC Gen2"' in page
    assert session.scalar(select(Item).where(Item.name == form["name"])) is None
    assert f'name="different" value="{form["name"]}"' in asked.text
    # Changed after the question: asked about again.
    gen3 = form | {"name": "LiteBeam 5AC Gen3", "different": form["name"]}
    assert "A different item?" in text(admin_client.post(ITEMS, data=gen3))
    added = admin_client.post(ITEMS, data=form | {"different": form["name"]})
    assert added.status_code == 303
    named(session, Item, "LiteBeam 5AC Gen2")


def test_a_name_holding_another_items_asks_too(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """ "mast" is close to "Mast, telescopic", though most of it is new."""
    mast = new_item(session, radios, "mast")
    form = {"name": "Mast, telescopic", "category_id": str(radios.id), "unit": "each"}
    asked = text(admin_client.post(ITEMS, data=form))
    assert f'<a href="{ITEMS}/{mast.id}">mast</a>' in asked


def test_other_refusals_come_before_the_question(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """A retired category is said first, rather than asking, then refusing."""
    new_item(session, radios, "LiteBeam 5AC Gen1")
    radios.retired_at = NOW
    session.flush()
    form = {"name": "LiteBeam 5AC Gen2", "category_id": str(radios.id), "unit": "each"}
    refused = admin_client.post(ITEMS, data=form)
    assert refused.status_code == 400
    assert "A different item?" not in text(refused)


def test_renaming_close_to_another_asks_first(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Only a new name asks; saving the other fields does not."""
    new_item(session, radios, "LiteBeam 5AC Gen1")
    item = new_item(session, radios, "Radio")
    form = {
        "name": "Radio",
        "category_id": str(radios.id),
        "unit": "each",
        "version": str(item.edit_version),
        "notes": "On the shelf",
    }
    path = f"{ITEMS}/{item.id}"
    assert admin_client.post(path, data=form).status_code == 303
    form["version"] = str(item.edit_version)
    asked = admin_client.post(path, data=form | {"name": "LiteBeam 5AC Gen2"})
    assert "A different item? Save it again." in text(asked)
    session.refresh(item)
    assert item.name == "Radio"
    form |= {"name": "LiteBeam 5AC Gen2", "different": "LiteBeam 5AC Gen2"}
    assert admin_client.post(path, data=form).status_code == 303
    session.refresh(item)
    assert item.name == "LiteBeam 5AC Gen2"
    # Respelling its own name is the same name: nothing is asked.
    form = {k: v for k, v in form.items() if k != "different"}
    form |= {"name": "Litebeam-5AC-Gen2", "version": str(item.edit_version)}
    assert admin_client.post(path, data=form).status_code == 303


def test_the_add_form_says_how_to_name_an_item(
    admin_client: TestClient, radios: Category
) -> None:
    page = " ".join(text(admin_client.get(ITEMS)).split())
    assert "what it is, then what tells it apart from the items nearest it" in page
    assert "the maker goes in Manufacturer" in page


def test_a_name_asked_about_saves_however_it_was_spaced(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Sent again with spaces around it, as typed, it is the name asked
    about, so it saves rather than asking again."""
    new_item(session, radios, "LiteBeam 5AC Gen1")
    form = {"name": "LiteBeam 5AC Gen2 ", "category_id": str(radios.id), "unit": "each"}
    asked = admin_client.post(ITEMS, data=form)
    assert 'name="different" value="LiteBeam 5AC Gen2"' in asked.text
    again = admin_client.post(ITEMS, data=form | {"different": "LiteBeam 5AC Gen2"})
    assert again.status_code == 303


def test_a_name_asked_about_stays_answered_through_another_refusal(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Refused for its part number after the question, the form keeps the
    answer, so fixing the part number saves it."""
    new_item(session, radios, "LiteBeam 5AC Gen1")
    add(
        session,
        Item(name="Omni", category_id=radios.id, manufacturer="U", part_number="P"),
    )
    form = {
        "name": "LiteBeam 5AC Gen2",
        "category_id": str(radios.id),
        "unit": "each",
        "manufacturer": "U",
        "part_number": "P",
        "different": "LiteBeam 5AC Gen2",
    }
    refused = admin_client.post(ITEMS, data=form)
    assert refused.status_code == 400
    assert 'name="different" value="LiteBeam 5AC Gen2"' in refused.text
    assert "A different item?" not in text(refused)
    added = admin_client.post(ITEMS, data=form | {"part_number": "Q"})
    assert added.status_code == 303


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
    assert '<span class="tag">Opened followed</span>' in text(admin_client.get(ITEMS))
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


def test_a_name_is_saved_as_typed_and_its_units_said_under_it(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Ali, 2026-11-15 (inventory-tng-c0c.3): saved as typed, in canonical
    spelling; under the name, how each unit is read, with Not right? to correct
    it."""
    form = {"category_id": str(radios.id), "unit": "each"}
    done = admin_client.post(ITEMS, data=form | {"name": "Cable 305 m, 10 \u00b5F"})
    assert done.status_code == 303
    item = named(session, Item, "Cable 305 m, 10 \u03bcF")
    page = text(admin_client.get(f"{ITEMS}/{item.id}"))
    assert "Read as 305m · length.\n  Saved as typed." in page
    assert "Read as 10\u03bcF · capacitance." in page
    assert "<summary>Not right?</summary>" in page
    assert 'name="unit:m" value="none"' in page
    assert session.scalars(select(ReadingRule)).all() == []


def test_a_spelling_nothing_settles_is_asked_once(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """10g may be gigabits a second or grams, with no word near it saying
    which: the form asks, inline, saving nothing; the answer, kept through
    the question about close names, is kept as a reading rule for g after
    any number, and asked no more."""
    from structlog.testing import capture_logs

    new_item(session, radios, "Box 10g spare")
    form = {"name": "Box 10g", "category_id": str(radios.id), "unit": "each"}
    with capture_logs() as logs:
        asked = admin_client.post(ITEMS, data=form)
    assert asked.status_code == 400
    page = text(asked)
    assert str(ASK_UNIT) in page
    assert "<legend>“10g” is new. Is it a unit?</legend>" in page
    assert "10Gbps · data rate" in page
    assert "10g · grams" in page
    assert "Not a unit: keep “10g”" in page
    assert "Remembered for “g” after a number in every name." in page
    (line,) = [line for line in logs if line["event"] == "unit asked about"]
    assert line == {"event": "unit asked about", "item_id": None, "asked": 1} | {
        "log_level": "debug"
    }
    assert session.scalar(select(func.count()).select_from(Item)) == 1
    answered = form | {"unit:g": "Gbit/s"}
    close = admin_client.post(ITEMS, data=answered)
    assert "A different item?" in text(close)
    assert 'name="unit:g" value="Gbit/s" checked' in close.text
    assert session.scalars(select(ReadingRule)).all() == []
    again = answered | {"different": "Box 10g"}
    with capture_logs() as logs:
        assert admin_client.post(ITEMS, data=again).status_code == 303
    item = named(session, Item, "Box 10g")
    (rule,) = session.scalars(select(ReadingRule)).all()
    assert (rule.token, rule.reading, rule.respell) == ("g", "Gbit/s", False)
    assert rule.decided_by_admin_id is not None
    (line,) = [line for line in logs if line["event"] == "reading rules set"]
    assert (line["reading_rule_ids"], line["by"]) == ([rule.id], "form")
    assert line["item_id"] == item.id
    # The spare, saved before, reads so too.
    assert "10Gbit/s" in named(session, Item, "Box 10g spare").search_text
    shelf = form | {"name": "Shelf 40 g", "different": "Shelf 40 g"}
    assert admin_client.post(ITEMS, data=shelf).status_code == 303
    page = text(admin_client.get(f"{ITEMS}/{named(session, Item, 'Shelf 40 g').id}"))
    assert "Read as 40Gbps · data rate." in page


def test_not_right_corrects_a_reading_for_every_name(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """A correction under the name, saved with it, is kept as the rule: a
    unit the table reads as no unit, then back; a respelling rule goes on
    respelling."""
    item = new_item(session, radios, "Tape 25 m")
    other = new_item(session, radios, "Spare 25 m")
    form = {
        "name": "Tape 25 m",
        "category_id": str(radios.id),
        "unit": "each",
        "version": shown(item),
    }
    # What it is read as already: no rule.
    same = admin_client.post(f"{ITEMS}/{item.id}", data=form | {"unit:m": "m"})
    assert same.status_code == 303
    assert session.scalars(select(ReadingRule)).all() == []
    form["version"] = shown(item)
    done = admin_client.post(f"{ITEMS}/{item.id}", data=form | {"unit:m": "none"})
    assert done.status_code == 303
    rule = session.scalars(select(ReadingRule)).one()
    assert (rule.token, rule.reading) == ("m", None)
    assert other.unit_key == "spare25m"
    assert "25m" not in other.search_text.replace("25 m", "")
    page = text(admin_client.get(f"{ITEMS}/{item.id}"))
    assert "“25m” is not read as a unit." in page
    # As read: nothing changes.
    form["version"] = shown(item)
    assert admin_client.post(f"{ITEMS}/{item.id}", data=form | {"unit:m": ""})
    rule.reading, rule.respell = "m", True
    session.flush()
    form["version"] = shown(item)
    renamed = form | {"name": "Tape 25 m!", "unit:m": "none"}
    assert admin_client.post(f"{ITEMS}/{item.id}", data=renamed).status_code == 303
    assert (rule.reading, rule.respell) == (None, False)


def test_not_right_is_offered_where_words_decide_and_its_rule_wins(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Ali, 2026-11-15: every reading may be corrected, a spool's M too,
    which the word spool reads as meters; the correction, kept as the
    rule, wins over the words near it, in every name."""
    item = new_item(session, radios, "Spool 305M")
    other = new_item(session, radios, "Cable reel 100M")
    page = text(admin_client.get(f"{ITEMS}/{item.id}"))
    assert "Read as 305m · length, by “spool”." in page
    assert 'name="unit:M" value="Mbit/s"' in page
    form = {
        "name": "Spool 305M",
        "category_id": str(radios.id),
        "unit": "each",
        "version": shown(item),
        "unit:M": "Mbit/s",
    }
    assert admin_client.post(f"{ITEMS}/{item.id}", data=form).status_code == 303
    rule = session.scalars(select(ReadingRule)).one()
    assert (rule.token, rule.reading) == ("M", "Mbit/s")
    assert other.unit_key == "cablereel100mbits"
    page = text(admin_client.get(f"{ITEMS}/{item.id}"))
    assert "Read as 305Mbps · data rate." in page


def test_a_rename_the_forms_answer_reads_as_its_own_name_asks_nothing(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Answered as gigabits a second, Box 10g is Box 10Gbps respelt: the
    same name, so close names are not asked about, nor an alias kept."""
    item = new_item(session, radios, "Box 10Gbps")
    new_item(session, radios, "Box 10g spare")
    form = {
        "name": "Box 10g",
        "category_id": str(radios.id),
        "unit": "each",
        "version": shown(item),
        "unit:g": "Gbit/s",
    }
    assert admin_client.post(f"{ITEMS}/{item.id}", data=form).status_code == 303
    assert item.name == "Box 10g"
    assert items.aliases(session, item.id) == []


def test_a_name_taken_once_read_as_answered_names_the_item(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Refused, the answer undone, the taken name is still found as the
    form's answer reads it."""
    other = new_item(session, radios, "Box 10Gbps")
    item = new_item(session, radios, "Spare")
    # As shown (KeyedName.name_shown).
    said = TAKEN_FIELDS["name"]("Box 10\u00a0Gbps")
    form = {"category_id": str(radios.id), "unit": "each", "unit:g": "Gbit/s"}
    added = admin_client.post(ITEMS, data=form | {"name": "Box 10g"})
    assert added.status_code == 400
    assert escape(said) in added.text
    renamed = form | {"name": "Box 10g", "version": shown(item)}
    saved = admin_client.post(f"{ITEMS}/{item.id}", data=renamed)
    assert saved.status_code == 400
    assert escape(said) in saved.text
    alias = admin_client.post(
        f"{ITEMS}/{item.id}/aliases",
        data={"alias_name": "Box 10g", "unit:g": "Gbit/s"},
    )
    assert alias.status_code == 400
    assert escape(said) in alias.text
    assert session.scalars(select(ReadingRule)).all() == []
    assert other.name == "Box 10Gbps"


def test_a_correction_reading_names_alike_is_refused_naming_them(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """On the item's, a package's or an alias's form: nothing kept."""
    one = new_item(session, radios, "Box 10g")
    other = new_item(session, radios, "Box 10 Gbps")
    spare = new_item(session, radios, "Spare")
    said = read_as_one(
        both(NAMED["item"](one.id, "Box 10g"), NAMED["item"](other.id, "Box 10 Gbps"))
    )
    base = f"{ITEMS}/{spare.id}"
    for path, form, field in (
        (
            base,
            {
                "name": "Spare 10g",
                "category_id": str(radios.id),
                "unit": "each",
                "version": shown(spare),
            },
            "name",
        ),
        (f"{base}/packages", {"package_name": "Bag 10g", "size": "1"}, "package_name"),
        (f"{base}/aliases", {"alias_name": "Extra 10g"}, "alias_name"),
    ):
        refused = admin_client.post(path, data=form | {"unit:g": "Gbit/s"})
        assert refused.status_code == 400, path
        assert f'id="{field}-problem">{escape(said)}' in refused.text, path
    assert session.scalars(select(ReadingRule)).all() == []
    box = add(session, ItemPackage(item_id=spare.id, name="Box", size=Decimal(1)))
    renamed = {"package_name": "Box 10g", "size": "1", "package_version": shown(box)}
    refused = admin_client.post(
        f"{base}/packages/{box.id}", data=renamed | {"unit:g": "Gbit/s"}
    )
    assert escape(said) in refused.text


def test_a_read_alike_refusal_with_none_left_to_name_says_so(
    admin_client: TestClient,
    session: Session,
    radios: Category,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The names it read alike renamed meanwhile: no empty list of them."""
    new_item(session, radios, "Box 10g")
    new_item(session, radios, "Box 10 Gbps")
    spare = new_item(session, radios, "Spare")
    monkeypatch.setattr(items, "read_alike", lambda *_: [])
    form = {"alias_name": "Extra 10g", "unit:g": "Gbit/s"}
    refused = admin_client.post(f"{ITEMS}/{spare.id}/aliases", data=form)
    assert refused.status_code == 400
    assert str(READ_AS_ONE) in text(refused)


def test_an_alias_answer_refused_is_not_the_items_name_answer(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Sent back after an alias is refused, its answer is checked under the
    alias only: under the item's name it would be sent as a correction with
    the next save (inventory-tng-3x2 review)."""
    box = new_item(session, radios, "Box 10g")
    new_item(session, radios, "Box 10 Gbps")
    form = {"alias_name": "Extra 10g", "unit:g": "Gbit/s"}
    refused = admin_client.post(f"{ITEMS}/{box.id}/aliases", data=form)
    assert refused.status_code == 400
    assert refused.text.count('name="unit:g" value="Gbit/s" checked') == 1


def test_a_package_name_reads_units_too(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Added or renamed, as an item's name is: saved as typed, asked where
    nothing settles it."""
    item = new_item(session, radios, "Brass")
    path = f"{ITEMS}/{item.id}/packages"
    added = admin_client.post(path, data={"package_name": "Bag 5 MG", "size": "2"})
    assert added.status_code == 400
    assert 'name="unit:MG" value="mg"' in added.text
    assert 'name="unit:MG" value="Mg" checked' not in added.text
    chosen = {"package_name": "Bag 5 MG", "size": "2", "unit:MG": "Mg"}
    assert admin_client.post(path, data=chosen).status_code == 303
    package = named(session, ItemPackage, "Bag 5 MG")
    page = text(admin_client.get(f"{ITEMS}/{item.id}"))
    assert "Read as 5Mg · mass." in page
    renamed = {"package_name": "Spool 1000 FT", "size": "2"}
    resent = admin_client.post(
        f"{path}/{package.id}", data=renamed | {"package_version": shown(package)}
    )
    assert resent.status_code == 303
    assert package.name == "Spool 1000 FT"
    # MG after any number is settled now, by the answer kept as a rule.
    settled = {"package_name": "Bag 7 MG", "size": "2"}
    assert admin_client.post(path, data=settled).status_code == 303
    page = text(admin_client.get(f"{ITEMS}/{item.id}"))
    assert "Read as 7Mg · mass." in page


def aliased(session: Session, item: Item, *names: str) -> list[ItemAlias]:
    """`item` given each of `names` as an alias, as the item's form does."""
    return [items.add_alias(session, item, name) for name in names]


def current_aliases(session: Session, item: Item) -> list[str]:
    """The names of `item`'s current aliases, oldest first."""
    return [each.name for each in items.aliases(session, item.id)]


def rename(session: Session, item: Item, name: str) -> items.Renamed | None:
    """`item` renamed `name`, the rest of it as it is."""
    return items.save(session, item, item_fields(item, name=name), seen=None)


def test_an_alias_is_searched_and_its_removal_frees_it(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Stored canonical and keyed as names are; in the item's search text,
    each folded alone, as typed and respelt (models.held), until removed
    (retired), which frees the name."""
    item = new_item(session, radios, "Cat6 cable, outdoor")
    tough, capacitor = aliased(session, item, "ToughCable", "Cap 10 µF")
    assert (capacitor.name, capacitor.name_key) == ("Cap 10 μF", "cap10μf")
    assert item.search_text.split() == [
        "Cat6",
        "cable,",
        "outdoor",
        "ToughCable",
        "Cap",
        "10",
        "mF",
        "Cap",
        "10uF",
    ]
    found = items_named(text(admin_client.get(ITEMS, params={"q": "toughcable"})))
    assert found == ["Cat6 cable, outdoor"]
    items.remove_alias(session, tough, NOW)
    assert item.search_text.split()[3:] == ["Cap", "10", "mF", "Cap", "10uF"]
    assert current_aliases(session, item) == ["Cap 10 μF"]
    # Free again: another item may be named so, and this one given it again.
    new_item(session, radios, "Tough cable")
    assert attempt(session, lambda: items.add_alias(session, item, "toughcable")) == (
        None,
        items.ALIAS_IS_NAME,
    )
    actions = [entry.action for entry in history(session, "item_alias", tough.id)]
    assert actions == ["insert", "update"]


def test_names_and_aliases_are_one_namespace(
    session: Session, radios: Category
) -> None:
    """No item's name is another's alias (its own swaps them: below); no
    alias is an item's name, retired items' too, or another current alias,
    as names are compared (names.name_key)."""
    beam = new_item(session, radios, "LiteBeam 5AC Gen2")
    old = new_item(session, radios, "NanoBeam")
    retire(session, old)
    other = new_item(session, radios, "Rocket")
    aliased(session, beam, "LBE Gen2")

    def broken(change: Callable[[], object]) -> str | None:
        return attempt(session, change)[1]

    new = items.Fields.new(name="lbe-gen2", category_id=radios.id)
    assert broken(lambda: items.add(session, new)) == items.NAME_IS_ALIAS
    save = partial(rename, session, other, "LBE gen 2")
    assert broken(save) == items.NAME_IS_ALIAS
    for name, rule in (
        ("Nano beam", items.ALIAS_IS_NAME),  # A retired item's name.
        ("litebeam 5ac gen2", items.ALIAS_IS_NAME),  # Its own.
        ("lbe gen2", "unique"),  # Its own alias.
    ):
        assert broken(partial(items.add_alias, session, beam, name)) == rule, name
    # Another's alias.
    assert broken(partial(items.add_alias, session, other, "LBE-Gen2")) == "unique"


def test_a_retired_items_aliases_are_read_only(
    session: Session, radios: Category
) -> None:
    item = new_item(session, radios, "NanoBeam")
    (alias,) = aliased(session, item, "NBE")
    retire(session, item)
    for change in (
        lambda: items.add_alias(session, item, "Nano"),
        lambda: items.remove_alias(session, alias, NOW),
    ):
        assert attempt(session, change) == (None, "retired_read_only")


def test_the_items_form_adds_and_removes_aliases(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    from structlog.testing import capture_logs

    item = new_item(session, radios, "Cat6 cable, outdoor")
    path = f"{ITEMS}/{item.id}/aliases"
    with capture_logs() as logs:
        done = admin_client.post(path, data={"alias_name": "Tough  cable 305 m"})
    assert done.headers["location"] == (
        f"{ITEMS}/{item.id}?notice=alias_added&section=aliases#aliases"
    )
    # Saved as typed, as a name typed on its form is (units.spell).
    (alias,) = items.aliases(session, item.id)
    assert alias.name == "Tough  cable 305 m"
    (line,) = [line for line in logs if line["event"] == "alias added"]
    assert (line["item_id"], line["item_alias_id"]) == (item.id, alias.id)
    assert "Tough" not in str(line)
    page = text(admin_client.get(f"{ITEMS}/{item.id}"))
    assert "Tough  cable 305\u00a0m" in page
    refused = admin_client.post(path, data={"alias_name": "- ?"})
    assert refused.status_code == 400
    assert str(NO_LETTERS) in text(refused)
    with capture_logs() as logs:
        done = admin_client.post(f"{path}/{alias.id}/remove")
    assert done.headers["location"] == (
        f"{ITEMS}/{item.id}?notice=alias_removed&section=aliases#aliases"
    )
    assert items.aliases(session, item.id) == []
    (line,) = [line for line in logs if line["event"] == "alias removed"]
    assert (line["item_id"], line["item_alias_id"]) == (item.id, alias.id)
    # Removed already, or another item's: not found.
    assert admin_client.post(f"{path}/{alias.id}/remove").status_code == 404
    other = new_item(session, radios, "Rocket")
    (theirs,) = aliased(session, other, "RM5")
    assert admin_client.post(f"{path}/{theirs.id}/remove").status_code == 404


def test_a_clash_with_an_alias_names_the_item(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Beside the field, naming the item whose name or alias it is."""
    beam = new_item(session, radios, "LiteBeam 5AC Gen2")
    aliased(session, beam, "LBE Gen2")
    rocket = new_item(session, radios, "Rocket")
    beams = "LiteBeam 5AC Gen2"
    for target, form, field, said in (
        (
            f"{ITEMS}/{rocket.id}/aliases",
            {"alias_name": "lbe-gen2"},
            "alias_name",
            ALIAS_TAKEN["alias"](beams),
        ),
        (
            f"{ITEMS}/{rocket.id}/aliases",
            {"alias_name": "Lite Beam 5AC gen2"},
            "alias_name",
            ALIAS_TAKEN["name"](beams),
        ),
        (
            f"{ITEMS}/{beam.id}/aliases",
            {"alias_name": "LBE gen2"},
            "alias_name",
            ALIAS_ITS_OWN["alias"],
        ),
        (
            f"{ITEMS}/{beam.id}/aliases",
            {"alias_name": "litebeam5acgen2"},
            "alias_name",
            ALIAS_ITS_OWN["name"],
        ),
        (
            ITEMS,
            {"name": "LBE gen2", "category_id": str(radios.id), "unit": "each"},
            "name",
            TAKEN_FIELDS["alias"](beams),
        ),
        (
            f"{ITEMS}/{rocket.id}",
            {
                "name": "LBE gen2",
                "category_id": str(radios.id),
                "unit": "each",
                "version": shown(rocket),
            },
            "name",
            TAKEN_FIELDS["alias"](beams),
        ),
    ):
        refused = admin_client.post(target, data=form)
        assert refused.status_code == 400, target
        assert f'id="{field}-problem">{said}</span>' in refused.text, said


def test_an_alias_added_or_removed_refuses_no_save_of_the_item(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Its search text moves with its aliases, but no field its form shows:
    a form opened before still saves, and the item's history is the
    alias's."""
    item = new_item(session, radios, "NanoBeam")
    form = {
        "name": "NanoBeam M5",
        "category_id": str(radios.id),
        "unit": "each",
        "version": shown(item),
    }
    path = f"{ITEMS}/{item.id}/aliases"
    assert admin_client.post(path, data={"alias_name": "NBE"}).status_code == 303
    (alias,) = items.aliases(session, item.id)
    assert admin_client.post(f"{path}/{alias.id}/remove").status_code == 303
    assert admin_client.post(f"{ITEMS}/{item.id}", data=form).status_code == 303
    session.refresh(item)
    assert item.name == "NanoBeam M5"
    actions = [entry.action for entry in history(session, "item", item.id)]
    assert actions == ["insert", "update"]


def test_an_alias_clash_gone_meanwhile_is_said_in_general(
    admin_client: TestClient,
    session: Session,
    radios: Category,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The holder renamed, or its alias removed, between the refusal and
    the look for it."""
    new_item(session, radios, "LiteBeam")
    item = new_item(session, radios, "Rocket")
    monkeypatch.setattr(items, "alias_taken_by", lambda *_: None)
    path = f"{ITEMS}/{item.id}/aliases"
    refused = admin_client.post(path, data={"alias_name": "litebeam"})
    assert refused.status_code == 400
    assert str(TAKEN) in text(refused)
    assert items.aliases(session, item.id) == []


def test_a_retired_items_page_refuses_alias_changes(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Said above the page, as for its packages; an item not there is not
    found."""
    item = new_item(session, radios, "NanoBeam")
    (alias,) = aliased(session, item, "NBE")
    retire(session, item)
    path = f"{ITEMS}/{item.id}/aliases"
    for refused in (
        admin_client.post(path, data={"alias_name": "Nano"}),
        admin_client.post(f"{path}/{alias.id}/remove"),
    ):
        assert refused.status_code == 409
        assert "Bring it back first" in text(refused)
    assert current_aliases(session, item) == ["NBE"]
    missing = admin_client.post(f"{ITEMS}/0/aliases", data={"alias_name": "X"})
    assert missing.status_code == 404


def test_a_rename_keeps_the_old_name_as_an_alias(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """So a search for the old name still finds it (Ali, 2026-11-15), said
    by the aliases, where it can be removed; logged by ids. A respelling,
    the same name, keeps nothing."""
    from structlog.testing import capture_logs

    item = new_item(session, radios, "LiteBeam 5AC")
    form = {"category_id": str(radios.id), "unit": "each"}

    def save(name: str) -> Response:
        session.refresh(item)
        sent = form | {"name": name, "version": shown(item)}
        return admin_client.post(f"{ITEMS}/{item.id}", data=sent)

    with capture_logs() as logs:
        done = save("LiteBeam 5AC Gen1")
    assert done.headers["location"] == (
        f"{ITEMS}/{item.id}?notice=renamed&section=aliases#aliases"
    )
    (alias,) = items.aliases(session, item.id)
    assert alias.name == "LiteBeam 5AC"
    (line,) = [line for line in logs if line["event"] == "alias added"]
    assert line == {
        "event": "alias added",
        "log_level": "info",
        "item_id": item.id,
        "item_alias_id": alias.id,
        "by": "rename",
    }
    assert [entry.action for entry in history(session, "item_alias", alias.id)] == [
        "insert"
    ]
    page = text(admin_client.get(done.headers["location"]))
    assert str(RENAMED) in page
    session.refresh(item)
    assert item.name == "LiteBeam 5AC Gen1"
    found = items_named(text(admin_client.get(ITEMS, params={"q": "litebeam 5ac"})))
    assert found == ["LiteBeam 5AC Gen1"]
    # Respelt only: the same name, so no alias.
    assert save("litebeam 5ac gen1").headers["location"] == (
        f"{ITEMS}/{item.id}?notice=saved"
    )
    assert current_aliases(session, item) == ["LiteBeam 5AC"]
    # The administrator may remove it.
    path = f"{ITEMS}/{item.id}/aliases/{alias.id}/remove"
    assert admin_client.post(path).status_code == 303
    assert current_aliases(session, item) == []


def test_renaming_to_its_own_alias_swaps_them(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """The alias, as spelt now, becomes its name and its old name an alias,
    in one save; not asked about close names, as it was found by it
    already; both logged by ids."""
    from structlog.testing import capture_logs

    item = new_item(session, radios, "ToughCable")
    (pro,) = aliased(session, item, "Tough Cable Pro")
    # Close to the alias: would be asked about, for a new name.
    new_item(session, radios, "Tough Cable Pro 2")
    form = {
        "name": "tough cable PRO",
        "category_id": str(radios.id),
        "unit": "each",
        "version": shown(item),
    }
    with capture_logs() as logs:
        done = admin_client.post(f"{ITEMS}/{item.id}", data=form)
    assert done.status_code == 303
    session.refresh(item)
    session.refresh(pro)
    assert item.name == "tough cable PRO"
    assert pro.retired_at is not None
    (kept,) = items.aliases(session, item.id)
    assert kept.name == "ToughCable"
    said = {line["event"]: line for line in logs if "alias" in line["event"]}
    assert said["alias added"]["item_alias_id"] == kept.id
    assert said["alias removed"]["item_alias_id"] == pro.id
    assert {line["by"] for line in said.values()} == {"swap"}
    assert "Tough" not in str(said)
    # And back: a swap again.
    assert rename(session, item, "Tough-Cable") == items.Renamed(
        kept=items.aliases(session, item.id)[0], swapped=kept
    )
    assert item.name == "Tough-Cable"
    assert current_aliases(session, item) == ["tough cable PRO"]
    assert item.search_text.split() == ["Tough-Cable", "tough", "cable", "PRO"]


def test_a_rename_refused_keeps_its_aliases(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Swap and kept alias go with the rename: refused for another field,
    nothing of either is kept, and the field is named, not the alias."""
    item = new_item(session, radios, "ToughCable")
    (pro,) = aliased(session, item, "Tough Cable Pro")
    add(session, Item(name="Rocket", category_id=radios.id, old_code="NYCM-9"))
    for name in ("Tough Cable Pro", "Tough Cable Max"):
        form = {
            "name": name,
            "category_id": str(radios.id),
            "unit": "each",
            "old_code": "NYCM-9",
            "different": name,
            "version": shown(item),
        }
        refused = admin_client.post(f"{ITEMS}/{item.id}", data=form)
        assert refused.status_code == 400, name
        taken = TAKEN_FIELDS["old_code"]("Rocket")
        assert f'id="old_code-problem">{taken}' in refused.text
        session.refresh(item)
        session.refresh(pro)
        assert (item.name, pro.retired_at) == ("ToughCable", None)
        assert current_aliases(session, item) == ["Tough Cable Pro"]


def test_an_old_name_is_always_free_as_an_alias(
    session: Session, radios: Category
) -> None:
    """It was the item's name, so no other item's and no current alias's
    (one namespace): renamed back and forth, past other items' removed
    aliases and retired items, each rename keeps it, and the namespace
    holds."""
    item = new_item(session, radios, "NanoBeam")
    other = new_item(session, radios, "Rocket")
    # Another item's alias once, removed: free again.
    (gone,) = aliased(session, other, "NanoBeam M5")
    items.remove_alias(session, gone, NOW)
    retire(session, new_item(session, radios, "Old NanoBeam"))
    for name in ("NanoBeam M5", "NanoBeam", "NanoBeam M5", "NBE", "NanoBeam"):
        done, broken = attempt(session, partial(rename, session, item, name))
        assert broken is None, name
        assert done is not None
        assert done.kept.item_id == item.id
    assert current_aliases(session, item) == ["NanoBeam M5", "NBE"]
    names = set(session.scalars(select(Item.name_key)))
    keys = [
        alias.name_key
        for alias in session.scalars(select(ItemAlias).where(CURRENT_ALIAS))
    ]
    assert len(keys) == len(set(keys))
    assert not names & set(keys)


def test_close_names_include_close_aliases(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Saying which alias is close; each item once, by its name where that
    is as close; an item's own aliases and removed ones not."""
    tough = new_item(session, radios, "ToughCable")
    aliased(session, tough, "Tuff cable outdoor")
    beam = new_item(session, radios, "LiteBeam 5AC")
    aliased(session, beam, "LiteBeam 5AC Gen1")
    (gone,) = aliased(session, new_item(session, radios, "Rocket"), "Patch lead")
    items.remove_alias(session, gone, NOW)
    assert items.similar(session, "Tuff cable outdoors", 0) == [
        items.Close(tough, "Tuff cable outdoor")
    ]
    assert items.similar(session, "LiteBeam 5AC", 0) == [items.Close(beam)]
    assert items.similar(session, "Tuff cable outdoors", tough.id) == []
    assert items.similar(session, "Patch leads", 0) == []
    form = {
        "name": "Tuff cable outdoors",
        "category_id": str(radios.id),
        "unit": "each",
    }
    asked = text(admin_client.post(ITEMS, data=form))
    assert (
        f'<a href="{ITEMS}/{tough.id}">ToughCable</a> (alias Tuff cable outdoor).'
        in asked
    )
