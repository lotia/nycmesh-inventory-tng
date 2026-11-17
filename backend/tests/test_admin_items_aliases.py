"""An item's aliases (admin/items.py): searched, one namespace with names,
kept on a rename, and unchanged while the item is retired."""

from collections.abc import Callable
from functools import partial

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import items
from inventory_tng.admin.items import (
    ALIAS_ITS_OWN,
    ALIAS_TAKEN,
    ITEMS,
    NO_LETTERS,
    RENAMED,
    TAKEN,
    TAKEN_FIELDS,
)
from inventory_tng.db import attempt
from inventory_tng.models import CURRENT_ALIAS, Category, Item, ItemAlias

from helpers.items import NOW, items_named, new_item
from helpers.markup import links, read, text
from helpers.rows import add, history, item_fields, shown
from helpers.stock import retire


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
    found = items_named(admin_client.get(ITEMS, params={"q": "toughcable"}).text)
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
    found = items_named(admin_client.get(ITEMS, params={"q": "litebeam 5ac"}).text)
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
    asked = read(admin_client.post(ITEMS, data=form).text)
    similar = asked.one("p", class_="warning", role="status")
    assert ("ToughCable", f"{ITEMS}/{tough.id}") in links(similar)
    assert "ToughCable (alias Tuff cable outdoor)." in similar.text
