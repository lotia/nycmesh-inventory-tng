"""Searches ignore accents (inventory-tng-8i4.135, docs/decisions/0021):
what is searched is kept folded to ASCII by the app on every save, and what
is typed is folded alike, so "cafe" finds "Café", and the reverse."""

import importlib.metadata
import unicodedata

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from inventory_tng import items, models
from inventory_tng.admin.common import LABELS
from inventory_tng.admin.items import ITEMS, SEARCHED
from inventory_tng.admin.listing import SEARCH_MAX, folded
from inventory_tng.admin.volunteers import VOLUNTEERS
from inventory_tng.models import (
    FOLDED_WITH,
    Category,
    Item,
    ItemPackage,
    Label,
    Location,
    Volunteer,
    fold,
)

from helpers.rows import add, tag
from helpers.sign_in import text as page_text


def test_fold_spells_in_ascii_what_decomposing_would_miss() -> None:
    assert fold("Câble Ærø Łódź Straße ﬁber") == "Cable Aero Lodz Strasse fiber"


def test_what_is_typed_is_folded_short_and_nothing_is_no_search() -> None:
    assert len(folded("\N{GRINNING FACE}" * SEARCH_MAX)) == SEARCH_MAX
    # A lone accent folds to nothing: not a search matching everything.
    assert SEARCHED.read(q="\N{COMBINING ACUTE ACCENT}").search == ""
    assert SEARCHED.read(q="Café").search == "Café"


def test_what_is_searched_is_folded_on_every_write(session: Session) -> None:
    cables = add(session, Category(name=f"Câbles {tag()}"))
    item = add(
        session,
        Item(name="Câble", category_id=cables.id, manufacturer="Ubiquiti"),
    )
    assert (item.search_text, item.name_folded) == ("Cable Ubiquiti  ", "Cable")
    item.part_number = "Ø-5"
    item.old_code = "NYCM-É"
    item.manufacturer = None
    item.name = "Cåble"
    session.flush()
    session.refresh(item)
    assert (item.search_text, item.name_folded) == ("Cable  O-5 NYCM-E", "Cable")
    place = add(session, Location(name="Łódź", kind="store"))
    person = add(session, Volunteer(name="Zoë"))
    package = add(session, ItemPackage(item_id=item.id, name="Bobine", size=1))
    package.name = "Bobïne"
    session.flush()
    session.refresh(package)
    assert (place.search_text, person.search_text, package.search_text) == (
        "Lodz",
        "Zoe",
        "Bobine",
    )
    # And what folded each, as installed.
    assert {row.folded_with for row in (item, place, person, package)} == {FOLDED_WITH}
    assert importlib.metadata.version("anyascii") in FOLDED_WITH
    assert unicodedata.unidata_version in FOLDED_WITH


def test_a_change_to_nothing_searched_folds_nothing(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cables = add(session, Category(name=f"Cables {tag()}"))
    item = add(session, Item(name="Câble", category_id=cables.id))
    session.expire(item)
    folds: list[str] = []
    monkeypatch.setattr(models, "fold", lambda text: folds.append(text) or text)
    item.notes = "By the door"
    session.flush()
    assert folds == []
    item.name = "Câble plat"
    session.flush()
    assert folds


def test_every_list_finds_accented_names_typed_without_and_the_reverse(
    admin_client: TestClient, session: Session
) -> None:
    cables = add(session, Category(name=f"Cables {tag()}"))
    add(session, Item(name="Câble plat", category_id=cables.id))
    add(session, Item(name="Cafe kettle", category_id=cables.id))
    assert "Câble plat" in page_text(admin_client.get(ITEMS, params={"q": "cable"}))
    assert "Cafe kettle" in page_text(admin_client.get(ITEMS, params={"q": "café"}))
    add(session, Volunteer(name="Zoë Ångström"))
    found = admin_client.get(f"{VOLUNTEERS}/suggest", params={"q": "zoe angstrom"})
    assert [each["name"] for each in found.json()] == ["Zoë Ångström"]
    place = add(session, Location(name=f"Łódź {tag()}", kind="store"))
    label = add(session, Label(location_id=place.id))
    listed = page_text(admin_client.get(LABELS, params={"q": "lodz"}))
    assert label.code in listed


def test_compatibility_characters_find_their_canonical_ones(
    admin_client: TestClient, session: Session
) -> None:
    # The micro, Ohm and degree Celsius signs, as some keyboards give them,
    # and the Greek letters and the degree sign, as others do (gfe).
    cables = add(session, Category(name=f"Parts {tag()}"))
    for sign, canonical in (
        ("10\N{MICRO SIGN}F", "10\N{GREEK SMALL LETTER MU}F"),
        ("5\N{OHM SIGN} load", "5\N{GREEK CAPITAL LETTER OMEGA} load"),
        ("85\N{DEGREE CELSIUS} rated", "85\N{DEGREE SIGN}C rated"),
    ):
        first = add(session, Item(name=f"{sign} {tag()}", category_id=cables.id))
        second = add(session, Item(name=f"{canonical} {tag()}", category_id=cables.id))
        for typed in (sign, canonical):
            listed = page_text(admin_client.get(ITEMS, params={"q": typed}))
            assert first.name in listed, typed
            assert second.name in listed, typed


def test_close_names_are_found_whatever_their_accents(session: Session) -> None:
    cables = add(session, Category(name=f"Cables {tag()}"))
    accented = add(session, Item(name="Câble plat", category_id=cables.id))
    assert accented in items.similar(session, "Cable plat", 0)
    plain = add(session, Item(name="Patch lead", category_id=cables.id))
    assert plain in items.similar(session, "Pätch lead", 0)


def test_rows_there_are_are_folded_by_the_migration(alembic_config: Config) -> None:
    command.downgrade(alembic_config, "0031")
    connection = alembic_config.attributes["connection"]
    category_id = connection.scalar(
        text("INSERT INTO category (name) VALUES ('Câbles') RETURNING id")
    )
    item_id = connection.scalar(
        text(
            "INSERT INTO item (name, category_id, manufacturer)"
            " VALUES ('Câble', :c, 'Ærø µ') RETURNING id"
        ),
        {"c": category_id},
    )
    connection.execute(
        text("INSERT INTO item_package (item_id, name, size) VALUES (:i, 'Bobïne', 1)"),
        {"i": item_id},
    )
    connection.execute(text("INSERT INTO volunteer (name) VALUES ('Zoë')"))
    connection.execute(
        text("INSERT INTO location (name, kind) VALUES ('Łódź', 'store')")
    )
    command.upgrade(alembic_config, "head")
    assert connection.execute(
        text("SELECT search_text, name_folded FROM item WHERE id = :i"), {"i": item_id}
    ).one() == ("Cable Aero m  ", "Cable")
    for table, spelt in (
        ("item_package", "Bobine"),
        ("volunteer", "Zoe"),
        ("location", "Lodz"),
    ):
        assert (
            spelt in connection.scalars(text(f"SELECT search_text FROM {table}")).all()
        ), table
    # 0033 records what folded them, every row.
    for table in ("item", "item_package", "volunteer", "location"):
        assert set(
            connection.scalars(text(f"SELECT DISTINCT folded_with FROM {table}"))
        ) == {FOLDED_WITH}, table
