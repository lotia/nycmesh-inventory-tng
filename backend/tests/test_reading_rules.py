"""Reading rules: how a number and its unit, as typed, are read, settled by
an administrator, before the unit table (reading_rules.py, admin/
reading_rules.py, models.ReadingRule; docs/decisions/0021, 2026-11-15,
inventory-tng-c0c.4), and names unique once their units are read
(models.KeyedName.unit_key)."""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from structlog.testing import capture_logs

from inventory_tng import items, names, reading_rules, sign_in, units
from inventory_tng.admin.items import ITEMS, SEARCHED
from inventory_tng.admin.listing import folded
from inventory_tng.admin.reading_rules import READINGS
from inventory_tng.catalogue import catalogue
from inventory_tng.models import (
    Category,
    Item,
    ItemAlias,
    ItemPackage,
    ReadingRule,
    unit_key,
)

from helpers.rows import add, breaks, history, named, shown, violates
from helpers.sign_in import text

GBPS = units.Rule("Gbit/s")


@pytest.fixture
def radios(session: Session) -> Category:
    return add(session, Category(name="Radios"))


def new_item(session: Session, category: Category, name: str) -> Item:
    return add(session, Item(name=name, category_id=category.id))


def rule_of(session: Session, token: str) -> ReadingRule:
    return session.scalars(select(ReadingRule).where(ReadingRule.token == token)).one()


def test_a_rule_reads_a_unit_before_the_table() -> None:
    """10G is grams by the table; a rule reads it as gigabits a second, or
    as no unit, wherever a unit is read: search, keys and the forms."""
    assert units.respell("SFP+ 10G") == "SFP+ 10g"
    assert units.respell("SFP+ 10 G", {"10G": GBPS}) == "SFP+ 10Gbit/s"
    assert units.respell("Tape 3M", {"3M": units.Rule(None)}) == "Tape 3M"
    assert unit_key("Link 10G", {"10G": GBPS}) == unit_key("Link 10 Gbps")
    (found,) = units.found_in("Link 10G", {"10G": units.Rule(None)})
    assert (found.key, found.settled, found.asks) == ("10G", None, False)


def test_names_that_read_alike_are_one(session: Session, radios: Category) -> None:
    """10 uF, 10 µF and 10μF are one name once units are read, though their
    keys differ (Ali, 2026-11-15): items', an item's packages' and the one
    namespace of names and aliases."""
    cap = new_item(session, radios, "Cap 10 uF")
    assert cap.unit_key == unit_key("Cap 10μF") == "cap10uf"
    assert cap.name_key != names.name_key("Cap 10μF")
    with violates(session, "uq_item_unit_key"):
        session.add(Item(name="Cap 10μF", category_id=radios.id))
    with violates(session, "uq_item_package_unit_key"):
        session.add_all(
            ItemPackage(item_id=cap.id, name=name, size=1)
            for name in ("Bag 2 uF", "Bag 2 µF")
        )
    with breaks(session, items.ALIAS_IS_NAME):
        items.add_alias(session, cap, "Cap 10 µF")
    items.add_alias(session, cap, "Capacitor 10 uF")
    with violates(session, "uq_item_alias_unit_key"):
        session.add(ItemAlias(item_id=cap.id, name="Capacitor 10µF"))


def test_a_name_reading_as_another_is_refused_naming_it(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    new_item(session, radios, "Cap 10 uF")
    form = {"category_id": str(radios.id), "unit": "each"}
    refused = admin_client.post(ITEMS, data=form | {"name": "Cap 10 µF"})
    assert refused.status_code == 400
    assert (
        "Cap 10 uF already has that name, or one differing from it only in "
        "case, spaces, punctuation or how its units are spelt."
    ) in text(refused)
    rocket = new_item(session, radios, "Rocket")
    items.add_alias(session, rocket, "Link 1 Gbps")
    refused = admin_client.post(ITEMS, data=form | {"name": "Link 1Gbit/s"})
    assert "Rocket has that name" in text(refused)
    package = admin_client.post(
        f"{ITEMS}/{rocket.id}/packages", data={"package_name": "Box 1 kg", "size": "1"}
    )
    assert package.status_code == 303
    again = admin_client.post(
        f"{ITEMS}/{rocket.id}/packages", data={"package_name": "Box 1kg", "size": "2"}
    )
    assert "It already has a package by that name." in text(again)


def test_a_rule_reads_every_name_with_it_again(
    session: Session, radios: Category
) -> None:
    """Its key and what search keeps, items', packages' and aliases', and
    back as the table reads it once deleted."""
    link = new_item(session, radios, "SFP+ 10G")
    spare = new_item(session, radios, "Spare")
    box = add(session, ItemPackage(item_id=spare.id, name="Box 10G", size=1))
    alias = items.add_alias(session, spare, "Uplink 10 G")
    assert link.unit_key == "sfp10g"
    (rule,) = reading_rules.decide(session, {"10G": GBPS}, None)
    session.flush()
    assert (link.unit_key, box.unit_key, alias.unit_key) == (
        "sfp10gbits",
        "box10gbits",
        "uplink10gbits",
    )
    assert "10Gbit/s" in link.search_text
    assert "10Gbit/s" in box.search_text
    assert "Uplink 10Gbit/s" in spare.search_text
    # The same again changes nothing.
    assert reading_rules.decide(session, {"10G": GBPS}, None) == []
    reading_rules.delete(session, rule)
    session.flush()
    assert link.unit_key == "sfp10g"
    assert "Gbit/s" not in link.search_text


def test_a_rule_reading_names_alike_is_refused(
    session: Session, radios: Category
) -> None:
    """Link 10G and Link 10 Gbps are two names until 10G reads as Gbps: the
    rule is refused, and read_alike names both, by id."""
    one = new_item(session, radios, "Link 10G")
    other = new_item(session, radios, "Link 10 Gbps")
    with breaks(session, items.READ_ALIKE):
        reading_rules.decide(session, {"10G": GBPS}, None)
    assert session.scalars(select(ReadingRule)).all() == []
    (group,) = items.read_alike(session, ["10G"], {"10G": GBPS})
    assert [(each.table, each.id) for each in group] == [
        ("item", one.id),
        ("item", other.id),
    ]
    # Only another reading's names: grams clash with nothing.
    assert items.read_alike(session, ["10G"], {}) == []


def test_a_rule_reads_only_known_readings(session: Session) -> None:
    with breaks(session, reading_rules.NOT_A_READING):
        reading_rules.decide(session, {"10G": units.Rule("Gbps")}, None)
    with breaks(session, reading_rules.NOT_A_READING):
        reading_rules.decide(session, {"10G": units.Rule(None, respell=True)}, None)


def test_the_answer_to_a_unit_asked_about_is_kept_as_a_rule(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Who settled it, and when; asked no more."""
    form = {"name": "Weight 5 MG", "category_id": str(radios.id), "unit": "each"}
    assert admin_client.post(ITEMS, data=form).status_code == 400
    done = admin_client.post(ITEMS, data=form | {"unit:5MG": "mg"})
    assert done.status_code == 303
    rule = rule_of(session, "5MG")
    assert (rule.reading, rule.decided_by_admin_id is not None) == ("mg", True)
    again = form | {"name": "Brass 5 MG"}
    assert admin_client.post(ITEMS, data=again).status_code == 303
    named(session, Item, "Brass 5mg")


def test_the_scan_page_has_the_rules(session: Session) -> None:
    reading_rules.decide(session, {"10G": GBPS, "16P": units.Rule(None)}, None)
    sent = catalogue(session).reading_rules
    assert [(rule.token, rule.reading) for rule in sent] == [
        ("10G", "Gbit/s"),
        ("16P", None),
    ]


def test_the_items_list_reads_searches_by_the_rules(session: Session) -> None:
    assert SEARCHED.rules(session) == {}
    reading_rules.decide(session, {"10G": GBPS}, None)
    rules = SEARCHED.rules(session)
    assert folded("SFP+ 10G", rules) == ("SFP+ 10G", "SFP+ 10Gbit/s")
    assert folded("SFP+ 10G") == ("SFP+ 10G",)


def test_the_rules_page_lists_changes_and_deletes_them(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    new_item(session, radios, "Link 10G")
    (rule,) = reading_rules.decide(session, {"10G": units.Rule(None)}, None)
    listed = text(admin_client.get(READINGS))
    assert f'<a href="{READINGS}/{rule.id}">10G</a>' in listed
    assert "Not a unit" in listed
    assert "The import" in listed
    page = text(admin_client.get(f"{READINGS}/{rule.id}"))
    assert 'value="g"' in page
    assert "g · mass" in page
    path = f"{READINGS}/{rule.id}"
    with capture_logs() as logs:
        done = admin_client.post(path, data={"reading": "g", "version": shown(rule)})
    assert done.status_code == 303
    assert rule.reading == "g"
    assert rule.decided_by_admin_id is not None
    (line,) = [line for line in logs if line["event"] == "reading rule saved"]
    assert line["reading_rule_id"] == rule.id
    assert "10G" not in str(line)
    # A reading not offered shows the form again.
    unknown = admin_client.post(path, data={"reading": "xx", "version": shown(rule)})
    assert unknown.status_code == 200
    stale = admin_client.post(path, data={"reading": "none", "version": "1"})
    assert stale.status_code == 409
    deleted = admin_client.post(f"{path}/delete")
    assert deleted.headers["location"] == f"{READINGS}?notice=reading_deleted"
    assert session.get(ReadingRule, rule.id) is None
    assert [row.action for row in history(session, "reading_rule", rule.id)][-1] == (
        "delete"
    )
    assert admin_client.get(path).status_code == 404
    assert admin_client.post(path, data={}).status_code == 404
    assert admin_client.post(f"{path}/delete").status_code == 404


def test_a_rule_change_reading_names_alike_names_them(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """85 Cel is no unit by its rule, so Probe 85 Cel and Probe 85°C are two
    names: read as degrees Celsius, by the rule or the table, they would be
    one, so neither change nor deletion is taken."""
    (rule,) = reading_rules.decide(session, {"85Cel": units.Rule(None)}, None)
    one = new_item(session, radios, "Probe 85 Cel")
    other = new_item(session, radios, "Probe 85\u00b0C")
    path = f"{READINGS}/{rule.id}"
    said = f"item {one.id} (Probe 85 Cel) and item {other.id} (Probe 85\u00b0C)"
    for refused in (
        admin_client.post(path, data={"reading": "degC", "version": shown(rule)}),
        admin_client.post(f"{path}/delete"),
    ):
        assert refused.status_code == 409
        assert said in text(refused)
    assert rule.reading is None


def test_rules_are_for_administrators(make_client: Callable[..., TestClient]) -> None:
    answer = make_client().get(READINGS)
    assert answer.status_code == 303
    assert answer.headers["location"].startswith(sign_in.SIGN_IN)
