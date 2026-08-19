"""Tests for the catalogue models.

These lean deliberately on the *database* constraints and triggers rather than
on model validation. docs/data-model.md states the invariants where they hold
regardless of which client writes the row, and a test that only exercised
``full_clean()`` would not prove that. Which rules are down there and which
stayed above the API is
docs/decisions/0016-invariants-for-every-writer.md.

For the ``ty: ignore[unresolved-attribute]`` comments below, see
DEVELOPERS.md#typing.
"""

import datetime
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from inventory.models import (
    Category,
    Item,
    ItemIdentifier,
    Label,
    Location,
    Vendor,
    VendorOffer,
    Volunteer,
)

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------
# Category
# --------------------------------------------------------------------------


def test_category_str(category: Category) -> None:
    assert str(category) == "Radios"


def test_category_names_unique_within_a_parent(category: Category) -> None:
    Category.objects.create(name="Pigtails", parent=category)
    with pytest.raises(IntegrityError):
        Category.objects.create(name="Pigtails", parent=category)


def test_root_category_names_are_unique(category: Category) -> None:
    """Two NULL parents must collide -- see docs/data-model.md, NULLS NOT DISTINCT."""
    with pytest.raises(IntegrityError):
        Category.objects.create(name="Radios")


def test_category_cannot_be_its_own_parent(category: Category) -> None:
    """A cycle makes every walk of the tree non-terminating."""
    category.parent = category
    with pytest.raises(IntegrityError):
        category.save()


def test_categories_sharing_a_name_have_a_total_order(category: Category) -> None:
    """A name is unique only within a parent, so the list needs a tie-break.

    Without one PostgreSQL may return tied rows in any order, and a paginated
    list can then show one category twice and never show another -- the same
    reasoning as Volunteer's ordering.
    """
    nested = Category.objects.create(name="Radios", parent=category)
    assert list(Category.objects.filter(name="Radios")) == sorted([category, nested], key=lambda row: row.pk)


# --------------------------------------------------------------------------
# Volunteer
# --------------------------------------------------------------------------


def test_category_cycles_are_rejected_at_any_depth(category: Category) -> None:
    """The guard a check constraint could not give us.

    A constraint can only see a node parented to itself. A -> B -> A and longer
    loops would pass it and then hang the next recursive walk of the tree.
    """
    child = Category.objects.create(name="Pigtails", parent=category)
    grandchild = Category.objects.create(name="SC/APC", parent=child)
    for ancestor in (child, grandchild):
        category.parent = ancestor
        with pytest.raises(IntegrityError), transaction.atomic():
            category.save()


def test_legitimate_nesting_is_unaffected(category: Category) -> None:
    child = Category.objects.create(name="Pigtails", parent=category)
    grandchild = Category.objects.create(name="SC/APC", parent=child)
    assert grandchild.parent is not None
    assert grandchild.parent.parent == category


def test_volunteer_str(volunteer: Volunteer) -> None:
    assert str(volunteer) == "Sean"


def test_volunteers_may_share_an_absent_email() -> None:
    """45% of the historical rows had no email. That must not be a collision."""
    Volunteer.objects.create(display_name="Alice")
    Volunteer.objects.create(display_name="Bob")
    assert Volunteer.objects.filter(email__isnull=True).count() == 2


def test_a_blank_identifier_is_stored_as_absent(volunteer: Volunteer) -> None:
    """Absence is NULL, never "".

    Normalised on the model so the admin and the planned sheet import are held
    to it too, not just the API. Two volunteers who each left the field alone
    would otherwise collide on the partial unique index.
    """
    first = Volunteer.objects.create(display_name="Olivia", email="", slack_id="")
    second = Volunteer.objects.create(display_name="Priya", email="", slack_id="")
    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.email, first.slack_id) == (None, None)
    assert (second.email, second.slack_id) == (None, None)


def test_volunteer_email_unique_when_supplied() -> None:
    Volunteer.objects.create(display_name="Alice", email="a@example.com")
    with pytest.raises(IntegrityError):
        Volunteer.objects.create(display_name="Alice again", email="a@example.com")


def test_volunteer_slack_id_unique_when_supplied() -> None:
    Volunteer.objects.create(display_name="Alice", slack_id="U123")
    with pytest.raises(IntegrityError):
        Volunteer.objects.create(display_name="Alice again", slack_id="U123")


def test_volunteer_cannot_be_merged_into_itself(volunteer: Volunteer) -> None:
    volunteer.merged_into = volunteer
    with pytest.raises(IntegrityError):
        volunteer.save()


def test_merging_preserves_the_duplicate_record(volunteer: Volunteer) -> None:
    """Merging repoints; it does not delete. History has to survive it."""
    duplicate = Volunteer.objects.create(display_name="sean", merged_into=volunteer)
    assert duplicate.pk is not None
    assert volunteer.merged_from.get() == duplicate  # ty: ignore[unresolved-attribute]


@pytest.mark.parametrize("withdrawn", ["merged", "retired"])
def test_a_merge_points_at_somebody_the_list_still_offers(volunteer: Volunteer, withdrawn: str) -> None:
    """Otherwise a chain forms, and -- pointing at a record that has itself
    been merged -- a cycle can be built out of two ordinary merges.
    """
    survivor = Volunteer.objects.create(display_name="Sean B")
    if withdrawn == "merged":
        survivor.merged_into = volunteer
    else:
        survivor.active = False
    survivor.save()

    with pytest.raises(IntegrityError, match="merged or retired"):
        Volunteer.objects.create(display_name="sean", merged_into=survivor)


def test_a_chain_forms_when_the_survivor_is_merged_later(volunteer: Volunteer) -> None:
    """Which is allowed, and is why decision 0015 follows the pointer forward
    rather than reading it once. The rule is about the moment of choosing, so
    merging the record somebody was already merged into is an ordinary merge.
    """
    middle = Volunteer.objects.create(display_name="Sean M")
    Volunteer.objects.create(display_name="sean", merged_into=middle)
    middle.merged_into = volunteer
    middle.save()
    assert Volunteer.objects.selectable().count() == 1


def test_retiring_somebody_who_still_holds_stock_is_allowed(volunteer: Volunteer, custody: Location) -> None:
    """The rule is checked where the naming happens, so the reverse is not
    refused -- it leaves a custody location pointing at a record the pick-list
    no longer offers, and whether that should move the stock to the survivor
    is a decision nobody has taken. Stated once in serializers.py.
    """
    volunteer.active = False
    volunteer.save()

    custody.name = "Sean's flat"
    custody.save()
    custody.refresh_from_db()
    assert custody.held_by == volunteer


# --------------------------------------------------------------------------
# Location
# --------------------------------------------------------------------------


def test_location_str(warehouse: Location) -> None:
    assert str(warehouse) == "131 Broome"


def test_custody_location_requires_a_holder() -> None:
    with pytest.raises(IntegrityError):
        Location.objects.create(name="Sean's flat", kind=Location.Kind.VOLUNTEER_CUSTODY)


def test_non_custody_location_must_not_have_a_holder(volunteer: Volunteer) -> None:
    with pytest.raises(IntegrityError):
        Location.objects.create(name="Basement", kind=Location.Kind.SHELF, held_by=volunteer)


@pytest.mark.parametrize("withdrawn", ["merged", "retired"])
def test_custody_is_recorded_against_somebody_the_list_still_offers(volunteer: Volunteer, withdrawn: str) -> None:
    """A custody location attached to a merged duplicate is the second
    generation of the duplicate the merge existed to remove.
    """
    holder = Volunteer.objects.create(display_name="Sean B")
    if withdrawn == "merged":
        holder.merged_into = volunteer
    else:
        holder.active = False
    holder.save()

    with pytest.raises(IntegrityError, match="merged or retired"):
        Location.objects.create(name="Sean B", kind=Location.Kind.VOLUNTEER_CUSTODY, held_by=holder)


def test_one_custody_location_per_volunteer(volunteer: Volunteer) -> None:
    Location.objects.create(name="Sean", kind=Location.Kind.VOLUNTEER_CUSTODY, held_by=volunteer)
    with pytest.raises(IntegrityError):
        Location.objects.create(name="Sean again", kind=Location.Kind.VOLUNTEER_CUSTODY, held_by=volunteer)


def test_locations_nest(warehouse: Location) -> None:
    room = Location.objects.create(name="Mesh room", kind=Location.Kind.ROOM, parent=warehouse)
    shelf = Location.objects.create(name="Shelf A", kind=Location.Kind.SHELF, parent=room)
    assert shelf.parent is not None
    assert shelf.parent.parent == warehouse


def test_location_cannot_be_its_own_parent(warehouse: Location) -> None:
    """A walk of the location tree has to terminate."""
    warehouse.parent = warehouse
    with pytest.raises(IntegrityError):
        warehouse.save()


def test_location_cycles_are_rejected_at_any_depth(warehouse: Location) -> None:
    room = Location.objects.create(name="Mesh room", kind=Location.Kind.ROOM, parent=warehouse)
    shelf = Location.objects.create(name="Shelf A", kind=Location.Kind.SHELF, parent=room)
    for ancestor in (room, shelf):
        warehouse.parent = ancestor
        with pytest.raises(IntegrityError), transaction.atomic():
            warehouse.save()


def test_locations_sharing_a_name_have_a_total_order(warehouse: Location) -> None:
    """Two rooms really can both be "Shelf 1", so the pick-list needs a
    tie-break for the same reason Category and Volunteer do.
    """
    first = Location.objects.create(name="Shelf 1", kind=Location.Kind.SHELF, parent=warehouse)
    second = Location.objects.create(name="Shelf 1", kind=Location.Kind.SHELF)
    assert list(Location.objects.filter(name="Shelf 1")) == sorted([first, second], key=lambda row: row.pk)


# --------------------------------------------------------------------------
# Item and ItemIdentifier
# --------------------------------------------------------------------------


def test_item_str(item: Item) -> None:
    assert str(item) == "LiteBeam"


def test_item_minimum_stock_cannot_be_negative(category: Category) -> None:
    with pytest.raises(IntegrityError):
        Item.objects.create(name="Bad", category=category, minimum_stock=Decimal("-1"))


def test_item_reorder_quantity_must_be_positive(category: Category) -> None:
    with pytest.raises(IntegrityError):
        Item.objects.create(name="Bad", category=category, reorder_quantity=Decimal("0"))


def test_item_attributes_default_to_an_empty_dict(item: Item) -> None:
    assert item.attributes == {}


def test_item_attributes_are_queryable(category: Category) -> None:
    """The JSONB column exists to be searched, not just stored."""
    Item.objects.create(name="AF24", category=category, attributes={"band": "24GHz"})
    Item.objects.create(name="Cat 6", category=category, attributes={"shielded": False})
    assert Item.objects.filter(attributes__band="24GHz").get().name == "AF24"


def test_quantities_are_decimal(category: Category) -> None:
    """Cable is measured, not counted -- half a metre has to be representable."""
    cable = Item.objects.create(
        name="ToughCable",
        category=category,
        unit_of_measure=Item.UnitOfMeasure.METRE,
        minimum_stock=Decimal("1500.500"),
    )
    cable.refresh_from_db()
    assert cable.minimum_stock == Decimal("1500.500")


def test_identifier_str(item: Item) -> None:
    identifier = ItemIdentifier.objects.create(item=item, kind=ItemIdentifier.Kind.ALIAS, value="litebeam")
    assert str(identifier) == "litebeam (Alias)"


def test_identifier_normalisation_is_done_by_the_database(item: Item) -> None:
    identifier = ItemIdentifier.objects.create(
        item=item,
        kind=ItemIdentifier.Kind.ALIAS,
        value="  Lbe 5ac Gen2  ",
    )
    identifier.refresh_from_db()
    assert identifier.value_normalised == "lbe 5ac gen2"


def test_identifiers_resolve_to_exactly_one_item(item: Item, category: Category) -> None:
    """The failure this model exists to prevent.

    In the sheet, 'archer 7' and 'Archer a7' were different items as far as the
    lookup was concerned, and 41 such strings matched nothing at all.
    """
    other = Item.objects.create(name="Archer A7", category=category)
    ItemIdentifier.objects.create(item=item, kind=ItemIdentifier.Kind.ALIAS, value="Litebeam")
    with pytest.raises(IntegrityError):
        ItemIdentifier.objects.create(item=other, kind=ItemIdentifier.Kind.ALIAS, value="  litebeam ")


def test_differing_identifiers_coexist(item: Item) -> None:
    ItemIdentifier.objects.create(item=item, kind=ItemIdentifier.Kind.MFG_PART, value="LBE-5AC-Gen2")
    ItemIdentifier.objects.create(item=item, kind=ItemIdentifier.Kind.LEGACY_NYCM, value="NYCM-ER-LBEG2")
    assert item.identifiers.count() == 2  # ty: ignore[unresolved-attribute]


# --------------------------------------------------------------------------
# Vendor and VendorOffer
# --------------------------------------------------------------------------


OBSERVED = datetime.date(2026, 8, 18)


def offer(item: Item, vendor: Vendor, **kwargs: object) -> VendorOffer:
    """VendorOffer needs an observation date; no test cares which."""
    return VendorOffer.objects.create(item=item, vendor=vendor, observed_at=OBSERVED, **kwargs)


@pytest.fixture
def vendor() -> Vendor:
    return Vendor.objects.create(name="streakwave")


def test_vendor_str(vendor: Vendor) -> None:
    assert str(vendor) == "streakwave"


def test_offer_str(item: Item, vendor: Vendor) -> None:
    assert str(offer(item, vendor)) == "LiteBeam from streakwave"


def test_several_offers_per_item(item: Item, vendor: Vendor) -> None:
    """The sheet crammed price comparisons into a notes column. This is why."""
    other = Vendor.objects.create(name="b&h")
    offer(
        item,
        vendor,
        unit_price=Decimal("134.44"),
    )
    offer(
        item,
        other,
        unit_price=Decimal("131.41"),
    )
    assert item.offers.count() == 2  # ty: ignore[unresolved-attribute]


def test_only_one_preferred_offer_per_item(item: Item, vendor: Vendor) -> None:
    other = Vendor.objects.create(name="b&h")
    offer(item, vendor, is_preferred=True)
    with pytest.raises(IntegrityError):
        offer(item, other, is_preferred=True)


def test_offer_price_cannot_be_negative(item: Item, vendor: Vendor) -> None:
    with pytest.raises(IntegrityError):
        offer(
            item,
            vendor,
            unit_price=Decimal("-1"),
        )


def test_offer_units_per_order_must_be_positive(item: Item, vendor: Vendor) -> None:
    with pytest.raises(IntegrityError):
        offer(
            item,
            vendor,
            units_per_order=Decimal("0"),
        )


# --------------------------------------------------------------------------
# Label
# --------------------------------------------------------------------------


def test_label_str(item: Item) -> None:
    label = Label.objects.create(code="7QK3M2XV9A", item=item)
    assert str(label) == "7QK3M2XV9A"


def test_label_is_active_until_revoked(item: Item) -> None:
    label = Label.objects.create(code="7QK3M2XV9A", item=item)
    assert label.is_active is True
    label.revoked_at = datetime.datetime(2026, 8, 18, tzinfo=datetime.UTC)
    label.save()
    assert label.is_active is False


def test_label_must_target_something(item: Item) -> None:
    with pytest.raises(IntegrityError):
        Label.objects.create(code="0RPHAN0000")


def test_label_cannot_target_both(item: Item, warehouse: Location) -> None:
    with pytest.raises(IntegrityError):
        Label.objects.create(code="B0TH000000", item=item, location=warehouse)


def test_label_may_target_a_location(warehouse: Location) -> None:
    label = Label.objects.create(code="10CAT10N01", location=warehouse)
    assert label.location == warehouse


def test_reprinting_replaces_the_token_not_the_item(item: Item) -> None:
    """The faded-label complaint. Revoke and reprint without touching the item."""
    old = Label.objects.create(code="FADED00000", item=item)
    old.revoked_at = datetime.datetime(2026, 8, 18, tzinfo=datetime.UTC)
    old.save()
    new = Label.objects.create(code="FRESH00000", item=item)
    assert new.item == item
    assert item.labels.count() == 2  # ty: ignore[unresolved-attribute]


def test_a_printed_code_cannot_be_changed(item: Item) -> None:
    """The code is on a sticker on a shelf. Changing it 404s that sticker for
    the life of the object carrying it, and no database write can go and
    reprint it. A reprint is a new label and a revocation of this one.
    """
    label = Label.objects.create(code="7QK3M2XV9A", item=item)
    label.code = "N3WC0DE001"
    with pytest.raises(IntegrityError, match="printed on it"):
        label.save()


def test_everything_else_about_a_label_stays_editable(item: Item, warehouse: Location) -> None:
    """Only the code is frozen. Correcting what a label points at, or how much
    one scan of it means, is what makes a correction cheaper than a reprint.
    """
    label = Label.objects.create(code="7QK3M2XV9A", item=item, quantity=Decimal("100"))
    label.item = None
    label.location = warehouse
    label.quantity = Decimal("1")
    label.save()
    label.refresh_from_db()
    assert (label.code, label.location) == ("7QK3M2XV9A", warehouse)


def test_a_label_cannot_be_revoked_in_the_future(item: Item) -> None:
    """A sticker is either honoured or it is not, and the map a client caches
    is built from ``revoked_at IS NULL`` -- so a date in the future revokes it
    now and misdates why.
    """
    label = Label.objects.create(code="7QK3M2XV9A", item=item)
    label.revoked_at = timezone.now() + datetime.timedelta(days=1)
    with pytest.raises(IntegrityError, match="in the future"):
        label.save()


def test_label_codes_are_unique(item: Item) -> None:
    Label.objects.create(code="DVP0000000", item=item)
    with pytest.raises(IntegrityError):
        Label.objects.create(code="DVP0000000", item=item)


def test_label_carries_the_quantity_one_scan_means(item: Item) -> None:
    """One item, three labels, three multipliers -- see decision 0011 section 5.

    Read back from the database, so what is proved is the stored column.
    """
    packet = Label.objects.create(code="PACKET0000", item=item, quantity=Decimal("100"))
    single = Label.objects.create(code="S1NG1E0000", item=item)
    offcut = Label.objects.create(code="0FFCVT0000", item=item, quantity=Decimal("30.500"))
    for label in (packet, single, offcut):
        label.refresh_from_db()
    assert packet.quantity == Decimal("100")
    assert single.quantity == Decimal("1")
    # Cable is cut, not counted -- see docs/data-model.md, "Units of measure".
    assert offcut.quantity == Decimal("30.5")


def test_label_quantity_must_be_positive(item: Item) -> None:
    with pytest.raises(IntegrityError):
        Label.objects.create(code="ZER0000000", item=item, quantity=Decimal("0"))


def test_label_quantity_is_one_for_a_location(warehouse: Location) -> None:
    pinned = Label.objects.create(code="10C0NE0000", location=warehouse, quantity=Decimal("1"))
    assert pinned.quantity == Decimal("1")
    with pytest.raises(IntegrityError):
        Label.objects.create(code="10CQTY0000", location=warehouse, quantity=Decimal("100"))


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------


def test_catalogue_edits_are_versioned(item: Item) -> None:
    """Catalogue records carry edit history; the ledger will not."""
    item.name = "LiteBeam AC Gen2"
    item.save()
    assert item.history.count() == 2  # ty: ignore[unresolved-attribute]
    assert item.history.earliest().name == "LiteBeam"  # ty: ignore[unresolved-attribute]


def test_renaming_an_item_does_not_break_its_identifiers(item: Item) -> None:
    """The brittleness that motivated ItemIdentifier."""
    ItemIdentifier.objects.create(item=item, kind=ItemIdentifier.Kind.ALIAS, value="litebeam")
    item.name = "Something Else Entirely"
    item.save()
    resolved = ItemIdentifier.objects.get(value_normalised="litebeam").item
    assert resolved == item


def test_history_records_who_made_the_change(
    editor: Client,
    administrator: User,
    category: Category,
) -> None:
    """ "Who changed this?" is the reason history is here, and it needs the request.

    ``history_user`` is populated from the request by
    ``simple_history.middleware.HistoryRequestMiddleware``. Without that
    middleware every edit records a NULL user and history answers only "what".

    Signed in through the app's own door, because changing anything in the
    admin now asks when this session last proved who it was -- decision 0014
    point 5, RequireSecondLookInTheAdmin.
    """
    response = editor.post(
        reverse("admin:inventory_category_change", args=[category.pk]),
        {"name": "Radios and antennas", "parent": ""},
    )
    assert response.status_code == 302

    latest = Category.history.first()  # ty: ignore[unresolved-attribute]
    assert latest.name == "Radios and antennas"
    assert latest.history_user == administrator


def test_constraint_violations_do_not_poison_the_transaction(category: Category) -> None:
    """Guards the test suite's own assumption about atomic blocks."""
    with transaction.atomic(), pytest.raises(IntegrityError):
        Item.objects.create(name="Bad", category=category, minimum_stock=Decimal("-1"))
    assert Item.objects.filter(name="Bad").count() == 0


def test_a_partial_save_does_not_normalise_what_it_will_not_write(volunteer: Volunteer) -> None:
    """Otherwise the instance describes a row that does not exist.

    Normalising in memory while writing only another column leaves the object
    claiming an email the database never received.
    """
    Volunteer.objects.filter(pk=volunteer.pk).update(email="sean@example.org")
    volunteer.refresh_from_db()
    volunteer.email = ""
    volunteer.active = False

    volunteer.save(update_fields=["active"])
    volunteer.refresh_from_db()

    assert volunteer.email == "sean@example.org"
    assert volunteer.active is False


def test_a_partial_save_does_not_normalise_a_code_it_will_not_write(item: Item) -> None:
    """The same rule as Volunteer.save, for the same reason."""
    label = Label.objects.create(code="7QK3M2XV9A", item=item)
    label.code = "wall01lo23"

    label.revoked_at = datetime.datetime(2026, 8, 19, tzinfo=datetime.UTC)
    label.save(update_fields=["revoked_at"])
    label.refresh_from_db()

    assert label.code == "7QK3M2XV9A"
    assert label.revoked_at is not None
