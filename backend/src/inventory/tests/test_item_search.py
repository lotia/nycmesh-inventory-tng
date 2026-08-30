"""What the box marked "search" actually looks at, and what it may show back.

`ItemFilter.search` read `Item.name` and nothing else, so a barcode, a legacy
NYCM code or a manufacturer's part number typed into the box the app offers
found nothing at all. inventory-tng-gz2.

Two halves, and the second is the one nothing enforced before. MATCHING is
decision 0026 point 5's first clause. OFFERING is its second, and it is a guard
rather than a fix, since nothing violates it today.

The guard SCANS rather than allowlisting, which is that record's point and the
reason these tests look the way they do: a test naming the fields that may not
carry an identifier passes the day somebody adds one under a new name.
"""

import json
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from inventory.models import Category, Item, ItemIdentifier

pytestmark = pytest.mark.django_db

# Deliberately unlike the item's name, so that a response carrying it can only
# have got it from the identifier row.
A_BARCODE = "0847101099965"


@pytest.fixture
def catalogued(category: Category) -> Item:
    item = Item.objects.create(name="LiteBeam AC Gen2", category=category)
    ItemIdentifier.objects.create(item=item, kind=ItemIdentifier.Kind.BARCODE, value=A_BARCODE)
    return item


def found(client: Client, typed: str) -> list[dict[str, Any]]:
    answer = client.get(reverse("items"), {"search": typed})
    assert answer.status_code == 200, answer.content
    body = answer.json()
    return body["results"] if isinstance(body, dict) else body


# ---------------------------------------------------------------------------
# What it matches
# ---------------------------------------------------------------------------


def test_a_barcode_finds_the_item_it_belongs_to(editor: Client, catalogued: Item) -> None:
    """The defect, stated as the thing somebody at a shelf tries to do."""
    results = found(editor, A_BARCODE)

    assert [row["name"] for row in results] == [catalogued.name], (
        "typing an identifier finds nothing, so the table that exists to make every string resolve is "
        "reachable only from Django's admin -- which is what inventory-tng-gz2 is"
    )


def test_a_prefix_of_one_is_enough(editor: Client, catalogued: Item) -> None:
    """Because this populates a control somebody types into a character at a time."""
    assert [row["name"] for row in found(editor, A_BARCODE[:5])] == [catalogued.name]


def test_the_name_still_finds_it(editor: Client, catalogued: Item) -> None:
    """BOTH, not identifiers instead of names.

    Decision 0026 point 5 argues the ordering, and inventory-tng-w4dg is the
    gap that makes it matter. What is held here is only that closing the first
    half did not quietly cost the other.
    """
    assert [row["name"] for row in found(editor, "LiteBeam")] == [catalogued.name]


def test_an_item_with_several_matching_identifiers_appears_once(editor: Client, catalogued: Item) -> None:
    """One product, three strings, one row.

    A join fans out, and the screen then shows the same thing three times --
    which is the shape of the complaint this project started from.
    """
    for suffix in ("A", "B"):
        ItemIdentifier.objects.create(
            item=catalogued,
            kind=ItemIdentifier.Kind.VENDOR_SKU,
            value=f"{A_BARCODE}{suffix}",
        )

    assert len(found(editor, A_BARCODE)) == 1, "the item is offered once per identifier that matched"


def test_something_that_matches_nothing_finds_nothing(editor: Client, catalogued: Item) -> None:
    """The other direction, so the two above cannot pass by matching everything."""
    assert found(editor, "nothing-here") == []


# ---------------------------------------------------------------------------
# What it may show back -- decision 0026 point 5, second clause
# ---------------------------------------------------------------------------


def anywhere_in(body: Any, wanted: str) -> bool:
    """Whether that string appears anywhere in the response, at any depth.

    Recursive on purpose. A test asserting that no field NAMED something
    carries an identifier passes the day a field is added under a new name,
    and the record predicted that is the shape the leak takes.
    """
    if isinstance(body, dict):
        return any(anywhere_in(value, wanted) for value in body.values())
    if isinstance(body, list):
        return any(anywhere_in(value, wanted) for value in body)
    return wanted in body if isinstance(body, str) else False


def test_the_identifier_that_matched_is_never_rendered(editor: Client, catalogued: Item) -> None:
    """Matched against, never shown.

    The rule binds what is OFFERED rather than what is matched, and the
    difference is load-bearing: matching widely never produces the complaint,
    because one product appearing three times is three rows drawn rather than
    three strings searched.
    """
    answer = editor.get(reverse("items"), {"search": A_BARCODE})

    assert anywhere_in(answer.json(), catalogued.name), "the item is not rendered by its name"
    assert not anywhere_in(answer.json(), A_BARCODE), (
        f"the response carries {A_BARCODE}, the identifier that matched, so a control populated from it "
        "would offer a string nobody recognises instead of the item's name"
    )


def test_and_not_on_the_item_read_on_its_own(editor: Client, catalogued: Item) -> None:
    """The detail endpoint is the other side of the same wire.

    Held separately because the two serializers are separate: a field added to
    one and not the other is exactly how half a rule survives.
    """
    answer = editor.get(reverse("item-detail", args=[catalogued.pk]))

    assert not anywhere_in(json.loads(answer.content), A_BARCODE)
