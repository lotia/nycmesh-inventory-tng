"""Admin registration for every model in the app.

The admin is the fallback interface and is kept complete for the reasons in
docs/decisions/0014-one-interface.md (point 4). ``tests/test_admin.py`` fails
the build when a model is added without being registered here, so this module
is where that obligation is discharged.

Catalogue models are registered through ``SimpleHistoryAdmin`` so an editor can
see who changed a record and when. The ledger models are registered too, but
append-only: see ``AppendOnlyAdmin`` below.
"""

import re
from typing import Any

from django import forms
from django.contrib import admin
from django.http import HttpRequest
from simple_history.admin import SimpleHistoryAdmin

from inventory.models import (
    CODE_ALPHABET,
    CODE_LENGTH,
    CODE_PATTERN,
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


class LabelForm(forms.ModelForm):
    """A label as the admin edits one.

    ``label_code_is_crockford_base32`` is the thing that enforces the format;
    this only decides what an administrator is told, which is the same
    distinction ``inventory/serializers.py`` draws. Without it the constraint
    is reached as an ``IntegrityError`` and an error page, on a form that had
    no way to say what was wrong. The pattern is imported rather than restated
    so there is still one definition of it.
    """

    class Meta:
        model = Label
        # Named rather than `__all__`, so a field added to Label is a decision
        # about whether an administrator edits it rather than an assumption.
        # `printed_at` is the database's and is not on the list for that
        # reason -- it is the label's age, not a date somebody chooses.
        fields = ["code", "item", "location", "quantity", "revoked_at"]

    def clean(self) -> dict[str, Any]:
        """Empty the quantity on a location label, and require one on an item.

        ``label_quantity_iff_item`` is what enforces this. The form has to
        agree because the model still defaults the column to ``1`` -- the right
        default for the common case, an item label standing for one of its
        item, and exactly the value the constraint forbids on the other. So the
        add form arrives pre-filled with a value that cannot be saved, and
        without this an administrator printing a wall code meets the constraint
        as a non-field error naming it, which is the opaque outcome this class
        exists to prevent.

        ``LabelSerializer.validate`` does the same for the API. Two mirrors of
        one constraint rather than one, because the admin does not pass through
        a serializer -- decision 0016.
        """
        cleaned = super().clean()
        if cleaned.get("location") is not None:
            cleaned["quantity"] = None
        elif cleaned.get("item") is not None and cleaned.get("quantity") is None:
            self.add_error(
                "quantity",
                "An item label says how much of its item one scan stands for, so it carries a quantity.",
            )
        return cleaned

    def clean_code(self) -> str:
        code = Label.normalise_code(self.cleaned_data["code"])
        if not re.match(CODE_PATTERN, code):
            raise forms.ValidationError(
                f"A code is {CODE_LENGTH} characters of {CODE_ALPHABET}. "
                "Leave the minted one alone unless you are recording a sticker printed elsewhere."
            )
        return code


@admin.register(Label)
class LabelAdmin(SimpleHistoryAdmin):
    """Printing and revoking a label, from the fallback interface.

    The code is minted by default and frozen once printed, which is
    ``LabelSerializer``'s rule arriving at the other write path: ``label_code_is_crockford_base32`` refuses anything
    else, and a code typed by hand would reach that constraint as an
    ``IntegrityError`` rather than as something the form could say; and a code
    changed after printing would 404 a sticker already out on a shelf.
    """

    form = LabelForm
    list_display = ["code", "item", "location", "quantity", "printed_at", "revoked_at"]
    search_fields = ["code"]

    def get_changeform_initial_data(self, request: HttpRequest) -> dict[str, Any]:
        """Mint the code the add form arrives holding.

        Offered rather than imposed, so an administrator recovering a label
        from a batch printed elsewhere can still type the code on the sticker.
        """
        return {**super().get_changeform_initial_data(request), "code": Label.mint_unique_code()}

    def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> list[str]:
        return ["code"] if obj is not None else []


# ---------------------------------------------------------------------------
# The stock ledger.
#
# Registered so the fallback is complete, but not editable. A database trigger
# rejects any UPDATE or DELETE on these tables -- see
# docs/data-model.md, "Where PostgreSQL-specific features are used" -- so an
# admin offering a save button would only be offering an error page.
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
