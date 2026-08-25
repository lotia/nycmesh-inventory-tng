"""Tests for the read API: the catalogue, and resolving a scanned label.

These are what the scanning client fetches before a volunteer ever presses
anything -- the item list it draws, and the label map it caches so a scan in a
basement resolves without a round trip. See decision 0011.
"""

import datetime
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse

from inventory.models import (
    Category,
    Item,
    ItemIdentifier,
    Label,
    Location,
    StockTransaction,
    Volunteer,
)

pytestmark = pytest.mark.django_db


def results(response: Any) -> list[dict[str, Any]]:
    body = response.json()
    return body["results"] if isinstance(body, dict) else body


def stock(client: Client, volunteer: Volunteer, item: Item, location: Location, quantity: str) -> None:
    """Put stock somewhere through the API, so balances are real ledger rows."""
    response = client.post(
        reverse("stock-transactions"),
        data={
            "kind": StockTransaction.Kind.RECEIPT,
            "actor": volunteer.pk,
            "movements": [{"item": item.pk, "quantity": quantity, "to_location": location.pk}],
        },
        content_type="application/json",
    )
    assert response.status_code == 201, response.content


# --------------------------------------------------------------------------
# The item list
# --------------------------------------------------------------------------


def test_items_carry_the_stock_behind_them(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
) -> None:
    """A count beside every item is most of what the mockup asks for."""
    stock(client, volunteer, item, warehouse, "12")

    listed = results(client.get(reverse("items")))

    assert [entry["name"] for entry in listed] == ["LiteBeam"]
    assert listed[0]["balances"] == [{"location": warehouse.pk, "quantity": "12.000"}]
    assert listed[0]["unit_of_measure"] == Item.UnitOfMeasure.EACH


def test_an_item_with_no_stock_anywhere_still_appears(client: Client, item: Item) -> None:
    """Zero is a count, and an item you cannot see is one you cannot receive."""
    listed = results(client.get(reverse("items")))
    assert listed[0]["balances"] == []


def test_balances_are_ordered_by_location_id(
    client: Client,
    volunteer: Volunteer,
    item: Item,
    warehouse: Location,
) -> None:
    """By the id column, not the relation, for the reason ITEMS gives.

    This annexe is named so that the two orders disagree.
    """
    annexe = Location.objects.create(name="0 Annexe", kind=Location.Kind.WAREHOUSE)
    stock(client, volunteer, item, warehouse, "5")
    stock(client, volunteer, item, annexe, "2")

    balances = results(client.get(reverse("items")))[0]["balances"]

    assert [entry["location"] for entry in balances] == [warehouse.pk, annexe.pk]


def test_an_items_labels_are_its_packaging(client: Client, item: Item) -> None:
    """The packaging chips, in quantity order."""
    Label.objects.create(code="PACKET0000", item=item, quantity=Decimal("100"))
    Label.objects.create(code="S1NG130000", item=item)

    labels = results(client.get(reverse("items")))[0]["labels"]

    assert labels == [
        {"code": "S1NG130000", "quantity": "1.000"},
        {"code": "PACKET0000", "quantity": "100.000"},
    ]


def test_packaging_is_the_distinct_quantities_not_the_sticker_count(
    client: Client,
    item: Item,
    category: Category,
) -> None:
    """Two hundred packets carry two hundred labels and offer two chips.

    The chips are the *packaging* (decision 0011 section 5), so a repeated
    quantity is one choice however many stickers are printed -- and the
    deduplication is per item, which is what a single prefetch across the whole
    page could otherwise get wrong.
    """
    other = Item.objects.create(name="Zip Ties", category=category)
    for number in range(3):
        Label.objects.create(code=f"S00000000{number}", item=item)
        Label.objects.create(code=f"P00000000{number}", item=item, quantity=Decimal("100"))
    Label.objects.create(code="Z000000000", item=other)

    listed = {entry["name"]: entry["labels"] for entry in results(client.get(reverse("items")))}

    assert [entry["quantity"] for entry in listed["LiteBeam"]] == ["1.000", "100.000"]
    assert [entry["quantity"] for entry in listed["Zip Ties"]] == ["1.000"]


def test_a_revoked_label_is_not_offered_as_packaging(client: Client, item: Item) -> None:
    Label.objects.create(
        code="FADED00000",
        item=item,
        quantity=Decimal("100"),
        revoked_at=datetime.datetime(2026, 8, 19, tzinfo=datetime.UTC),
    )
    assert results(client.get(reverse("items")))[0]["labels"] == []


def test_items_can_be_searched_by_name(client: Client, item: Item, category: Category) -> None:
    Item.objects.create(name="Zip Ties", category=category)
    assert [entry["name"] for entry in results(client.get(reverse("items"), {"search": "beam"}))] == ["LiteBeam"]


def anywhere_in(body: Any, wanted: str) -> bool:
    """Whether ``wanted`` appears anywhere in a decoded JSON response.

    A recursive walk rather than a list of fields that may not carry it,
    because a list of fields is a promise about the fields that exist today and
    the leak this guards against arrives as a new one.

    A substring test at the leaf, not equality. The likeliest shape of the leak
    is a composed label -- ``"LiteBeam (litebeam)"`` -- which is a string equal
    to neither of the two things it is made of.
    """
    if isinstance(body, str):
        return wanted in body
    if isinstance(body, dict):
        return any(anywhere_in(value, wanted) for value in body.values())
    if isinstance(body, list):
        return any(anywhere_in(value, wanted) for value in body)
    return False


def test_the_item_list_never_answers_with_an_identifier_value(client: Client, item: Item) -> None:
    """Decision 0026's rule 5: search identifiers, display items.

    Nothing violates this today -- `ItemIdentifier` is imported by no
    serializer and no view. This is the guard for when something searches them,
    because the natural next step is to annotate the value that matched so the
    interface can say why a row appeared, and an annotation rendered as an
    option label is one product offered under three spellings.

    The mis-cased spelling is the point: it is one a pick-list must never show,
    and it is distinct from the item's name, so finding it in the response
    means it came from the identifier and nowhere else.
    """
    ItemIdentifier.objects.create(item=item, kind=ItemIdentifier.Kind.ALIAS, value="litebeam")

    body = client.get(reverse("items"), {"search": "beam"}).json()

    assert anywhere_in(body, "LiteBeam"), "the item's own name is what a pick-list draws"
    assert not anywhere_in(body, "litebeam"), "an identifier value reached the item list"


def test_items_can_be_narrowed_to_a_category(client: Client, item: Item, category: Category) -> None:
    fibre = Category.objects.create(name="Fibre")
    Item.objects.create(name="Pigtail", category=fibre)

    listed = results(client.get(reverse("items"), {"category": fibre.pk}))

    assert [entry["name"] for entry in listed] == ["Pigtail"]


def test_a_retired_item_is_not_offered(client: Client, item: Item, category: Category) -> None:
    """A retired item is not something to add to a cart; see inventory-tng-6c7."""
    Item.objects.create(name="Discontinued Radio", category=category, active=False)
    assert [entry["name"] for entry in results(client.get(reverse("items")))] == ["LiteBeam"]


def test_the_item_list_requires_authentication(client: Client) -> None:
    client.logout()
    assert client.get(reverse("items")).status_code in (401, 403)


# --------------------------------------------------------------------------
# Locations and categories
# --------------------------------------------------------------------------


def test_locations_are_listed(client: Client, warehouse: Location, custody: Location) -> None:
    listed = results(client.get(reverse("locations")))
    assert {entry["name"] for entry in listed} == {"131 Broome", "Sean"}


def test_a_retired_location_is_not_offered(client: Client, warehouse: Location) -> None:
    Location.objects.create(name="Old Hub", kind=Location.Kind.HUB, active=False)
    assert [entry["name"] for entry in results(client.get(reverse("locations")))] == ["131 Broome"]


def test_locations_can_be_narrowed_to_a_kind(client: Client, warehouse: Location, custody: Location) -> None:
    listed = results(client.get(reverse("locations"), {"kind": Location.Kind.VOLUNTEER_CUSTODY}))
    assert [entry["name"] for entry in listed] == ["Sean"]


def test_categories_are_listed(client: Client, category: Category) -> None:
    assert [entry["name"] for entry in results(client.get(reverse("categories")))] == ["Radios"]


# --------------------------------------------------------------------------
# Resolving a scanned code
# --------------------------------------------------------------------------


def resolve(client: Client, code: str) -> Any:
    return client.get(reverse("label-resolve", args=[code]))


def test_a_scanned_code_resolves_to_its_item(client: Client, item: Item) -> None:
    Label.objects.create(code="7QK3M2XV9A", item=item, quantity=Decimal("100"))

    body = resolve(client, "7QK3M2XV9A").json()

    assert body == {
        "code": "7QK3M2XV9A",
        "kind": "item",
        "quantity": "100.000",
        "revoked_at": None,
        "item": item.pk,
        "location": None,
    }


def test_a_wall_code_resolves_to_its_location(client: Client, warehouse: Location) -> None:
    """The wall code: where is this stock moving from?"""
    Label.objects.create(code="WA1132XKTZ", location=warehouse, quantity=None)

    body = resolve(client, "WA1132XKTZ").json()

    assert body["kind"] == "location"
    assert body["location"] == warehouse.pk
    assert body["item"] is None
    # Null rather than the sentinel 1 decision 0011 section 5 once pinned: a
    # wall code stands for no quantity of anything, and every client would
    # otherwise carry the convention that 1 means "not applicable".
    assert body["quantity"] is None


@pytest.mark.parametrize("typed", ["7qk3m2xv9a", "  7QK3M2XV9A  ", "  7Qk3M2Xv9A  "])
def test_a_code_resolves_however_it_was_typed(client: Client, item: Item, typed: str) -> None:
    Label.objects.create(code="7QK3M2XV9A", item=item)
    assert resolve(client, typed).status_code == 200


@pytest.mark.parametrize(
    ("typed", "stored"), [("I234567890", "1234567890"), ("L234567890", "1234567890"), ("O234567890", "0234567890")]
)
def test_letters_people_get_wrong_are_folded(client: Client, item: Item, typed: str, stored: str) -> None:
    """The letters a Crockford code never contains, so the fold is safe."""
    Label.objects.create(code=stored, item=item)
    assert resolve(client, typed).status_code == 200


def test_an_unknown_code_is_a_typed_404(client: Client) -> None:
    """A code naming nothing is a 404 carrying a detail, not an empty 200."""
    response = resolve(client, "ZZZZZZZZZZ")
    assert response.status_code == 404
    assert response.json()["detail"]


def test_a_revoked_label_still_says_what_it_pointed_at(client: Client, item: Item) -> None:
    """A superseded sticker still resolves; the client is told it is retired."""
    Label.objects.create(
        code="FADED00000",
        item=item,
        revoked_at=datetime.datetime(2026, 8, 19, tzinfo=datetime.UTC),
    )

    response = resolve(client, "FADED00000")

    assert response.status_code == 200
    assert response.json()["revoked_at"] is not None
    assert response.json()["item"] == item.pk


# --------------------------------------------------------------------------
# The label map the client caches
# --------------------------------------------------------------------------


def test_every_live_label_is_listed_for_caching(client: Client, item: Item, warehouse: Location) -> None:
    Label.objects.create(code="AAA1110000", item=item, quantity=Decimal("100"))
    Label.objects.create(code="BBB2220000", location=warehouse, quantity=None)

    listed = results(client.get(reverse("labels")))

    assert [entry["code"] for entry in listed] == ["AAA1110000", "BBB2220000"]
    assert listed[0]["quantity"] == "100.000"


def test_the_label_map_is_not_paginated(client: Client, item: Item) -> None:
    """A bare list, not a page envelope."""
    for number in range(3):
        Label.objects.create(code=f"C0DE00000{number}", item=item)
    assert isinstance(client.get(reverse("labels")).json(), list)


def test_a_revoked_label_is_not_in_the_map(client: Client, item: Item) -> None:
    Label.objects.create(
        code="FADED00000",
        item=item,
        revoked_at=datetime.datetime(2026, 8, 19, tzinfo=datetime.UTC),
    )
    assert results(client.get(reverse("labels"))) == []


def test_a_code_stored_in_any_other_form_is_canonicalised(client: Client, item: Item) -> None:
    """Why the alphabet excludes I, L, O and U in the first place.

    A code carrying one of them would be unresolvable for the life of the
    physical object, because the resolver folds the very characters it holds.
    Normalising on write closes that: whatever the admin or the planned import
    stores, the code and the scan agree. What stops such a code reaching the
    column at all is `label_code_is_crockford_base32` -- so this writes one
    that folds to a well-formed code, which is the case normalisation is for.
    """
    label = Label.objects.create(code="wall01lo23", item=item)

    assert label.code == "WA11011023"
    assert resolve(client, "wall01lo23").status_code == 200
    assert resolve(client, "WA11011023").status_code == 200


def test_the_cached_map_omits_a_field_it_would_always_repeat(client: Client, item: Item) -> None:
    """The map holds only live labels, so revoked_at could only say null."""
    Label.objects.create(code="AAA1110000", item=item)

    entry = results(client.get(reverse("labels")))[0]

    assert "revoked_at" not in entry
    assert set(entry) == {
        "code",
        "kind",
        "quantity",
        "item",
        "location",
        "item_name",
        "unit_of_measure",
    }


def test_the_cached_map_carries_what_a_cart_line_needs(client: Client, item: Item) -> None:
    """Otherwise the client holds the whole catalogue too, which is paginated.

    Forty bytes a row here against four round trips and eighty kilobytes of
    balances and labels for three fields per item.
    """
    Label.objects.create(code="AAA1110000", item=item)

    entry = results(client.get(reverse("labels")))[0]

    assert entry["item_name"] == item.name
    assert entry["unit_of_measure"] == item.unit_of_measure


def test_a_location_label_in_the_map_names_no_item(client: Client, warehouse: Location) -> None:
    """A wall code sets where the batch is; there is no item to name."""
    Label.objects.create(code="WA11110000", location=warehouse, quantity=None)

    entry = next(row for row in results(client.get(reverse("labels"))) if row["code"] == "WA11110000")

    assert entry["item_name"] is None
    assert entry["unit_of_measure"] is None


def test_the_map_does_not_query_once_per_label(
    client: Client,
    item: Item,
    django_assert_max_num_queries: Any,
) -> None:
    """The item is joined, not walked: a few hundred rows is one response."""
    for index in range(5):
        Label.objects.create(code=f"AAA111000{index}", item=item)

    with django_assert_max_num_queries(6):
        client.get(reverse("labels"))
