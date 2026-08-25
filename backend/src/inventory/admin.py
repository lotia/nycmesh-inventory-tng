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
from django.contrib.admin import ActionLocation  # ty: ignore[unresolved-import]  # stub predates it; see below
from django.http import HttpRequest
from simple_history.admin import SimpleHistoryAdmin

from inventory.models import (
    CODE_ALPHABET,
    CODE_LENGTH,
    CODE_PATTERN,
    Category,
    Device,
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
from inventory.staging import StagedCatalogueRow, StagedSubmissionRow, UnresolvedItemString


class ItemIdentifierInline(admin.TabularInline):
    """An item's identifiers, on the item's own form, added and corrected there.

    ``can_delete`` is the third route to the loss ``ItemIdentifierAdmin``
    argues against, and it is a separate switch from the two
    ``NeverDeletedAdmin`` closes: an inline draws its own "Delete?" box and
    reads this rather than the related admin's permission. Closing one and not
    the other would leave the whole argument reachable from the page an
    administrator is most often on.
    """

    model = ItemIdentifier
    extra = 1
    can_delete = False
    # Written by the database, so it is shown but never edited.
    readonly_fields = ["value_normalised"]


class NeverDeletedAdmin(admin.ModelAdmin):
    """Both routes to a delete, closed together. Each user says why for itself.

    There are two, and closing one is the mistake that leaves this looking
    done. ``has_delete_permission`` is what the change form and the
    confirmation page read; ``delete_selected`` is an action, offered in two
    places since Django 6.1 -- the changelist's menu and the change form's.
    Django does filter the action by this same permission, but that is Django's
    arrangement rather than a promise this module made, and the loss if it ever
    changes lands hardest on the ledger below.
    """

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    # Two suppressions here and two of the same pair in `tests/test_admin.py`.
    # django-stubs still describes Django 6.0's admin, which had neither
    # `ActionLocation` nor the parameter naming it, so `ty` reports as errors
    # what Django deprecation-warns for omitting. Spelled out rather than
    # swallowed by `**kwargs`, because Django decides whether an override is
    # current by reading its signature for that name.
    def get_actions(
        self,
        request: HttpRequest,
        action_location: ActionLocation = ActionLocation.CHANGE_LIST,
    ) -> dict[str, Any]:
        actions = super().get_actions(request, action_location)  # ty: ignore[too-many-positional-arguments]
        actions.pop("delete_selected", None)
        return actions


@admin.register(Category)
class CategoryAdmin(SimpleHistoryAdmin):
    list_display = ["name", "parent"]
    search_fields = ["name"]


@admin.register(Volunteer)
class VolunteerAdmin(SimpleHistoryAdmin):
    """Where the sheet import's doubts are answered.

    ``sheet_flag`` is on the list and filterable because a flag the import
    raises is work for whoever reads this page: the filter narrows the list to
    the rows it could not tell apart, and the merge that settles one is two
    fields away on the same page. ``EmptyFieldListFilter`` rather than a filter
    of our own -- the question is only whether the field says anything.
    """

    list_display = ["display_name", "email", "active", "merged_into", "sheet_flag"]
    list_filter = ["active", ("sheet_flag", admin.EmptyFieldListFilter)]
    search_fields = ["display_name", "email", "slack_id", "sheet_key"]


@admin.register(Location)
class LocationAdmin(SimpleHistoryAdmin):
    list_display = ["name", "kind", "parent", "held_by", "active"]
    list_filter = ["kind", "active"]
    search_fields = ["name"]


@admin.register(Item)
class ItemAdmin(NeverDeletedAdmin, SimpleHistoryAdmin):
    """``sheet_flag`` is listed and filterable for the reason ``VolunteerAdmin`` gives.

    And no Delete, which is the half worth explaining. Every reference to an
    item is PROTECT -- a sticker, an identifier, a recorded price, a movement
    -- so the button refused for anything catalogued, printed, priced or moved,
    and went quietly through for a row somebody had created a minute earlier by
    mistake. A control whose effect cannot be read off it, on the one page an
    administrator reaches for when the app will not do something, is worse than
    no control. ``active`` is how an item leaves the catalogue and it is
    already on this form; ``guides/administrator.md`` says so to the person
    using it, and ``docs/decisions/0024-no-hard-delete.md`` says what this does
    and does not settle for the models around it.
    """

    list_display = ["name", "category", "unit_of_measure", "minimum_stock", "active", "sheet_flag"]
    list_filter = ["category", "unit_of_measure", "active", ("sheet_flag", admin.EmptyFieldListFilter)]
    search_fields = ["name", "identifiers__value"]
    inlines = [ItemIdentifierInline]


@admin.register(ItemIdentifier)
class ItemIdentifierAdmin(NeverDeletedAdmin, SimpleHistoryAdmin):
    """Editable on its own as well as inline on the item.

    An identifier that resolves to the wrong item is found by searching for the
    string somebody scanned, not by already knowing which item it was mistyped
    onto.

    AND CORRECTED RATHER THAN REMOVED, which is inventory-tng-k50y and is this
    model's own argument. ``item_identifier_unique_normalised_value`` makes the
    normalised string unique across the whole table, which is what lets a scan
    resolve to exactly one item. Deleting a row therefore FREES the string, and
    a freed string can be created again against a different item -- at which
    point the barcode printed on the object, or the sticker on the shelf that
    resolves through it, goes on scanning and answers with the wrong item. That
    is precisely the harm inventory-tng-6kyb made ``ItemIdentifier.item``
    PROTECT to prevent, reachable from the screen instead of from the ORM, and
    answering wrongly is worse than answering not at all.

    THE WRINKLE THE ITEM'S ARGUMENT DID NOT HAVE, stated because it is the
    reason this is not simply the same case again: an identifier typed against
    the wrong item is a real mistake somebody has to be able to undo, and until
    now deleting it was how. It is not the only how, and it was never the best
    one. Every repair here is a CORRECTION to a field on this row and every one
    of those fields is on this form:

    * the string was typed against the wrong item -- change ``item``, which is
      what the standalone page above exists for;
    * the string itself was mistyped -- change ``value``, and the generated
      column follows;
    * the kind was wrong -- change ``kind``.

    Correcting beats deleting on the merits rather than by policy: the string
    stays owned throughout, so there is no window in which somebody else can
    claim it, and ``django-simple-history`` keeps what it used to say. The one
    thing a delete can express that a correction cannot is "this string should
    now mean nothing at all", and that is the case that must not be possible,
    because the barcode is still printed on the object.

    NOT A SWEEP. Decision 0024 is explicit that each of these is one screen's
    question; a volunteer, a category, a vendor and an offer keep their buttons
    and are not argued here. ``guides/administrator.md`` is where an
    administrator is told which move replaced which.
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
        # self.cleaned_data rather than super().clean()'s return, which is
        # typed dict | None: the base returns the same object and Django's own
        # documentation reads it from the attribute for exactly this reason.
        super().clean()
        cleaned = self.cleaned_data
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
class LabelAdmin(NeverDeletedAdmin, SimpleHistoryAdmin):
    """Printing and revoking a label, from the fallback interface.

    The code is minted by default and frozen once printed, which is
    ``LabelSerializer``'s rule arriving at the other write path: ``label_code_is_crockford_base32`` refuses anything
    else, and a code typed by hand would reach that constraint as an
    ``IntegrityError`` rather than as something the form could say; and a code
    changed after printing would 404 a sticker already out on a shelf.

    AND NO DELETE, which is inventory-tng-ls6d and is its own argument rather
    than the item's repeated. A label is the one row here that nothing else
    points at -- no foreign key names it, and ``label_code_is_printed`` is
    BEFORE UPDATE -- so unlike an item's, this button never refused anybody and
    took every sticker it was pressed on.

    WHAT GOES WITH THE ROW IS THE STICKER'S ONLY MEANING. A code is ten
    characters of an alphabet chosen for being readable off a fading label, it
    is minted rather than derived, and it is already stuck to a shelf. Delete
    the row and the scan resolves to nothing, for ever, with no way to work out
    what it had pointed at.

    AND THEN IT GETS WORSE, which is the half that makes this the same harm as
    inventory-tng-k50y rather than a tidier version of it.
    ``Label.mint_unique_code`` draws a code and keeps it if no row holds it, so
    a deleted code is a FREE code: the next print run can reissue it against a
    different item or a different shelf. The sticker on the wall goes on
    scanning and starts answering with something else, which is worse than
    answering with nothing.

    THE WAY OUT IS REVOKING, and it is on this form. ``LabelResolveView`` says
    it in the API's own words -- a PATCH of ``revoked``, never a delete,
    because the ledger's history refers to what the sticker pointed at -- and
    that API exposes no DELETE at all. This is the admin agreeing with it.
    Decision 0024 is the general posture; this is the particular argument, and
    ``guides/administrator.md`` is where an administrator reads it.
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


class AppendOnlyAdmin(NeverDeletedAdmin):
    """Rows may be added and read here, never changed or removed."""

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
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


# ---------------------------------------------------------------------------
# The import's staging tables.
#
# Read-only, and for a different reason than the ledger above: nothing rejects
# a write here, but a row typed over by hand would be the one thing in the
# import that no longer says what the export said, which is the only property
# these tables have. `manage.py stage_sheet` is how they change.
# ---------------------------------------------------------------------------


class StagedAdmin(NeverDeletedAdmin):
    """Rows arrive from the workbook and are read here, never written."""

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(StagedCatalogueRow)
class StagedCatalogueRowAdmin(StagedAdmin):
    list_display = ["row", "name", "staged_at"]
    search_fields = ["name"]


@admin.register(StagedSubmissionRow)
class StagedSubmissionRowAdmin(StagedAdmin):
    """What the row said, beside what was read out of it.

    ``taken`` is a filter rather than only a column because the question this
    page exists to answer is why one of the rows the population rule left out
    was left out, and that starts by listing them.
    """

    list_display = ["row", "at", "name", "direction", "item", "quantity", "taken"]
    list_filter = ["taken", "direction"]
    search_fields = ["email", "name", "item", "note"]


@admin.register(UnresolvedItemString)
class UnresolvedItemStringAdmin(StagedAdmin):
    """The strings the import could not turn into an identifier, and why.

    Read-only like its neighbours, and for the extra reason that editing a row
    here would look like resolving one: the way to clear an entry is to add the
    missing catalogue row or the missing alias and stage and mint again, which
    is also what puts the identifier in place.
    """

    list_display = ["value", "reason", "noted_at"]
    search_fields = ["value", "reason"]


# ---------------------------------------------------------------------------
# The devices this API tells apart. Not the catalogue and not the ledger: one
# row per browser that asked to be distinguishable, holding no person.
# `inventory_tng/devices.py` is what the credential is and what it is not.
# ---------------------------------------------------------------------------


@admin.register(Device)
class DeviceAdmin(NeverDeletedAdmin):
    """Where a device is cut off, and the one screen that can do it.

    EDITING `revoked_at` IS THE WHOLE POINT of this page, so unlike the
    read-only admins above this one is writable -- and it is the only field
    worth writing. Everything else describes an enrolment that has already
    happened.

    NOT ADDABLE. A row created here would carry an identifier nobody holds a
    token for, since `identifier` is written at enrolment and never editable.
    It would be a row that can never match a request.

    AND NOT DELETABLE, which matters more than it looks. Deleting a REVOKED row
    un-revokes that device: `presented_device` reads the row to decide, and a
    token whose row has gone is indistinguishable from one this deployment
    never minted, which is a state that refuses nothing. So the row is the
    revocation, and removing it is the one edit that quietly undoes the thing
    this page exists for. Decision 0024 is the general argument; this is the
    particular one.

    `enrolled_from` IS A COLUMN AND A SEARCH, and deliberately not a filter.
    Searching is the query the address was recorded for -- fifty devices from
    one address in three minutes is a burst somebody closes by finding them
    here. A `list_filter` on it would draw one sidebar link per distinct
    address off a `SELECT DISTINCT` over the whole table, on every load of this
    page, and this table gains a row per browser and loses none.
    """

    list_display = ["identifier", "enrolled_at", "enrolled_from", "revoked_at"]
    list_filter = [("revoked_at", admin.EmptyFieldListFilter)]
    search_fields = ["identifier", "enrolled_from"]
    readonly_fields = ["identifier", "enrolled_at", "enrolled_from"]
    date_hierarchy = "enrolled_at"

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False
