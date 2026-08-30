"""An item made in the app is findable the way an imported one is.

An item from the spreadsheet held an identifier and an item made in the app
held none, so the two were in different states and only one kept the promise
`ItemIdentifier`'s own docstring makes. inventory-tng-w4dg, and decision 0026's
amendment is what it settled.

These are the journeys rather than the rules: create one and find it, rename it
and still find it, and meet a clean refusal where a string is taken.
"""

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from inventory.models import Category, Item, ItemIdentifier

pytestmark = pytest.mark.django_db


# The fields frontend/src/admin/CreateItem.tsx sends, so that this fails if the
# app can produce an item by a route these tests do not cover.
#
# `reorder_quantity` is not nought, and that is the serializer's own rule
# rather than an arbitrary choice here: reordering none of something is not an
# order, and posting a nought would test the validator instead of the alias.
def as_the_app_sends_it(category: Category, name: str) -> dict[str, Any]:
    return {
        "name": name,
        "category": category.pk,
        "unit_of_measure": "each",
        "minimum_stock": "2",
        "reorder_quantity": "10",
    }


def created(client: Client, category: Category, name: str) -> Any:
    return client.post(reverse("items"), as_the_app_sends_it(category, name), content_type="application/json")


def test_an_item_created_in_the_app_can_be_found_by_its_name(editor: Client, category: Category) -> None:
    """The acceptance criterion, written as the thing somebody does.

    Created through the API with the fields the form sends, then found by
    typing its name -- which is the whole journey the issue is about, rather
    than an assertion about a row.
    """
    assert created(editor, category, "Mikrotik hEX S").status_code == 201

    found = editor.get(reverse("items"), {"search": "Mikrotik hEX S"})
    body = found.json()
    results = body["results"] if isinstance(body, dict) else body

    assert [row["name"] for row in results] == ["Mikrotik hEX S"]


def test_and_it_holds_an_alias_exactly_as_mint_items_would_have(editor: Client, category: Category) -> None:
    """The row, and its kind.

    A different kind would make the importer and this path disagree about what
    an item's own name is, which is the drift the amendment names.
    """
    created(editor, category, "Mikrotik hEX S")

    identifier = ItemIdentifier.objects.get(value="Mikrotik hEX S")

    assert identifier.kind == ItemIdentifier.Kind.ALIAS
    assert identifier.item.name == "Mikrotik hEX S"


def test_a_name_another_item_is_known_by_is_a_conflict_not_a_500(editor: Client, category: Category) -> None:
    """Decision 0026 point 4, arriving from the opposite direction.

    That record says which direction; what matters here is that it reaches the
    same unique index from the other side and so needs its own case rather
    than leaning on the duplicate-identifier one.
    """
    other = Item.objects.create(name="Ubiquiti LiteBeam", category=category)
    ItemIdentifier.objects.create(item=other, kind=ItemIdentifier.Kind.ALIAS, value="LiteBeam AC")

    refused = created(editor, category, "LiteBeam AC")

    assert refused.status_code == 400, refused.content
    assert "Ubiquiti LiteBeam" in str(refused.json()), (
        "the refusal does not name the item that already holds the string, so somebody meeting it cannot "
        "tell which of their two items is the problem"
    )


def test_and_the_refused_item_was_not_left_behind(editor: Client, category: Category) -> None:
    """The two writes are one transaction, so a refusal leaves nothing behind."""
    other = Item.objects.create(name="Ubiquiti LiteBeam", category=category)
    ItemIdentifier.objects.create(item=other, kind=ItemIdentifier.Kind.ALIAS, value="LiteBeam AC")

    created(editor, category, "LiteBeam AC")

    assert not Item.objects.filter(name="LiteBeam AC").exists()


def test_a_fold_collision_is_a_conflict_too(editor: Client, category: Category) -> None:
    """The database's fold decides, not Python's.

    Two strings that differ only by case and spacing are one identifier, which
    is what decision 0026 settles -- so this has to be refused for the same
    reason and with the same message as an exact repeat.
    """
    other = Item.objects.create(name="Ubiquiti LiteBeam", category=category)
    ItemIdentifier.objects.create(item=other, kind=ItemIdentifier.Kind.ALIAS, value="LiteBeam AC")

    refused = created(editor, category, "  litebeam   AC  ")

    assert refused.status_code == 400, refused.content
    assert "Ubiquiti LiteBeam" in str(refused.json())


# ---------------------------------------------------------------------------
# A rename, which is a decision rather than something that falls out
# ---------------------------------------------------------------------------


def renamed(client: Client, item: Item, name: str) -> Any:
    return client.patch(reverse("item-detail", args=[item.pk]), {"name": name}, content_type="application/json")


def test_a_rename_keeps_the_old_name_findable(editor: Client, category: Category) -> None:
    """Every string that has ever meant the item, which is what the table is for.

    Who that promise is for, and why dropping it would be the spreadsheet's
    failure returning, is the amendment.
    """
    created(editor, category, "Mikrotik hEX S")
    item = Item.objects.get(name="Mikrotik hEX S")

    assert renamed(editor, item, "MikroTik hEX S (rev 2)").status_code == 200

    results = editor.get(reverse("items"), {"search": "Mikrotik hEX S"}).json()
    rows = results["results"] if isinstance(results, dict) else results
    assert [row["name"] for row in rows] == ["MikroTik hEX S (rev 2)"], (
        "the old name no longer finds the item, so a rename silently broke every printout and every "
        "person who still calls it that"
    )


def test_and_makes_the_new_one_findable_too(editor: Client, category: Category) -> None:
    """The other half, which is the one that could fall out of the implementation.

    Keeping the old alias is the visible decision; minting one for the new name
    is what stops the rename leaving the item findable only by a string nobody
    uses any more.
    """
    created(editor, category, "Mikrotik hEX S")
    item = Item.objects.get(name="Mikrotik hEX S")

    renamed(editor, item, "MikroTik hEX S (rev 2)")

    held = set(ItemIdentifier.objects.filter(item=item).values_list("value", flat=True))
    assert held == {"Mikrotik hEX S", "MikroTik hEX S (rev 2)"}


def test_renaming_onto_another_items_name_is_the_same_conflict(editor: Client, category: Category) -> None:
    """Because it reaches the same unique index by the same route."""
    other = Item.objects.create(name="Ubiquiti LiteBeam", category=category)
    ItemIdentifier.objects.create(item=other, kind=ItemIdentifier.Kind.ALIAS, value="LiteBeam AC")
    created(editor, category, "Mikrotik hEX S")
    item = Item.objects.get(name="Mikrotik hEX S")

    refused = renamed(editor, item, "LiteBeam AC")

    assert refused.status_code == 400, refused.content
    assert "Ubiquiti LiteBeam" in str(refused.json())
    item.refresh_from_db()
    assert item.name == "Mikrotik hEX S", "the rename was refused and applied anyway"


def test_renaming_to_what_it_is_already_called_changes_nothing(editor: Client, category: Category) -> None:
    """The item holds its own alias, so a no-op rename meets its own row.

    Which must not be read as a collision: `hold` reports the holder either
    way, and only a holder that is a DIFFERENT item is a conflict.
    """
    created(editor, category, "Mikrotik hEX S")
    item = Item.objects.get(name="Mikrotik hEX S")

    assert renamed(editor, item, "Mikrotik hEX S").status_code == 200
    assert ItemIdentifier.objects.filter(item=item).count() == 1
