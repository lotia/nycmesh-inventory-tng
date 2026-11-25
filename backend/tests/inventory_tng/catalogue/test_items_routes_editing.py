"""Items on the administrators' pages (catalogue/items_routes.py): adding
and editing one, its name and the names it clashes with, close names
asked about first, and retiring and bringing one back."""

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.catalogue import items
from inventory_tng.catalogue.forms import (
    IS_RETIRED,
    NO_LETTERS,
    PICK_A_CATEGORY,
    PICK_A_UNIT,
    TAKEN,
    TAKEN_FIELDS,
)
from inventory_tng.catalogue.paths import ITEMS
from inventory_tng.core.local_time import DEFAULT_ZONE
from inventory_tng.labels import labels
from inventory_tng.labels.paths import item_labels
from inventory_tng.models.catalogue import (
    Category,
    Item,
    ItemPackage,
    VendorOffer,
    reading_rules_held,
)
from inventory_tng.models.places import Location
from inventory_tng.models.stock import LedgerTransaction, Movement
from inventory_tng.web.forms import NAME_MAX, RULES, name_up_to

from helpers.items import NOW, item_form, new_item, shown_item_form
from helpers.markup import (
    box_values,
    hidden_fields,
    links,
    problem,
    read,
    text,
    words_on,
)
from helpers.query_counts import counting
from helpers.rows import add, breaks, history, item_fields, named, shown
from helpers.stock import Place, received


def test_adding_an_item(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    done = admin_client.post(
        ITEMS,
        data=item_form(
            radios, name="LiteBeam 5AC", manufacturer="Ubiquiti", part_number="LBE-5AC"
        ),
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
    form = item_form(radios)
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
    form = item_form(radios)
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
    refused = admin_client.post(ITEMS, data=item_form(radios, name=""))
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
        refused = admin_client.post(ITEMS, data=item_form(radios) | form)
        assert refused.status_code == 400
        assert problem(refused.text, field) == words


def test_a_name_pasted_with_look_alikes_clashes_naming_the_other(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """A non-breaking space, a curly quote or an en dash is read as the
    plain one, so the name clashes with the one typed plainly, which the
    form names; a name is kept as typed."""
    form = item_form(radios)
    for kept, pasted in (
        ("Tough cable", "Tough\u00a0Cable"),
        ("Ubiquiti's dish", "Ubiquiti\u2019s dish"),
        ("Patch\u2014cord", "Patch-cord"),
    ):
        assert admin_client.post(ITEMS, data=form | {"name": kept}).status_code == 303
        refused = admin_client.post(ITEMS, data=form | {"name": pasted})
        assert refused.status_code == 400
        assert problem(refused.text, "name") == TAKEN_FIELDS["name"](kept)
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
    form = shown_item_form(item, name="Lite Beam")
    path = f"{ITEMS}/{item.id}"
    refused = admin_client.post(path, data=form | {"old_code": "NYCM-1"})
    assert refused.status_code == 400
    taken = TAKEN_FIELDS["old_code"](other.name)
    assert problem(refused.text, "old_code") == f"{taken} {IS_RETIRED}"
    assert admin_client.post(path, data=form).status_code == 303
    session.refresh(item)
    assert item.name == "Lite Beam"


def test_a_name_is_checked_and_saved_in_canonical_spelling(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Typed with the micro sign, a name is compared and stored with μ
    (docs/decisions/0021, 2026-11-14): another's in that spelling is taken,
    naming it, and its own respelt so is no change asked about."""
    form = item_form(radios)
    assert (
        admin_client.post(ITEMS, data=form | {"name": "Cap 10\u00b5F"}).status_code
        == 303
    )
    item = named(session, Item, "Cap 10\u03bcF")
    refused = admin_client.post(ITEMS, data=form | {"name": "cap 10\u00b5f"})
    assert refused.status_code == 400
    assert TAKEN_FIELDS["name"]("Cap 10\u00a0\u03bcF") in refused.text
    path = f"{ITEMS}/{item.id}"
    edit = form | {"version": shown(item)}
    saved = admin_client.post(path, data=edit | {"name": "Cap 10\u00b5F"})
    assert saved.status_code == 303
    # Close names, asked about, then sent again as the form sends them.
    add(session, Item(name="Cap 10\u03bcF 50V", category_id=radios.id))
    asked = admin_client.post(ITEMS, data=form | {"name": "Cap 10\u00b5F 5OV"})
    assert "A different item?" in text(asked)
    again = {"name": "Cap 10\u00b5F 5OV", "different": "Cap 10\u00b5F 5OV"}
    assert admin_client.post(ITEMS, data=form | again).status_code == 303
    named(session, Item, "Cap 10\u03bcF 5OV")


@pytest.mark.parametrize(
    ("asked", "legend", "refused"),
    [
        # Ali, 2026-11-16 (inventory-tng-t78, inventory-tng-uxo6).
        ("Box 1 000 M", "1 000M", "Cap 1 000 \u03bcF"),
        # inventory-tng-vw0f.
        ("Box 1000\u00a0M", "1000M", "Cap 1000\u00a0\u03bcF"),
    ],
)
def test_a_number_and_its_unit_are_judged_as_search_reads_them(
    admin_client: TestClient,
    session: Session,
    radios: Category,
    asked: str,
    legend: str,
    refused: str,
) -> None:
    """The form reads a number grouped by spaces before a unit as one
    number, and finds a unit after a number and a no-break space as after
    a plain one, as search does: so asks about `M` after it, and refuses a
    name reading alike with another's, naming it."""
    form = item_form(radios)
    asking = admin_client.post(ITEMS, data=form | {"name": asked})
    assert asking.status_code == 400
    question = f"\u201c{legend}\u201d is new. Is it a unit?"
    assert read(asking.text).all("legend", question)
    add(session, Item(name="Cap 1000 uF", category_id=radios.id))
    refusing = admin_client.post(ITEMS, data=form | {"name": refused})
    assert refusing.status_code == 400
    assert TAKEN_FIELDS["name"]("Cap 1000\u00a0uF") in refusing.text


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
    form = item_form(radios, name="litebeam")
    refused = admin_client.post(ITEMS, data=form)
    assert str(TAKEN) in text(refused)


def test_editing_every_field(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios)
    cables = add(session, Category(name="Cables"))
    form = item_form(
        cables,
        name="Cat6 cable",
        unit="m",
        manufacturer="",
        part_number="",
        old_code="NYCM-0042",
        minimum="305",
        notes="Outdoor",
        ordering_notes="Buy by the reel",
        version=shown(item),
    )
    done = admin_client.post(f"{ITEMS}/{item.id}", data=form)
    # Renamed: its old name kept as an alias.
    assert done.headers["location"] == (
        f"{ITEMS}/{item.id}?notice=item_renamed&section=aliases#aliases"
    )
    assert (item.name, item.category_id, item.unit) == ("Cat6 cable", cables.id, "m")
    assert (item.old_code, item.minimum, item.manufacturer) == (
        "NYCM-0042",
        Decimal(305),
        None,
    )
    page = admin_client.get(f"{ITEMS}/{item.id}").text
    assert box_values(page, "minimum") == ["305"]
    # A quantity, perhaps with a decimal point; its neighbours take words.
    boxes = read(page)
    assert boxes.one("input", name="minimum", inputmode="decimal")
    assert boxes.one("input", name="part_number", inputmode="text")


def _moved(session: Session, item: Item) -> None:
    """One of the item received into a store."""
    received(
        session,
        Place(session),
        [{"item_id": item.id, "quantity": "1", "unit": item.unit}],
    )


def test_a_package_takes_its_items_unit_and_follows_a_change_to_it(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """A package records its size's unit, its item's as it is added; a
    switch while the unit may change records the item's packages, and its
    vendor offers, in the new one, their amounts as typed
    (inventory-tng-2rlr)."""
    item = new_item(session, radios, unit="m")
    reel = add(session, ItemPackage(item_id=item.id, name="Reel", size=Decimal(305)))
    offer = add(session, VendorOffer(item_id=item.id, url="https://example.org"))
    assert (reel.unit, offer.unit) == ("m", "m")
    saved = admin_client.post(
        f"{ITEMS}/{item.id}", data=shown_item_form(item, unit="ft")
    )
    assert saved.status_code == 303
    for row in (reel, offer):
        session.refresh(row)
    assert (reel.unit, reel.size, offer.unit) == ("ft", Decimal(305), "ft")
    page = admin_client.get(f"{ITEMS}/{item.id}").text
    assert "305 ft" in words_on(page)


def test_the_unit_is_fixed_once_stock_has_moved(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios)
    _moved(session, item)
    page = admin_client.get(f"{ITEMS}/{item.id}").text
    assert "each: fixed, as stock has moved in it" in words_on(page)
    assert "Revoke its labels" not in words_on(page)
    assert ("unit", "each") in hidden_fields(page)
    form = shown_item_form(item, unit="m")
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


def test_the_unit_is_fixed_once_a_spools_label_is_printed(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Cat6 cable, Spool 305 m of 305 m, labelled, nothing moved yet: a
    switch to ft would make the next scan of that label receive 305 ft. So
    the unit is fixed once any label of the item or its packages is
    printed, and nothing is converted (Ali, 2026-11-23, inventory-tng-rpud)."""
    cable = new_item(session, radios, "Cat6 cable", unit="m")
    spool = add(session, ItemPackage(item_id=cable.id, name="Spool", size=Decimal(305)))
    page = admin_client.get(f"{ITEMS}/{cable.id}").text
    assert ("unit", "m") not in hidden_fields(page)
    label = labels.make_for_item(session, cable.id, spool.id)
    assert label is not None
    page = admin_client.get(f"{ITEMS}/{cable.id}").text
    assert (
        "m: fixed, as a label has been printed for it. Revoke its labels to change it."
    ) in words_on(page)
    # To the labels list, filtered to this item's, each offered to revoke:
    # beside the unit, and the spool's size, labelled too.
    revoke = read(page).all("a", "Revoke its labels")
    assert [a.attrs["href"] for a in revoke] == [item_labels(cable.id)] * 2
    assert ("unit", "m") in hidden_fields(page)
    form = shown_item_form(cable, unit="ft")
    refused = admin_client.post(f"{ITEMS}/{cable.id}", data=form)
    assert refused.status_code == 400
    assert str(RULES["item_unit_labelled"]) in text(refused)
    assert (cable.unit, spool.size) == ("m", 305)
    kept = admin_client.post(f"{ITEMS}/{cable.id}", data=form | {"unit": "m"})
    assert kept.status_code == 303
    # Its labels revoked, a mistaken unit is corrected (Ali, 2026-11-23,
    # inventory-tng-37dj).
    assert labels.revoke(session, label.id, NOW)
    page = admin_client.get(f"{ITEMS}/{cable.id}").text
    assert ("unit", "m") not in hidden_fields(page)
    form = shown_item_form(cable, unit="ft")
    assert admin_client.post(f"{ITEMS}/{cable.id}", data=form).status_code == 303
    assert cable.unit == "ft"


@pytest.mark.parametrize("package", [False, True])
def test_a_label_fixes_the_unit_until_revoked_a_movement_for_good(
    session: Session, radios: Category, package: bool
) -> None:
    """The item's own label, counting one unit a scan, or one of a
    package's, each alone, fixes the unit; revoked, it scans as no label,
    so the unit may change. Once stock has moved it may not, labels
    revoked or not (Ali, 2026-11-23, inventory-tng-37dj)."""
    item = new_item(session, radios, "Patch cord", unit="m")
    reel = add(session, ItemPackage(item_id=item.id, name="Reel", size=10))
    label = labels.make_for_item(session, item.id, reel.id if package else None)
    assert label is not None
    assert items.unit_fixed(session, item.id) == "labelled"
    with breaks(session, "item_unit_labelled"):
        items.save(session, item, item_fields(item, unit="ft"), seen=None)
    assert labels.revoke(session, label.id, NOW)
    assert items.unit_fixed(session, item.id) is None
    items.save(session, item, item_fields(item, unit="ft"), seen=None)
    assert item.unit == "ft"
    _moved(session, item)
    assert items.unit_fixed(session, item.id) == "moved"
    with breaks(session, "item_unit_fixed"):
        items.save(session, item, item_fields(item, unit="m"), seen=None)


def test_a_retired_labelled_items_unit_offers_no_labels_to_revoke(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Read only, its labels inactive: nothing there to revoke."""
    item = new_item(session, radios, "Patch cord", unit="m")
    assert labels.make_for_item(session, item.id) is not None
    admin_client.post(f"{ITEMS}/{item.id}/retire")
    assert item.retired_at is not None
    words = words_on(admin_client.get(f"{ITEMS}/{item.id}").text)
    assert "m: fixed, as a label has been printed for it" in words
    assert "Revoke its labels" not in words


def test_retiring_and_bringing_back(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios)
    admin_client.post(f"{ITEMS}/{item.id}/retire")
    assert item.retired_at is not None
    assert "Retired" in text(admin_client.get(ITEMS, params={"show": "retired"}))
    # Its name stays its own while it is retired, so it can come back.
    taken = admin_client.post(ITEMS, data=item_form(radios, name=item.name))
    assert taken.status_code == 400
    assert f"{TAKEN_FIELDS['name'](item.name)} {IS_RETIRED}" in text(taken)
    assert admin_client.post(f"{ITEMS}/{item.id}/restore").status_code == 303
    session.refresh(item)
    assert item.retired_at is None


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
    add(
        session,
        Movement(transaction_id=batch.id, item_id=item.id, quantity=3, unit=item.unit),
    )
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


def test_an_edit_with_a_problem_changes_nothing(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    item = new_item(session, radios)
    refused = admin_client.post(
        f"{ITEMS}/{item.id}",
        data=item_form(radios, name=""),
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
            data=item_form(radios, name="Radio X"),
        )
    (line,) = [line for line in logs if line["event"] == "item added"]
    assert line["item_id"] == named(session, Item, "Radio X").id
    assert "Radio X" not in str(line)


def test_taken_by_skips_the_item_itself(session: Session, radios: Category) -> None:
    item = add(
        session,
        Item(name="LiteBeam", category_id=radios.id, old_code="NYCM-1"),
    )
    asked = items.Fields.new(name="Lite beam", category_id=radios.id, old_code="NYCM-1")
    # Its own name and old code are no clash with itself.
    assert items.taken_by(session, asked, item.id) is None
    assert items.taken_by(session, asked) == ("name", item)


def test_taken_by_looks_only_for_the_refusing_index_field(
    session: Session, radios: Category
) -> None:
    """Named by the violated index, only its field's holder is looked for,
    in one statement; an index it does not know looks for each field."""
    named_alike = new_item(session, radios, "LiteBeam")
    coded = add(session, Item(name="Omni", category_id=radios.id, old_code="NYCM-1"))
    asked = items.Fields.new(name="Lite beam", category_id=radios.id, old_code="NYCM-1")
    rules = reading_rules_held(session)
    with counting(session) as sent:
        found = items.taken_by(session, asked, None, rules, "uq_item_old_code")
    assert found == ("old_code", coded)
    assert len(sent) == 1
    assert items.taken_by(session, asked, None, rules, "uq_other") == (
        "name",
        named_alike,
    )
    # The holder gone meanwhile: none, though another field is taken.
    gone = items.Fields.new(name="Lite beam", category_id=radios.id, old_code="NYCM-2")
    assert items.taken_by(session, gone, None, rules, "uq_item_old_code") is None


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
    form = item_form(radios, name="LiteBeam 5AC Gen2")
    asked = admin_client.post(ITEMS, data=form)
    assert asked.status_code == 200
    warning = read(asked.text).one("p", class_="warning", role="status")
    said = warning.text
    assert ("LiteBeam 5AC Gen1", f"{ITEMS}/{gen1.id}") in links(warning)
    assert ("LiteBeam 5AC Gen0", f"{ITEMS}/{old.id}") in links(warning)
    assert "LiteBeam 5AC Gen0 Retired" in said
    assert warning.one("span", class_="tag").text.startswith("Retired")
    assert "Patch cable" not in said
    assert "A different item? Add it again." in said
    assert "LiteBeam 5AC Gen2" in box_values(asked.text, "name")
    assert session.scalar(select(Item).where(Item.name == form["name"])) is None
    assert form["name"] in box_values(asked.text, "different")
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
    form = item_form(radios, name="Mast, telescopic")
    asked = admin_client.post(ITEMS, data=form).text
    assert ("mast", f"{ITEMS}/{mast.id}") in links(asked)


def test_other_refusals_come_before_the_question(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """A retired category is said first, rather than asking, then refusing."""
    new_item(session, radios, "LiteBeam 5AC Gen1")
    radios.retired_at = NOW
    session.flush()
    form = item_form(radios, name="LiteBeam 5AC Gen2")
    refused = admin_client.post(ITEMS, data=form)
    assert refused.status_code == 400
    assert "A different item?" not in text(refused)


def test_renaming_close_to_another_asks_first(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Only a new name asks; saving the other fields does not."""
    new_item(session, radios, "LiteBeam 5AC Gen1")
    item = new_item(session, radios, "Radio")
    form = shown_item_form(item, notes="On the shelf")
    path = f"{ITEMS}/{item.id}"
    assert admin_client.post(path, data=form).status_code == 303
    form["version"] = shown(item)
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
    form |= {"name": "Litebeam-5AC-Gen2", "version": shown(item)}
    assert admin_client.post(path, data=form).status_code == 303


@pytest.mark.usefixtures("radios")
def test_the_add_form_says_how_to_name_an_item(admin_client: TestClient) -> None:
    page = words_on(admin_client.get(ITEMS).text)
    assert "what it is, then what tells it apart from the items nearest it" in page
    assert "the maker goes in Manufacturer" in page


def test_a_name_asked_about_saves_however_it_was_spaced(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Sent again with spaces around it, as typed, it is the name asked
    about, so it saves rather than asking again."""
    new_item(session, radios, "LiteBeam 5AC Gen1")
    form = item_form(radios, name="LiteBeam 5AC Gen2 ")
    asked = admin_client.post(ITEMS, data=form)
    assert "LiteBeam 5AC Gen2" in box_values(asked.text, "different")
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
    form = item_form(
        radios,
        name="LiteBeam 5AC Gen2",
        manufacturer="U",
        part_number="P",
        different="LiteBeam 5AC Gen2",
    )
    refused = admin_client.post(ITEMS, data=form)
    assert refused.status_code == 400
    assert "LiteBeam 5AC Gen2" in box_values(refused.text, "different")
    assert "A different item?" not in text(refused)
    added = admin_client.post(ITEMS, data=form | {"part_number": "Q"})
    assert added.status_code == 303
