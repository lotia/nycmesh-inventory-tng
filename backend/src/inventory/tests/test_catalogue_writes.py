"""Tests for the catalogue write API: what an administrator may change.

Decision 0012 reserves every operation that edits what is already recorded for
somebody identified, and decision 0014 point 2 puts those operations in this
API rather than in a second application. So each of these endpoints is read by
one population and written by another, and most of what is worth testing is
the seam between them: a volunteer gets a refusal, not a hidden button.

The read side of the same endpoints is in test_catalogue_api.py.
"""

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from inventory.models import Category, Item, Label, Location, Volunteer
from inventory.tests.helpers import patch, post

pytestmark = pytest.mark.django_db

# --------------------------------------------------------------------------
# Who may write
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("items", {"name": "Cable tie", "category": None}),
        ("locations", {"name": "Shelf 3", "kind": Location.Kind.SHELF}),
        ("categories", {"name": "Fibre"}),
        ("labels", {"quantity": "1"}),
    ],
)
def test_a_volunteer_is_refused_every_catalogue_create(
    client: Client,
    category: Category,
    name: str,
    body: dict[str, Any],
) -> None:
    """A refusal, not a hidden button. Decision 0014 point 2."""
    if name == "items":
        body = {**body, "category": category.pk}
    response = post(client, name, body)
    assert response.status_code == 403, response.content
    assert "administrators" in response.json()["detail"]


def test_a_volunteer_is_refused_an_edit(client: Client, item: Item) -> None:
    response = patch(client, "item-detail", {"name": "Something else"}, item.pk)
    assert response.status_code == 403, response.content


def test_a_volunteer_may_still_read_what_they_may_not_write(client: Client, item: Item) -> None:
    """Gating the writes must not close the reads that share the endpoint."""
    assert client.get(reverse("items")).status_code == 200
    assert client.get(reverse("item-detail", args=[item.pk])).status_code == 200


def test_a_catalogue_write_is_json_and_not_a_form(editor: Client, category: Category) -> None:
    """A form body is refused rather than misread; see DEFAULT_PARSER_CLASSES.

    DRF reads a key missing from a form body as ``false`` for a boolean, so a
    form-encoded create arrives carrying ``active=false`` and retires the row
    the administrator has just added -- silently, and out of the very pick-list
    they were adding to.
    """
    response = editor.post(reverse("items"), data={"name": "Formy", "category": category.pk})

    assert response.status_code == 415, response.content
    assert not Item.objects.filter(name="Formy").exists()


def test_a_label_write_is_json_and_not_a_form(editor: Client, item: Item) -> None:
    """The same refusal, on the one endpoint that reads the raw body itself.

    ``LabelSerializer`` asks the submitted body whether it carries a code, and
    a form body is a ``QueryDict`` holding a list per key -- so a form-encoded
    write reaches that check as a shape nothing else here ever sees.
    """
    response = editor.post(reverse("labels"), data={"item": item.pk, "quantity": "5"})

    assert response.status_code == 415, response.content


# --------------------------------------------------------------------------
# Items
# --------------------------------------------------------------------------


def test_an_administrator_creates_an_item(editor: Client, category: Category) -> None:
    response = post(
        editor,
        "items",
        {"name": "LiteBeam 5AC", "category": category.pk, "minimum_stock": "2", "reorder_quantity": "10"},
    )
    assert response.status_code == 201, response.content
    assert Item.objects.filter(name="LiteBeam 5AC").exists()


def test_a_created_item_carries_the_fields_the_list_leaves_out(editor: Client, category: Category) -> None:
    """The list is a hundred rows on a phone; the item itself is one row."""
    response = post(
        editor,
        "items",
        {
            "name": "SXTsq",
            "category": category.pk,
            "description": "Small sector, 60 degrees.",
            "attributes": {"gain_dbi": 16},
        },
    )
    assert response.status_code == 201, response.content
    assert response.json()["description"] == "Small sector, 60 degrees."
    assert response.json()["attributes"] == {"gain_dbi": 16}
    assert "description" not in editor.get(reverse("items")).json()["results"][0]


def test_an_administrator_edits_an_item(editor: Client, item: Item) -> None:
    response = patch(editor, "item-detail", {"minimum_stock": "5"}, item.pk)
    assert response.status_code == 200, response.content
    item.refresh_from_db()
    assert item.minimum_stock == 5


def test_retiring_an_item_takes_it_out_of_the_pick_list(editor: Client, item: Item) -> None:
    """Deactivation, not deletion: the ledger refers to it for as long as it exists."""
    assert patch(editor, "item-detail", {"active": False}, item.pk).status_code == 200
    assert Item.objects.filter(pk=item.pk).exists()
    assert editor.get(reverse("items")).json()["results"] == []


def test_an_administrator_can_list_what_they_withdrew(editor: Client, item: Item) -> None:
    """The way to arrive at a retired row without already knowing its id.

    Editing one was reachable only through the Django admin otherwise, which
    decision 0014 point 4 keeps for a broken deployment rather than for repair.
    """
    patch(editor, "item-detail", {"active": False}, item.pk)

    withdrawn = editor.get(reverse("items"), {"withdrawn": "true"}).json()["results"]

    assert [row["id"] for row in withdrawn] == [item.pk]


def test_listing_the_withdrawn_leaves_the_pick_list_alone(editor: Client, item: Item) -> None:
    """An administrator filling a batch must see what a volunteer sees.

    Widening the default would offer them stock nobody else can pick, which is
    why this is a question asked on purpose rather than a wider answer.
    """
    patch(editor, "item-detail", {"active": False}, item.pk)

    assert editor.get(reverse("items")).json()["results"] == []


def test_a_volunteer_may_not_ask_what_was_withdrawn(client: Client, item: Item) -> None:
    """Refused rather than quietly answered with the offered rows."""
    assert client.get(reverse("items"), {"withdrawn": "true"}).status_code == 403


def test_withdrawn_false_is_the_ordinary_list(editor: Client, item: Item) -> None:
    """The schema says boolean, so a generated client sends false for default."""
    listed = editor.get(reverse("items"), {"withdrawn": "false"}).json()["results"]

    assert [row["id"] for row in listed] == [item.pk]


def test_withdrawn_takes_a_boolean_or_nothing(editor: Client) -> None:
    """A value nobody meant is a mistake worth naming, not a silent full list."""
    refused = editor.get(reverse("items"), {"withdrawn": "maybe"})

    assert refused.status_code == 400
    assert "withdrawn" in str(refused.json())


def test_a_volunteer_asking_nonsense_is_told_it_is_nonsense(client: Client) -> None:
    """A typo is not an occasion to explain an administrators-only parameter."""
    assert client.get(reverse("items"), {"withdrawn": "maybe"}).status_code == 400


def test_a_merged_volunteer_can_be_listed_the_same_way(editor: Client, volunteer: Volunteer) -> None:
    """Same question of the pick-list that decision 0015 hands out pks from."""
    keeper = Volunteer.objects.create(display_name="The one to keep")
    patch(editor, "volunteer-detail", {"merged_into": keeper.pk}, volunteer.pk)

    withdrawn = editor.get(reverse("volunteers"), {"withdrawn": "true"}).json()["results"]

    assert [row["id"] for row in withdrawn] == [volunteer.pk]


def test_a_retired_location_can_be_listed_the_same_way(editor: Client, warehouse: Location) -> None:
    patch(editor, "location-detail", {"active": False}, warehouse.pk)

    withdrawn = editor.get(reverse("locations"), {"withdrawn": "true"}).json()["results"]

    assert warehouse.pk in [row["id"] for row in withdrawn]


def test_a_retired_item_is_still_editable_by_an_administrator(editor: Client, item: Item) -> None:
    """Putting one back is the reason the retired row stays reachable."""
    patch(editor, "item-detail", {"active": False}, item.pk)
    assert editor.get(reverse("item-detail", args=[item.pk])).status_code == 200
    assert patch(editor, "item-detail", {"active": True}, item.pk).status_code == 200


def test_a_retired_item_is_hidden_from_everybody_else(editor: Client, client: Client, item: Item) -> None:
    """The detail endpoint offers exactly what the list beside it offers."""
    patch(editor, "item-detail", {"active": False}, item.pk)
    assert client.get(reverse("item-detail", args=[item.pk])).status_code == 404


@pytest.mark.parametrize(
    "field_and_value",
    [{"reorder_quantity": "0"}, {"minimum_stock": "-1"}],
    ids=["reorder-none", "negative-minimum"],
)
def test_an_item_the_constraints_refuse_is_a_400(
    editor: Client,
    category: Category,
    field_and_value: dict[str, Any],
) -> None:
    """A check constraint answers with a 500 naming nothing; the serializer names the field."""
    response = post(editor, "items", {"name": "Broken", "category": category.pk, **field_and_value})
    assert response.status_code == 400, response.content


# --------------------------------------------------------------------------
# Locations and categories
# --------------------------------------------------------------------------


def test_an_administrator_creates_a_location(editor: Client) -> None:
    response = post(editor, "locations", {"name": "Shelf 1", "kind": Location.Kind.SHELF})
    assert response.status_code == 201, response.content


def test_a_custody_location_without_a_holder_is_a_400(editor: Client) -> None:
    """held_by is set if and only if it is a custody location; the database says so."""
    response = post(editor, "locations", {"name": "Nobody", "kind": Location.Kind.VOLUNTEER_CUSTODY})
    assert response.status_code == 400, response.content


def test_a_custody_location_with_a_holder_is_created(editor: Client, volunteer: Volunteer) -> None:
    response = post(
        editor,
        "locations",
        {"name": "Sean", "kind": Location.Kind.VOLUNTEER_CUSTODY, "held_by": volunteer.pk},
    )
    assert response.status_code == 201, response.content


def test_only_a_custody_location_names_a_volunteer(editor: Client, volunteer: Volunteer) -> None:
    """The other half of location_held_by_iff_custody."""
    response = post(
        editor,
        "locations",
        {"name": "Shelf 2", "kind": Location.Kind.SHELF, "held_by": volunteer.pk},
    )
    assert response.status_code == 400, response.content


def test_retiring_a_location_takes_it_out_of_the_pick_list(editor: Client, warehouse: Location) -> None:
    assert patch(editor, "location-detail", {"active": False}, warehouse.pk).status_code == 200
    assert editor.get(reverse("locations")).json()["results"] == []


def test_a_retired_location_is_hidden_from_everybody_but_an_administrator(
    editor: Client,
    client: Client,
    warehouse: Location,
) -> None:
    patch(editor, "location-detail", {"active": False}, warehouse.pk)
    url = reverse("location-detail", args=[warehouse.pk])
    assert editor.get(url).status_code == 200
    assert client.get(url).status_code == 404


@pytest.mark.parametrize(
    ("collection", "detail"),
    [("categories", "category-detail"), ("locations", "location-detail")],
)
def test_a_parent_that_would_make_a_loop_is_a_400(
    editor: Client,
    category: Category,
    warehouse: Location,
    collection: str,
    detail: str,
) -> None:
    """Both trees are guarded by one database trigger; both mirror it.

    Before the write API existed no request could set `parent` at all, so the
    trigger had never had to answer one.
    """
    root = {"categories": category, "locations": warehouse}[collection]
    child = post(editor, collection, {"name": "Below", "parent": root.pk, "kind": Location.Kind.SHELF})
    assert child.status_code == 201, child.content
    response = patch(editor, detail, {"parent": child.json()["id"]}, root.pk)
    assert response.status_code == 400, response.content


def test_a_row_cannot_be_its_own_parent(editor: Client, category: Category) -> None:
    assert patch(editor, "category-detail", {"parent": category.pk}, category.pk).status_code == 400


def test_custody_is_recorded_against_somebody_the_list_still_offers(
    editor: Client,
    volunteer: Volunteer,
) -> None:
    """Otherwise the merge grows a second generation of the duplicate it removed."""
    duplicate = Volunteer.objects.create(display_name="sean", merged_into=volunteer)
    response = post(
        editor,
        "locations",
        {"name": "sean", "kind": Location.Kind.VOLUNTEER_CUSTODY, "held_by": duplicate.pk},
    )
    assert response.status_code == 400, response.content


def _holding(volunteer: Volunteer, name: str = "The blue van") -> Location:
    """A volunteer with an active custody location, which is the whole case."""
    return Location.objects.create(
        name=name,
        kind=Location.Kind.VOLUNTEER_CUSTODY,
        held_by=volunteer,
    )


def test_merging_somebody_who_holds_custody_is_refused(
    editor: Client,
    volunteer: Volunteer,
) -> None:
    """The guard was on naming a holder, not on the record leaving the list."""
    survivor = Volunteer.objects.create(display_name="The one to keep")
    held = _holding(volunteer)

    refused = patch(editor, "volunteer-detail", {"merged_into": survivor.pk}, volunteer.pk)

    assert refused.status_code == 400, refused.content
    # Named, so the answer is actionable rather than a rule the caller has to
    # go and read.
    assert held.name in str(refused.json())


def test_retiring_somebody_who_holds_custody_is_refused(
    editor: Client,
    volunteer: Volunteer,
) -> None:
    """The other route to the same state, which is why the guard is here."""
    _holding(volunteer)

    refused = patch(editor, "volunteer-detail", {"active": False}, volunteer.pk)

    assert refused.status_code == 400, refused.content


def test_the_survivor_already_holding_one_makes_no_difference(
    editor: Client,
    volunteer: Volunteer,
) -> None:
    """The case that rules out repointing; the serializer says why."""
    survivor = Volunteer.objects.create(display_name="The one to keep")
    _holding(survivor, "The white van")
    _holding(volunteer)

    refused = patch(editor, "volunteer-detail", {"merged_into": survivor.pk}, volunteer.pk)

    assert refused.status_code == 400, refused.content


def test_a_volunteer_holding_nothing_still_merges(editor: Client, volunteer: Volunteer) -> None:
    """The guard is about custody, not about merging."""
    survivor = Volunteer.objects.create(display_name="The one to keep")

    merged = patch(editor, "volunteer-detail", {"merged_into": survivor.pk}, volunteer.pk)

    assert merged.status_code == 200, merged.content


def test_retiring_the_custody_location_first_unblocks_the_merge(
    editor: Client,
    volunteer: Volunteer,
) -> None:
    """What the refusal tells them to do, and that doing it works."""
    survivor = Volunteer.objects.create(display_name="The one to keep")
    held = _holding(volunteer)

    patch(editor, "location-detail", {"active": False}, held.pk)
    merged = patch(editor, "volunteer-detail", {"merged_into": survivor.pk}, volunteer.pk)

    assert merged.status_code == 200, merged.content


def test_a_retired_custody_location_cannot_be_revived_for_a_merged_holder(
    editor: Client,
    volunteer: Volunteer,
) -> None:
    """The three steps the refusal's own advice would otherwise open.

    Retire the location, merge the volunteer -- now allowed, since only active
    ones are checked -- then put the location back. The last step names no
    holder, so the field validator never sees one, and the trigger returns
    early because held_by is unchanged.
    """
    survivor = Volunteer.objects.create(display_name="The one to keep")
    held = _holding(volunteer)
    patch(editor, "location-detail", {"active": False}, held.pk)
    patch(editor, "volunteer-detail", {"merged_into": survivor.pk}, volunteer.pk)

    revived = patch(editor, "location-detail", {"active": True}, held.pk)

    assert revived.status_code == 400, revived.content


def test_a_retired_custody_location_comes_back_when_somebody_holds_it(
    editor: Client,
    volunteer: Volunteer,
) -> None:
    """The way out the refusal names: say who holds the stock now."""
    survivor = Volunteer.objects.create(display_name="The one to keep")
    held = _holding(volunteer)
    patch(editor, "location-detail", {"active": False}, held.pk)
    patch(editor, "volunteer-detail", {"merged_into": survivor.pk}, volunteer.pk)

    revived = patch(editor, "location-detail", {"active": True, "held_by": survivor.pk}, held.pk)

    assert revived.status_code == 200, revived.content


def test_a_retired_custody_location_comes_back_for_a_holder_still_offered(
    editor: Client,
    volunteer: Volunteer,
) -> None:
    """The guard is about a withdrawn holder, not about reviving."""
    held = _holding(volunteer)
    patch(editor, "location-detail", {"active": False}, held.pk)

    revived = patch(editor, "location-detail", {"active": True}, held.pk)

    assert revived.status_code == 200, revived.content


def test_the_detail_endpoint_shows_the_same_packaging_as_the_list(editor: Client, item: Item) -> None:
    """A revoked sticker is missing from the detail as well as from the list.

    The chips a client renders come from either, and LabelManager.live() says
    so for exactly this reason; ITEMS is what makes the two agree.
    """
    Label.objects.create(code="KEPT110000", item=item, quantity=1)
    Label.objects.create(code="DEAD110000", item=item, quantity=5, revoked_at=timezone.now())
    Label.objects.create(code="SAME110000", item=item, quantity=1)
    listed = editor.get(reverse("items")).json()["results"][0]["labels"]
    alone = editor.get(reverse("item-detail", args=[item.pk])).json()["labels"]
    assert alone == listed
    assert [label["quantity"] for label in alone] == ["1.000"]


def test_editing_an_item_answers_with_the_packaging_the_list_shows(editor: Client, item: Item) -> None:
    """The write path renders the row the same way the read paths do.

    DRF empties the prefetch cache before serialising what it just wrote, so
    without DetailView.update this comes back through the plain related
    managers -- every sticker, revoked ones included.
    """
    Label.objects.create(code="KEPT110000", item=item, quantity=1)
    Label.objects.create(code="DEAD110000", item=item, quantity=5, revoked_at=timezone.now())
    written = patch(editor, "item-detail", {"minimum_stock": "3"}, item.pk).json()
    assert written["labels"] == editor.get(reverse("item-detail", args=[item.pk])).json()["labels"]
    assert [label["quantity"] for label in written["labels"]] == ["1.000"]


def test_an_administrator_nests_a_category(editor: Client, category: Category) -> None:
    response = post(editor, "categories", {"name": "Pigtails", "parent": category.pk})
    assert response.status_code == 201, response.content
    assert Category.objects.get(name="Pigtails").parent == category


def test_a_category_moves_under_one_that_is_not_below_it(editor: Client, category: Category) -> None:
    """The ordinary re-parent, which the loop check has to let through."""
    sibling = Category.objects.create(name="Fibre")
    assert patch(editor, "category-detail", {"parent": sibling.pk}, category.pk).status_code == 200
    category.refresh_from_db()
    assert category.parent == sibling


def test_an_administrator_renames_a_category(editor: Client, category: Category) -> None:
    assert patch(editor, "category-detail", {"name": "Radio"}, category.pk).status_code == 200
    category.refresh_from_db()
    assert category.name == "Radio"


# --------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------


def test_an_administrator_prints_a_label_for_an_item(editor: Client, item: Item) -> None:
    """No code in the request: printing a label is asking the server to mint one."""
    response = post(editor, "labels", {"item": item.pk, "quantity": "100"})
    assert response.status_code == 201, response.content
    assert response.json()["kind"] == "item"


def test_a_label_pointing_at_both_or_neither_is_a_400(editor: Client, item: Item, warehouse: Location) -> None:
    assert post(editor, "labels", {}).status_code == 400
    both = post(editor, "labels", {"item": item.pk, "location": warehouse.pk})
    assert both.status_code == 400, both.content


def test_a_location_label_may_not_stand_for_more_than_one(editor: Client, warehouse: Location) -> None:
    response = post(editor, "labels", {"location": warehouse.pk, "quantity": "5"})
    assert response.status_code == 400, response.content


@pytest.mark.parametrize("quantity", ["0", "-5"])
def test_a_label_standing_for_nothing_is_a_400(editor: Client, item: Item, quantity: str) -> None:
    """The third of Label's check constraints; the other two were mirrored first."""
    response = post(editor, "labels", {"item": item.pk, "quantity": quantity})
    assert response.status_code == 400, response.content


def test_a_printed_code_cannot_be_changed_out_from_under_the_sticker(editor: Client, item: Item) -> None:
    """The code is the label's identity, and the sticker is already on a shelf.

    Refused rather than quietly ignored: `code` is read-only in the schema, so
    DRF would drop it, and a client told 200 by a change that did not happen
    has no way to find out.
    """
    label = Label.objects.create(code="AB12340000", item=item)
    response = patch(editor, "label-resolve", {"code": "CD56780000"}, label.code)
    assert response.status_code == 400, response.content
    assert "code" in response.json()
    label.refresh_from_db()
    assert label.code == "AB12340000"
    assert editor.get(reverse("label-resolve", args=["CD56780000"])).status_code == 404


def test_a_correction_may_repeat_the_code_it_is_not_changing(editor: Client, item: Item) -> None:
    """A client sending the whole row back is correcting it, not renaming it."""
    label = Label.objects.create(code="AB12340000", item=item)
    assert patch(editor, "label-resolve", {"code": "ab12340000", "quantity": "4"}, label.code).status_code == 200
    label.refresh_from_db()
    assert label.quantity == 4


def test_a_label_is_corrected_rather_than_replaced(editor: Client, item: Item) -> None:
    """PUT is not offered: a replacement omitting `revoked` would silently keep it."""
    label = Label.objects.create(code="EF90120000", item=item)
    response = editor.put(
        reverse("label-resolve", args=[label.code]),
        data={"code": label.code, "item": item.pk, "quantity": "1"},
        content_type="application/json",
    )
    assert response.status_code == 405, response.content


def test_revoking_a_label_takes_it_out_of_the_cached_map(editor: Client, item: Item) -> None:
    label = Label.objects.create(code="FADED10000", item=item)
    response = patch(editor, "label-resolve", {"revoked": True}, label.code)
    assert response.status_code == 200, response.content
    label.refresh_from_db()
    assert label.revoked_at is not None
    assert editor.get(reverse("labels")).json() == []


def test_a_revoked_label_still_resolves(editor: Client, item: Item) -> None:
    """The sticker is superseded; refusing the scan would block a volunteer."""
    label = Label.objects.create(code="FADED20000", item=item)
    patch(editor, "label-resolve", {"revoked": True}, label.code)
    resolved = editor.get(reverse("label-resolve", args=[label.code]))
    assert resolved.status_code == 200
    assert resolved.json()["revoked_at"] is not None


def test_revoking_the_wrong_label_is_undone_rather_than_reprinted(editor: Client, item: Item) -> None:
    label = Label.objects.create(code="00PS100000", item=item)
    patch(editor, "label-resolve", {"revoked": True}, label.code)
    assert patch(editor, "label-resolve", {"revoked": False}, label.code).status_code == 200
    label.refresh_from_db()
    assert label.revoked_at is None


def test_a_volunteer_may_not_revoke_a_label(client: Client, item: Item) -> None:
    label = Label.objects.create(code="M1NE100000", item=item)
    assert patch(client, "label-resolve", {"revoked": True}, label.code).status_code == 403


def test_the_revocation_timestamp_is_the_servers(editor: Client, item: Item) -> None:
    """``LabelSerializer`` says why the clock is the server's here."""
    label = Label.objects.create(code="C10CK10000", item=item)
    patch(editor, "label-resolve", {"revoked": True, "revoked_at": "2099-01-01T00:00:00Z"}, label.code)
    label.refresh_from_db()
    assert label.revoked_at is not None
    assert label.revoked_at.year != 2099


def test_an_item_label_may_not_be_printed_without_a_quantity(editor: Client, item: Item) -> None:
    """The other half of ``label_quantity_iff_item``, mirrored as a 400.

    The column was NOT NULL before that constraint, so DRF refused an explicit
    null on its own; widening it for location labels took that refusal away
    and left the request reaching the database as a 500.
    """
    response = post(editor, "labels", {"item": item.pk, "quantity": None})

    assert response.status_code == 400
    assert "quantity" in str(response.json())


def test_a_wall_label_is_printed_without_a_quantity(editor: Client, warehouse: Location) -> None:
    """And the ordinary case it exists to leave alone: a bare location label."""
    response = post(editor, "labels", {"location": warehouse.pk})

    assert response.status_code == 201
    assert response.json()["quantity"] is None


def test_repointing_a_wall_label_at_an_item_carries_a_quantity(editor: Client, item: Item, warehouse: Location) -> None:
    """Repointing is a correction rather than a reprint, so it has to work."""
    label = Label.objects.create(code="RE90INT000", location=warehouse, quantity=None)

    response = patch(editor, "label-resolve", {"item": item.pk, "location": None, "quantity": "5"}, label.code)

    assert response.status_code == 200
    assert response.json()["quantity"] == "5.000"
