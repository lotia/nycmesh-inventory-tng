"""How the units in an item's or a package's name are read
(catalogue/items_routes.py, inventory-tng-c0c.3): said under the name,
asked about where nothing settles them, and corrected with Not right?."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from markupsafe import escape
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng.catalogue import items
from inventory_tng.catalogue.forms import (
    ASK_UNIT,
    READ_AS_ONE,
    TAKEN_FIELDS,
    read_as_one,
)
from inventory_tng.catalogue.items_routes import ITEMS
from inventory_tng.catalogue.reading_rules import NAMED, both
from inventory_tng.models import Category, Item, ItemPackage, ReadingRule

from helpers.items import new_item
from helpers.markup import read, text
from helpers.rows import add, named, shown


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
    response = admin_client.get(f"{ITEMS}/{item.id}")
    page = text(response)
    assert "Read as 305m · length.\n  Saved as typed." in page
    assert "Read as 10\u03bcF · capacitance." in page
    assert read(response.text).all("summary", "Not right?")
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
    assert read(asked.text).all("legend", "“10g” is new. Is it a unit?")
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
