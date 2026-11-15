"""Reading rules: how a unit, as typed after a number, is read, settled by
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
from inventory_tng.admin.reading_rules import READINGS, SEED, offered
from inventory_tng.catalogue import catalogue
from inventory_tng.models import (
    Category,
    Item,
    ItemAlias,
    ItemPackage,
    ReadingRule,
    unit_key,
)

from helpers.rows import add, breaks, history, item_fields, named, shown, violates
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
    """10g is grams by the table; a rule, kept by the unit as typed (Ali,
    2026-11-15), reads it after any number as gigabits a second, or as no
    unit, wherever a unit is read: search, keys and the forms."""
    assert units.respell("Box 10g") == "Box 10g"
    assert units.respell("Box 10 g, 40g", {"g": GBPS}) == "Box 10Gbit/s, 40Gbit/s"
    assert units.respell("Tape 3M", {"M": units.Rule(None)}) == "Tape 3M"
    assert unit_key("Box 10g", {"g": GBPS}) == unit_key("Box 10 Gbps")
    (found,) = units.found_in("Box 10G", {"G": units.Rule(None)})
    assert (found.key, found.read, found.asks) == ("G", None, False)
    # Case kept: a rule for G settles no g.
    (found,) = units.found_in("Box 10g", {"G": units.Rule(None)})
    assert (found.read, found.asks) == (None, True)


def test_a_rule_wins_over_the_words_near_a_unit() -> None:
    """A rule is looked up first and always wins (Ali, 2026-11-15); the
    words near a unit decide where no rule does, on the forms and in
    search alike."""
    rules = {"M": units.Rule("Mbit/s"), "G": units.Rule(None)}
    for name, read, clue, ruled in [
        ("Spool 305M", "m", ("spool",), "Mbit/s"),
        ("Cat6 patch 3M", "m", ("cat6", "patch"), "Mbit/s"),
        ("Tape 3M", None, (), "Mbit/s"),
        ("SFP+ 10G", "Gbit/s", ("sfp+",), None),
        ("Box 10G", "Gbit/s", (), None),
    ]:
        (found,) = units.found_in(name)
        assert (str(found.read) if found.read else None, found.clue) == (
            read,
            clue,
        ), name
        (found,) = units.found_in(name, rules)
        assert (str(found.read) if found.read else None, found.clue) == (
            ruled,
            (),
        ), name
        assert units.respell(name, rules).endswith(ruled or name[-1]), name


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


def test_a_rename_to_its_own_alias_takes_no_other_items_alias(
    session: Session, radios: Category
) -> None:
    """Its own alias found by one key, swapped, the name's other key is
    still checked against every other current alias: here another item's
    Box 10G, read as no unit, has Box 10g's name key."""
    reading_rules.decide(session, {"G": units.Rule(None), "g": GBPS}, None)
    one, other = new_item(session, radios, "Thing A"), new_item(session, radios, "B")
    items.add_alias(session, one, "Box 10 Gbps")
    items.add_alias(session, other, "Box 10G")
    with breaks(session, items.NAME_IS_ALIAS):
        items.save(session, one, item_fields(one, name="Box 10g"), seen=None)


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


def test_a_respelling_rule_respells_whatever_the_words_near_it() -> None:
    respelling = {"G": units.Rule("Gbit/s", respell=True)}
    for typed in ("Switch 10G", "Nano 10G"):
        found = units.found_in(typed, respelling)
        assert units.spell(typed, found) == typed.replace("10G", "10Gbps")


def test_a_rule_reads_every_name_with_it_again(
    session: Session, radios: Category
) -> None:
    """Its key and what search keeps, items', packages' and aliases', and
    back as the table reads it once deleted."""
    link = new_item(session, radios, "Thing 10g")
    spare = new_item(session, radios, "Spare")
    box = add(session, ItemPackage(item_id=spare.id, name="Box 40g", size=1))
    alias = items.add_alias(session, spare, "Other 1 g")
    rack = new_item(session, radios, "Rack 10G")
    assert link.unit_key == "thing10g"
    # Only names with that unit after a number are read again, and locked.
    found = items._with_units(session, ["g"], lock=False)
    assert found == ([link, spare], [box], [alias])
    assert rack not in found[0]
    (rule,) = reading_rules.decide(session, {"g": GBPS}, None)
    session.flush()
    assert (link.unit_key, box.unit_key, alias.unit_key) == (
        "thing10gbits",
        "box40gbits",
        "other1gbits",
    )
    assert "10Gbit/s" in link.search_text
    assert "40Gbit/s" in box.search_text
    assert "Other 1Gbit/s" in spare.search_text
    # Its alias once, though rereading it flushed the alias first.
    assert spare.search_text.count("Other 1") == 2
    # The same again changes nothing.
    assert reading_rules.decide(session, {"g": GBPS}, None) == []
    reading_rules.delete(session, rule, seen=None)
    session.flush()
    assert link.unit_key == "thing10g"
    assert "Gbit/s" not in link.search_text


def test_a_rule_reading_names_alike_is_refused(
    session: Session, radios: Category
) -> None:
    """Box 10g and Box 10 Gbps are two names until g reads as Gbps: the
    rule is refused, and read_alike names both, by id."""
    one = new_item(session, radios, "Box 10g")
    other = new_item(session, radios, "Box 10 Gbps")
    with breaks(session, items.READ_ALIKE):
        reading_rules.decide(session, {"g": GBPS}, None)
    assert session.scalars(select(ReadingRule)).all() == []
    (group,) = items.read_alike(session, ["g"], {"g": GBPS})
    assert [(each.table, each.id) for each in group] == [
        ("item", one.id),
        ("item", other.id),
    ]
    # Unsettled, it is read as typed, clashing with nothing.
    assert items.read_alike(session, ["g"], {}) == []


def test_a_rule_may_read_what_the_table_finds_unlikely() -> None:
    """Offered with the likely readings; on its page, every reading of the
    unit, whatever the number; one no unit spells offers none."""
    rules = {"G": units.Rule("Mbit/s")}
    (found,) = units.found_in("Box 10G", rules)
    assert [str(r) for r in found.offered] == ["Gbit/s", "g", "Mbit/s"]
    assert [str(r) for r in offered("G", units.Rule("Mbit/s"))] == [
        "Gbit/s",
        "g",
        "Mbit/s",
    ]
    assert offered("P", units.Rule(None)) == ()


def test_a_rule_reads_only_known_readings(session: Session) -> None:
    with breaks(session, reading_rules.NOT_A_READING):
        reading_rules.decide(session, {"G": units.Rule("Gbps")}, None)
    with breaks(session, reading_rules.NOT_A_READING):
        reading_rules.decide(session, {"G": units.Rule(None, respell=True)}, None)


def test_the_answer_to_a_unit_asked_about_is_kept_as_a_rule(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Who settled it, and when; asked no more."""
    form = {"name": "Weight 5 MG", "category_id": str(radios.id), "unit": "each"}
    assert admin_client.post(ITEMS, data=form).status_code == 400
    done = admin_client.post(ITEMS, data=form | {"unit:MG": "mg"})
    assert done.status_code == 303
    rule = rule_of(session, "MG")
    assert (rule.reading, rule.decided_by_admin_id is not None) == ("mg", True)
    again = form | {"name": "Brass 50 MG"}
    assert admin_client.post(ITEMS, data=again).status_code == 303
    named(session, Item, "Brass 50 MG")


def test_a_g_after_a_number_is_asked_unless_the_words_near_it_say(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Ali, 2026-11-15: a g is never silently grams. Near SFP, a switch or
    an uplink it is a data rate, near paste or grease grams; else asked
    once, the answer kept as a rule. A G is Gbps without asking."""
    form = {"category_id": str(radios.id), "unit": "each"}

    def adding(name: str, **more: str) -> dict[str, str]:
        # Each a different item, whatever names are close.
        return form | {"name": name, "different": name, **more}

    for name in ("SFP 10g", "Thermal paste 100g", "Box 1G"):
        assert admin_client.post(ITEMS, data=adding(name)).status_code == 303
    assert unit_key("SFP 10g") == "sfp10gbits"
    assert unit_key("Thermal paste 100g") == "thermalpaste100g"
    assert unit_key("Box 1G") == "box1gbits"
    asked = admin_client.post(ITEMS, data=adding("Thing 10g"))
    assert asked.status_code == 400
    page = text(asked)
    assert "“10g” is new. Is it a unit?" in page
    assert "10Gbps · data rate" in page
    assert "10g · grams" in page
    assert "Not a unit: keep “10g”" in page
    done = admin_client.post(ITEMS, data=adding("Thing 10g", **{"unit:g": "Gbit/s"}))
    assert done.status_code == 303
    assert rule_of(session, "g").reading == "Gbit/s"
    assert named(session, Item, "Thing 10g").unit_key == "thing10gbits"
    assert admin_client.post(ITEMS, data=adding("Other 40g")).status_code == 303


def test_the_scan_page_has_the_rules(session: Session) -> None:
    reading_rules.decide(session, {"g": GBPS, "M": units.Rule(None)}, None)
    sent = catalogue(session).reading_rules
    assert [(rule.token, rule.reading) for rule in sent] == [
        ("M", None),
        ("g", "Gbit/s"),
    ]


def test_the_items_list_reads_searches_by_the_rules(session: Session) -> None:
    assert SEARCHED.rules(session) == {}
    reading_rules.decide(session, {"g": GBPS}, None)
    rules = SEARCHED.rules(session)
    assert folded("Box 10g", rules) == ("Box 10g", "Box 10Gbit/s")
    assert folded("Box 10g") == ("Box 10g",)


def test_the_rules_page_lists_changes_and_deletes_them(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    new_item(session, radios, "Box 10G")
    (rule,) = reading_rules.decide(session, {"G": units.Rule(None)}, None)
    listed = text(admin_client.get(READINGS))
    assert f'<a href="{READINGS}/{rule.id}">G</a>' in listed
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
    assert "Box" not in str(line)
    # A reading not offered shows the form again.
    unknown = admin_client.post(path, data={"reading": "xx", "version": shown(rule)})
    assert unknown.status_code == 400
    assert "Choose one of the readings offered." in text(unknown)
    stale = admin_client.post(path, data={"reading": "none", "version": "1"})
    assert stale.status_code == 409
    # Nor deleted, changed since it was shown.
    assert admin_client.post(f"{path}/delete", data={"version": "1"}).status_code == 409
    assert session.get(ReadingRule, rule.id) is not None
    page = text(admin_client.get(path))
    assert f'name="version" value="{shown(rule)}"><button>Delete' in page
    deleted = admin_client.post(f"{path}/delete", data={"version": shown(rule)})
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
    (rule,) = reading_rules.decide(session, {"Cel": units.Rule(None)}, None)
    one = new_item(session, radios, "Probe 85 Cel")
    other = new_item(session, radios, "Probe 85\u00b0C")
    path = f"{READINGS}/{rule.id}"
    said = f"item {one.id} (Probe 85 Cel) and item {other.id} (Probe 85\u00b0C)"
    for refused in (
        admin_client.post(path, data={"reading": "degC", "version": shown(rule)}),
        admin_client.post(f"{path}/delete", data={"version": shown(rule)}),
    ):
        assert refused.status_code == 409
        assert said in text(refused)
    assert rule.reading is None


def test_rules_are_for_administrators(make_client: Callable[..., TestClient]) -> None:
    answer = make_client().get(READINGS)
    assert answer.status_code == 303
    assert answer.headers["location"].startswith(sign_in.SIGN_IN)


def test_proposals_read_the_words_near_each_unit() -> None:
    """Ali, 2026-11-15: a unit the words near it settle needs no rule, but
    says what the unit is in names where they do not: G near SFP+ or an
    uplink is a data rate, M near a spool a length. A unit the table reads
    loosely is proposed so; one nothing settles is proposed nothing, to
    choose; one read as typed, or by a rule, is not listed."""
    names = [
        "Uplink SFP+ 10G",
        "Switch 10G port",
        "Spool 305M cable",
        "Tape 3M",
        "Cap 10MF",
        "Patch 2 m",
        "Link 1G",
        "Box 10G",
        "Thing 1G",
        "Other 2G",
        "Spare 10G",
        "Thing 10g",
        "Hub 4 K",
    ]
    proposals = reading_rules.propose(names, {"K": units.Rule(None)})
    said = {
        p.found.key: (p.count, str(p.proposed) if p.proposed else None, p.why)
        for p in proposals
    }
    assert said == {
        "G": (4, "Gbit/s", "elsewhere near link, port, sfp+, switch, uplink"),
        "M": (1, "m", "elsewhere near cable, spool"),
        "MF": (1, "mF", "the one likely reading"),
        "g": (1, None, ""),
    }
    assert proposals[0].found.key == "G"
    # A few of the names, first found.
    assert proposals[0].names == ["Box 10G", "Thing 1G", "Other 2G"]


def test_the_seeding_page_proposes_and_accepts_readings(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Accept all, each as chosen; refused while one nothing settles is not
    chosen; accepted ones become rules, and are proposed no more."""
    link = new_item(session, radios, "Uplink SFP+ 10G")
    box = add(session, ItemPackage(item_id=link.id, name="Box 1G", size=1))
    items.add_alias(session, link, "Tape 3M")
    page = text(admin_client.get(SEED))
    assert "“G”" in page
    assert "elsewhere near sfp+, uplink" in page
    assert '<option value="">Choose…</option>' in page
    assert "Mbps · data rate" in page
    assert "m · meters" in page
    form = {"reading:G": "g", "reading:M": ""}
    refused = admin_client.post(SEED, data=form)
    assert refused.status_code == 400
    assert "Choose how to read each one marked" in text(refused)
    assert session.scalars(select(ReadingRule)).all() == []
    with capture_logs() as logs:
        done = admin_client.post(SEED, data=form | {"reading:M": "none"})
    assert done.headers["location"] == f"{SEED}?notice=readings_accepted"
    rules = {r.token: r.reading for r in session.scalars(select(ReadingRule))}
    assert rules == {"G": "g", "M": None}
    (line,) = [line for line in logs if line["event"] == "reading rules set"]
    assert line["by"] == "seed"
    assert len(line["reading_rule_ids"]) == 2
    assert "Nothing to settle" in text(admin_client.get(SEED))
    assert "Gbit/s" not in box.search_text
    # The rule wins over the words near it: the item's G is grams too.
    assert "Gbit/s" not in link.search_text
    assert link.search_text.count("Tape 3M") == 1
    # Accepted twice, as a page sent again: nothing more is written.
    with capture_logs() as logs:
        again = admin_client.post(SEED, data=form | {"reading:M": "none"})
    assert again.status_code == 303
    (line,) = [line for line in logs if line["event"] == "reading rules set"]
    assert line["reading_rule_ids"] == []
    assert len(session.scalars(select(ReadingRule)).all()) == 2


def test_the_seeding_page_reviews_one_by_one(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    new_item(session, radios, "Box 10G")
    new_item(session, radios, "Tape 3M")
    page = text(admin_client.get(f"{SEED}?one=1"))
    assert "1 of 2." in page
    assert "“G”" in page
    assert "“M”" not in page
    assert f'href="{SEED}?one=1&at=1">Skip</a>' in page
    skipped = text(admin_client.get(f"{SEED}?one=1&at=1"))
    assert "2 of 2." in skipped
    assert "“M”" in skipped
    form = {"one": "1", "at": "0", "reading:G": "Gbit/s"}
    done = admin_client.post(SEED, data=form)
    assert done.headers["location"] == (f"{SEED}?one=1&at=0&notice=readings_accepted")
    assert "“M”" in text(admin_client.get(done.headers["location"]))
    # A row settled meanwhile is left as it is.
    again = admin_client.post(SEED, data=form | {"reading:G": "g"})
    assert again.status_code == 303
    assert rule_of(session, "G").reading == "Gbit/s"


def test_the_seeding_page_names_what_would_read_alike(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    one = new_item(session, radios, "Box 10g")
    other = new_item(session, radios, "Box 10 Gbps")
    refused = admin_client.post(SEED, data={"reading:g": "Gbit/s"})
    assert refused.status_code == 409
    assert f"item {one.id} (Box 10g) and item {other.id} (Box 10 Gbps)" in (
        text(refused)
    )


def test_a_rule_may_respell_names_typed_with_it(
    admin_client: TestClient, session: Session, radios: Category
) -> None:
    """Set on the rule's page: names typed with it after are saved with the
    unit's symbol joined to its number; one typed before stays as it is."""
    before = new_item(session, radios, "Tape A 305M")
    (rule,) = reading_rules.decide(session, {"M": units.Rule("m")}, None)
    page = text(admin_client.get(f"{READINGS}/{rule.id}"))
    assert "Respell it" in page
    sent = {"reading": "m", "respell": "on", "version": shown(rule)}
    assert admin_client.post(f"{READINGS}/{rule.id}", data=sent).status_code == 303
    assert (rule.reading, rule.respell) == ("m", True)
    assert "Respelt" in text(admin_client.get(READINGS))
    form = {"name": "Tape B 305 M", "category_id": str(radios.id), "unit": "each"}
    # A different item from the other tape, whose name is close.
    sent = form | {"different": "Tape B 305 M"}
    assert admin_client.post(ITEMS, data=sent).status_code == 303
    named(session, Item, "Tape B 305m")
    assert before.name == "Tape A 305M"
    page = text(admin_client.get(f"{ITEMS}/{before.id}"))
    assert "Read as 305m · length.\n  Saved as 305m." in page
