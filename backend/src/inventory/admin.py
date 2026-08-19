"""Admin registration for every model in the app.

The admin is the fallback interface and is kept complete for the reasons in
docs/decisions/0014-one-interface.md (point 4). ``tests/test_admin.py`` fails
the build when a model is added without being registered here, so this module
is where that obligation is discharged.

Catalogue models are registered through ``SimpleHistoryAdmin`` so an editor can
see who changed a record and when. The ledger models are registered too, but
append-only: see ``AppendOnlyAdmin`` below.
"""

from typing import Any

from django.contrib import admin
from django.http import HttpRequest
from simple_history.admin import SimpleHistoryAdmin

from inventory.models import (
    Category,
    Item,
    ItemIdentifier,
    Label,
    Location,
    StockMovement,
    StockTransaction,
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


@admin.register(ItemIdentifier)
class ItemIdentifierAdmin(SimpleHistoryAdmin):
    """Editable on its own as well as inline on the item.

    An identifier that resolves to the wrong item is found by searching for the
    string somebody scanned, not by already knowing which item it was mistyped
    onto.
    """

    list_display = ["value", "kind", "item"]
    list_filter = ["kind"]
    search_fields = ["value", "item__name"]
    readonly_fields = ["value_normalised"]


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
    list_display = ["code", "item", "location", "quantity", "printed_at", "revoked_at"]
    search_fields = ["code"]


# ---------------------------------------------------------------------------
# The stock ledger.
#
# Registered so the fallback is complete, but not editable. A database trigger
# rejects any UPDATE or DELETE on these tables
# (docs/decisions/0008-stock-ledger-transfer-graph.md), so an admin offering a
# save button would only be offering an error page.
# ---------------------------------------------------------------------------


class AppendOnlyAdmin(admin.ModelAdmin):
    """Rows may be added and read here, never changed or removed."""

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


class StockMovementInline(admin.TabularInline):
    model = StockMovement
    extra = 1


@admin.register(StockTransaction)
class StockTransactionAdmin(AppendOnlyAdmin):
    """The unit an administrator records: one act, with its movements.

    Adding is allowed because a correction *is* a new transaction, and this is
    the interface that still works when the single-page app does not.
    """

    list_display = ["occurred_at", "kind", "actor", "job_reference", "reason"]
    list_filter = ["kind"]
    search_fields = ["job_reference", "reason", "note", "actor__display_name"]
    date_hierarchy = "occurred_at"
    inlines = [StockMovementInline]


@admin.register(StockMovement)
class StockMovementAdmin(AppendOnlyAdmin):
    """Readable, and deliberately not addable on its own.

    A movement is only meaningful as part of the act that produced it, so it is
    added through the transaction above. On its own it answers "where did this
    item go?", which is what the list is for.
    """

    list_display = ["item", "quantity", "from_location", "to_location", "transaction"]
    list_filter = ["item"]
    search_fields = ["item__name", "transaction__job_reference"]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False
