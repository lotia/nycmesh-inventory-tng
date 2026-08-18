"""Admin registration for the catalogue models.

Registered through ``SimpleHistoryAdmin`` so an editor can see who changed a
catalogue record and when. The stock ledger is not administered here: it is
append-only and corrections are new entries, not edits
(docs/decisions/0008-stock-ledger-transfer-graph.md).
"""

from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

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


class ItemIdentifierInline(admin.TabularInline):
    model = ItemIdentifier
    extra = 1
    # Written by the database, so it is shown but never edited.
    readonly_fields = ["value_normalised"]


@admin.register(Category)
class CategoryAdmin(SimpleHistoryAdmin):
    list_display = ["name", "parent"]
    search_fields = ["name"]


@admin.register(Volunteer)
class VolunteerAdmin(SimpleHistoryAdmin):
    list_display = ["display_name", "email", "active", "merged_into"]
    list_filter = ["active"]
    search_fields = ["display_name", "email", "slack_id"]


@admin.register(Location)
class LocationAdmin(SimpleHistoryAdmin):
    list_display = ["name", "kind", "parent", "held_by", "active"]
    list_filter = ["kind", "active"]
    search_fields = ["name"]


@admin.register(Item)
class ItemAdmin(SimpleHistoryAdmin):
    list_display = ["name", "category", "unit_of_measure", "minimum_stock", "active"]
    list_filter = ["category", "unit_of_measure", "active"]
    search_fields = ["name", "identifiers__value"]
    inlines = [ItemIdentifierInline]


@admin.register(Vendor)
class VendorAdmin(SimpleHistoryAdmin):
    list_display = ["name", "website"]
    search_fields = ["name"]


@admin.register(VendorOffer)
class VendorOfferAdmin(SimpleHistoryAdmin):
    list_display = ["item", "vendor", "unit_price", "is_preferred", "observed_at"]
    list_filter = ["vendor", "is_preferred"]


@admin.register(Label)
class LabelAdmin(SimpleHistoryAdmin):
    list_display = ["code", "item", "location", "printed_at", "revoked_at"]
    search_fields = ["code"]
