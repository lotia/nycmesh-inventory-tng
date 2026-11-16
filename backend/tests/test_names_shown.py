"""Names are kept as typed and shown with each number and unit spaced
(models.KeyedName.name_shown, units.shown_name; Ali, 2026-11-15,
inventory-tng-ist.3). Every page showing them so is
test_query_counts.test_every_page_names_items_and_packages_as_shown."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import items, reading_rules, units
from inventory_tng.admin.items import ITEMS, TAKEN_FIELDS
from inventory_tng.models import Category, Item, ItemPackage
from inventory_tng.reading_rules import NAMED, both

from helpers.rows import add, named, tag
from helpers.rows import shown as version
from helpers.sign_in import text

NB, THIN = "\u00a0", "\u202f"


@pytest.fixture
def cables(session: Session) -> Category:
    return add(session, Category(name=f"Cables {tag()}"))


def test_items_packages_and_aliases_keep_the_name_typed_beside_it_shown(
    session: Session, cables: Category
) -> None:
    mark = tag()
    box = add(session, Item(name=f"Box {mark} 10,000ft", category_id=cables.id))
    reel = add(session, ItemPackage(item_id=box.id, name="Reel 1,000 ft", size=1))
    alias = items.add_alias(session, box, f"Cap {mark} 10uF 50V")
    session.flush()
    assert (box.name, box.name_shown) == (
        f"Box {mark} 10,000ft",
        f"Box {mark} 10{THIN}000{NB}ft",
    )
    assert (reel.name, reel.name_shown) == ("Reel 1,000 ft", f"Reel 1000{NB}ft")
    assert alias.name_shown == f"Cap {mark} 10{NB}uF 50{NB}V"
    # Renamed, shown anew.
    box.name = f"Cable {mark} 100ft 48 VDC"
    session.flush()
    assert box.name_shown == f"Cable {mark} 100{NB}ft 48{NB}VDC"


def test_a_rule_changed_shows_every_name_with_its_unit_anew(
    session: Session, cables: Category
) -> None:
    """Items', packages' and aliases' alike (items.reread), as the app may
    update each."""
    mark = tag()
    tape = add(session, Item(name=f"Tape {mark} 3M", category_id=cables.id))
    roll = add(session, ItemPackage(item_id=tape.id, name="Pad 2M", size=1))
    alias = items.add_alias(session, tape, f"Sticky {mark} 4M")
    session.flush()
    # `M` after a number, with no word near it, is asked: as typed.
    assert (tape.name_shown, roll.name_shown, alias.name_shown) == (
        tape.name,
        roll.name,
        alias.name,
    )
    (rule,) = reading_rules.decide(session, {"M": units.Rule("m")}, None)
    session.flush()
    assert (tape.name_shown, roll.name_shown, alias.name_shown) == (
        f"Tape {mark} 3{NB}M",
        f"Pad 2{NB}M",
        f"Sticky {mark} 4{NB}M",
    )
    reading_rules.delete(session, rule, seen=None)
    session.flush()
    assert tape.name_shown == tape.name


def test_the_form_edits_the_name_as_typed_and_says_how_it_is_shown(
    admin_client: TestClient, session: Session, cables: Category
) -> None:
    mark = tag()
    typed = f"Box {mark} 10,000 ft"
    shown = f"Box {mark} 10{THIN}000{NB}ft"
    form = {"name": typed, "category_id": str(cables.id), "unit": "each"}
    assert admin_client.post(ITEMS, data=form).status_code == 303
    item = named(session, Item, typed)
    assert item.name_shown == shown
    page = text(admin_client.get(f"{ITEMS}/{item.id}"))
    assert f"<h1>{shown}</h1>" in page
    assert f'<input name="name" value="{typed}"' in page
    assert f"Shown as {shown}." in page
    # A package and an alias, added on its page, alike.
    path = f"{ITEMS}/{item.id}"
    admin_client.post(
        f"{path}/packages", data={"package_name": "Reel 1,000ft", "size": "1"}
    )
    admin_client.post(f"{path}/aliases", data={"alias_name": f"Spool {mark} 305M"})
    page = text(admin_client.get(path))
    assert f"<td>Spool {mark} 305{NB}M</td>" in page
    assert f"Reel 1000{NB}ft" in page
    assert 'value="Reel 1,000ft"' in page
    assert f"Shown as Reel 1000{NB}ft." in page
    # The list, and its suggestions.
    assert shown in text(admin_client.get(ITEMS))
    suggested = admin_client.get(f"{ITEMS}/suggest", params={"q": f"Box {mark}"})
    assert [each["name"] for each in suggested.json()] == [shown]


def test_a_form_sent_back_shows_the_name_as_the_unit_answered_reads_it(
    admin_client: TestClient, session: Session, cables: Category
) -> None:
    """Not saved, as its name is close to another's, the form says how the
    name will be shown as the unit answered in it reads it, as saving would
    (inventory-tng-nbo). A package's and an alias's forms alike."""
    mark = tag()
    spare = add(session, Item(name=f"Box {mark} 10g spare", category_id=cables.id))
    session.flush()
    form = {"name": f"Box {mark} 10g", "category_id": str(cables.id), "unit": "each"}
    asked = text(admin_client.post(ITEMS, data=form))
    assert "Shown as" not in asked
    close = text(admin_client.post(ITEMS, data=form | {"unit:g": "Gbit/s"}))
    assert "A different item?" in close
    assert f"Shown as Box {mark} 10{NB}g." in close
    # Answered as not a unit: as typed, so nothing more.
    kept = text(admin_client.post(ITEMS, data=form | {"unit:g": "none"}))
    assert "A different item?" in kept
    assert "Shown as" not in kept
    # Refused for another reason, on its page.
    item = add(session, Item(name=f"Hub {mark}", category_id=cables.id))
    session.flush()
    path = f"{ITEMS}/{item.id}"
    package = {"package_name": "Reel 5g", "size": "0", "unit:g": "Gbit/s"}
    assert f"Shown as Reel 5{NB}g." in text(
        admin_client.post(f"{path}/packages", data=package)
    )
    # An existing package's form, beside it, and only there.
    held = add(session, ItemPackage(item_id=item.id, name="Reel", size=Decimal(1)))
    session.flush()
    edit = package | {"package_version": version(held)}
    edited = text(admin_client.post(f"{path}/packages/{held.id}", data=edit))
    assert edited.count(f"Shown as Reel 5{NB}g.") == 1
    alias = {"alias_name": f"Box {mark} 10g spare", "unit:g": "Gbit/s"}
    taken = text(admin_client.post(f"{path}/aliases", data=alias))
    assert TAKEN_FIELDS["name"](spare.name_shown) in taken
    assert f"Shown as Box {mark} 10{NB}g spare." in taken


def test_a_name_shown_as_typed_says_nothing_more(
    admin_client: TestClient, session: Session, cables: Category
) -> None:
    item = add(session, Item(name=f"Patch {tag()} 2 m", category_id=cables.id))
    item.name = f"Patch {tag()}"
    session.flush()
    assert "Shown as" not in text(admin_client.get(f"{ITEMS}/{item.id}"))


def test_close_and_taken_names_are_named_as_shown(
    admin_client: TestClient, session: Session, cables: Category
) -> None:
    mark = tag()
    spool = add(session, Item(name=f"Spool {mark} 1,000ft", category_id=cables.id))
    other = add(session, Item(name=f"Other {mark}", category_id=cables.id))
    items.add_alias(session, other, f"Drum {mark} 2,000ft")
    session.flush()
    form = {"category_id": str(cables.id), "unit": "each"}
    asked = text(
        admin_client.post(ITEMS, data=form | {"name": f"Spool {mark} 1,500ft"})
    )
    assert f">Spool {mark} 1000{NB}ft</a>" in asked
    asked = text(admin_client.post(ITEMS, data=form | {"name": f"Drum {mark} 2,500ft"}))
    assert f"(alias Drum {mark} 2000{NB}ft)" in asked
    taken = text(
        admin_client.post(ITEMS, data=form | {"name": f"spool {mark} 1000 ft"})
    )
    assert TAKEN_FIELDS["name"](f"Spool {mark} 1000{NB}ft") in taken
    assert spool.retired_at is None


def test_a_read_alike_refusal_names_each_as_typed(
    admin_client: TestClient, session: Session, cables: Category
) -> None:
    """Not as shown: the refusal is about how typed names read
    (reading_rules.read_alike_words; Ali, 2026-11-15, inventory-tng-dfz)."""
    mark = tag()
    one = add(session, Item(name=f"Box {mark} 1,000g", category_id=cables.id))
    other = add(session, Item(name=f"Box {mark} 1,000 Gbps", category_id=cables.id))
    spare = add(session, Item(name=f"Spare {mark}", category_id=cables.id))
    session.flush()
    assert other.name_shown != other.name
    form = {
        "name": f"Spare {mark} 10g",
        "category_id": str(cables.id),
        "unit": "each",
        "unit:g": "Gbit/s",
        "version": version(spare),
    }
    refused = text(admin_client.post(f"{ITEMS}/{spare.id}", data=form))
    assert (
        both(
            NAMED["item"](one.id, f"Box {mark} 1,000g"),
            NAMED["item"](other.id, f"Box {mark} 1,000 Gbps"),
        )
        in refused
    )


@pytest.mark.parametrize("typed", [f"10{THIN}000{NB}ft", "10,000 ft", "10000ft"])
def test_a_name_is_found_as_shown(
    admin_client: TestClient, session: Session, cables: Category, typed: str
) -> None:
    """Searched as typed or as shown, as copied from a page: a thin space
    groups thousands as a comma does (inventory-tng-ist.1)."""
    mark = tag()
    item = add(session, Item(name=f"Box {mark} 10,000 ft", category_id=cables.id))
    session.flush()
    page = text(admin_client.get(ITEMS, params={"q": f"Box {mark} {typed}"}))
    assert f'href="{ITEMS}/{item.id}"' in page


def test_the_catalogue_sends_names_as_shown(
    registered_client: TestClient, session: Session, cables: Category
) -> None:
    """Items', packages' and retired ones', for the scan page to show; it
    searches search_text, which keeps them as typed."""
    mark = tag()
    now = datetime.now(UTC)
    item = add(session, Item(name=f"Box {mark} 1,000ft", category_id=cables.id))
    add(session, ItemPackage(item_id=item.id, name="Reel 5M spool", size=1))
    add(session, ItemPackage(item_id=item.id, name="Old 2ft", size=2, retired_at=now))
    gone = add(
        session,
        Item(name=f"Gone {mark} 3ft", category_id=cables.id, retired_at=now),
    )
    add(session, ItemPackage(item_id=gone.id, name="Bag 4ft", size=1))
    session.flush()
    reply = registered_client.get("/api/catalogue").json()
    (entry,) = [each for each in reply["items"] if each["id"] == item.id]
    assert entry["name"] == f"Box {mark} 1000{NB}ft"
    assert [p["name"] for p in entry["packages"]] == [f"Reel 5{NB}M spool"]
    assert [p["name"] for p in entry["retired_packages"]] == [f"Old 2{NB}ft"]
    (retired,) = [each for each in reply["retired_items"] if each["id"] == gone.id]
    assert retired["name"] == f"Gone {mark} 3{NB}ft"
    assert [p["name"] for p in retired["packages"]] == [f"Bag 4{NB}ft"]
    assert f"Box {mark} 1,000ft" in entry["search_text"]
    assert session.scalars(select(Item.name).where(Item.id == item.id)).one() == (
        f"Box {mark} 1,000ft"
    )
