"""Inventory models: the catalogue and the stock ledger built on top of it.

The entity model and the reasoning behind it are documented once, in
docs/data-model.md and docs/decisions/0008-stock-ledger-transfer-graph.md.
This module implements it; it does not re-explain it.
"""

from typing import Any

from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models.functions import Lower, Trim
from django.utils import timezone
from simple_history.models import HistoricalRecords


class Category(models.Model):
    """A grouping of items, nestable so that "Fibre" can contain "Pigtails".

    Cycles are rejected by a database trigger shared with Location; see the
    migration. A check constraint would only have caught a node parented to
    itself, leaving A -> B -> A to hang every walk of the tree.
    """

    name = models.CharField(max_length=100)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    history = HistoricalRecords()

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]
        constraints = [
            # nulls_distinct=False: see docs/data-model.md, "Where PostgreSQL-
            # specific features are used".
            models.UniqueConstraint(
                fields=["parent", "name"],
                name="category_unique_name_within_parent",
                nulls_distinct=False,
            ),
        ]

    def __str__(self) -> str:
        return self.name


class VolunteerManager(models.Manager["Volunteer"]):
    """Queries over volunteers that more than one caller needs."""

    def selectable(self) -> models.QuerySet[Volunteer]:
        """Volunteers who may be offered as a choice, or recorded against.

        Stated once because two callers must agree: the pick-list offers these
        and the batch endpoint accepts these as the actor. If the two drifted
        apart, the API would offer somebody it then refuses to record work for.

        A merge sets ``merged_into`` and leaves the ledger untouched, so past
        work stays attributed while the duplicate stops being offered; merging
        duplicates is a first-class operation here (docs/data-model.md), and
        recording new work against a retired record would start the next
        generation of them.
        """
        return self.filter(merged_into__isnull=True, active=True)


class Volunteer(models.Model):
    """Someone who moves stock.

    Deliberately not ``auth.User``: most volunteers transact without ever
    needing to log in. They may add themselves, so duplicates are expected and
    merging is a first-class operation rather than a cleanup script. See
    docs/data-model.md.
    """

    display_name = models.CharField(max_length=100)
    # NULL rather than "" is deliberate, and is why DJ001 is suppressed on these
    # two fields only. The unique constraints below are partial: they apply to
    # volunteers who supplied the identifier. PostgreSQL treats NULLs as
    # distinct, so absent values do not collide -- whereas a shared "" would
    # make the second volunteer without an email a constraint violation.
    email = models.EmailField(null=True, blank=True)  # noqa: DJ001
    slack_id = models.CharField(max_length=50, null=True, blank=True)  # noqa: DJ001
    active = models.BooleanField(default=True)
    merged_into = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="merged_from",
        help_text="Set when this record is a duplicate of another volunteer.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    history = HistoricalRecords()

    objects = VolunteerManager()

    # Absence is NULL, never "". The unique indexes below are partial so that
    # every volunteer who supplied nothing can coexist; a stored "" is a value,
    # so the second one would collide and get an error naming a constraint they
    # cannot act on. Normalised here rather than at the API, so the admin and
    # the planned sheet import are held to it too.
    NULL_WHEN_BLANK = ("email", "slack_id")

    class Meta:
        # pk breaks the tie, and is not decoration: display names are
        # deliberately not unique -- two volunteers really are both called
        # Sean -- and PostgreSQL is free to return tied rows in any order, so
        # a paginated list without a tie-break can show one volunteer twice
        # and never show another at all.
        ordering = ["display_name", "pk"]
        constraints = [
            # Partial: uniqueness applies only to volunteers who supplied the
            # identifier. Most will not, and 45% of the historical rows did not.
            models.UniqueConstraint(
                fields=["email"],
                condition=models.Q(email__isnull=False),
                name="volunteer_unique_email_when_present",
            ),
            models.UniqueConstraint(
                fields=["slack_id"],
                condition=models.Q(slack_id__isnull=False),
                name="volunteer_unique_slack_id_when_present",
            ),
            models.CheckConstraint(
                condition=~models.Q(merged_into=models.F("id")),
                name="volunteer_not_merged_into_self",
            ),
        ]
        indexes = [
            # Trigram index: the self-registration form searches existing
            # volunteers before offering to create one, which is what stops a
            # second generation of 102 spellings for 65 people.
            #
            # Indexes the bare column, not Lower(display_name): pg_trgm already
            # lowercases when it builds trigrams, so the index is case-
            # insensitive either way, and an expression index is only usable by
            # queries that repeat the expression. Wrapping it would leave
            # `display_name__trigram_similar` -- the query this index exists
            # for -- unable to use it. The `icontains` half of the search runs
            # unindexed whatever is done here; see VolunteerFilter.
            GinIndex(
                fields=["display_name"],
                opclasses=["gin_trgm_ops"],
                name="volunteer_display_name_trgm",
            ),
        ]

    def __str__(self) -> str:
        return self.display_name

    def save(self, *args: Any, **kwargs: Any) -> None:
        for field in self.NULL_WHEN_BLANK:
            if getattr(self, field) == "":
                setattr(self, field, None)
        super().save(*args, **kwargs)


class Location(models.Model):
    """Somewhere stock can be, including a volunteer holding it.

    Nestable, so a site contains rooms and a room contains shelves. Somewhere
    outside the system -- a vendor shipment, hardware fitted at an install --
    is represented by a NULL location on a movement, never by a row here.
    """

    class Kind(models.TextChoices):
        WAREHOUSE = "warehouse", "Warehouse"
        HUB = "hub", "Hub"
        ROOM = "room", "Room"
        SHELF = "shelf", "Shelf"
        VOLUNTEER_CUSTODY = "volunteer_custody", "Volunteer custody"
        VEHICLE = "vehicle", "Vehicle"

    name = models.CharField(max_length=150)
    kind = models.CharField(max_length=20, choices=Kind)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    held_by = models.ForeignKey(
        Volunteer,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="custody_locations",
    )
    active = models.BooleanField(default=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["parent", "name"],
                name="location_unique_name_within_parent",
                nulls_distinct=False,
            ),
            # held_by is set if and only if this is a custody location. Stated
            # as a constraint rather than trusted to application code, because
            # a custody location with no holder answers "who has it?" with
            # nothing, which is the whole point of the model.
            models.CheckConstraint(
                # "volunteer_custody" is spelled out rather than referenced as
                # Kind.VOLUNTEER_CUSTODY: Meta is evaluated while the enclosing
                # class is still being built, so the nested class is not yet in
                # scope. The value is the one stored in the column.
                condition=(
                    models.Q(kind="volunteer_custody", held_by__isnull=False)
                    | (~models.Q(kind="volunteer_custody") & models.Q(held_by__isnull=True))
                ),
                name="location_held_by_iff_custody",
            ),
            models.UniqueConstraint(
                fields=["held_by"],
                condition=models.Q(held_by__isnull=False),
                name="location_one_custody_per_volunteer",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Item(models.Model):
    """A catalogued thing. Not the stock of it: quantities are never stored here.

    Current stock is derived from the ledger, so this model has no count field
    and adding one would be a bug.
    """

    class UnitOfMeasure(models.TextChoices):
        EACH = "each", "Each"
        METRE = "metre", "Metre"
        FOOT = "foot", "Foot"

    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="items")
    unit_of_measure = models.CharField(max_length=10, choices=UnitOfMeasure, default=UnitOfMeasure.EACH)
    minimum_stock = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    reorder_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=1)
    active = models.BooleanField(default=True)
    attributes = models.JSONField(
        default=dict,
        blank=True,
        help_text="Type-specific specifications. Radios, cable and hand tools have little in common.",
    )
    history = HistoricalRecords()

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(minimum_stock__gte=0),
                name="item_minimum_stock_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(reorder_quantity__gt=0),
                name="item_reorder_quantity_positive",
            ),
        ]
        indexes = [GinIndex(fields=["attributes"], name="item_attributes_gin")]

    def __str__(self) -> str:
        return self.name


class ItemIdentifier(models.Model):
    """Any string that has ever meant a given item.

    The sheet this replaces matched items by display name, so typos and
    informal names silently matched nothing at all. Every string that has ever
    meant an item is a row here instead, and the normalised form is unique
    across the whole table, so a scan or a typed string resolves to exactly one
    item. The evidence is in docs/data-model.md.
    """

    class Kind(models.TextChoices):
        MFG_PART = "mfg_part", "Manufacturer part number"
        VENDOR_SKU = "vendor_sku", "Vendor SKU"
        LEGACY_NYCM = "legacy_nycm", "Legacy NYCM code"
        ALIAS = "alias", "Alias"
        BARCODE = "barcode", "Barcode"

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="identifiers")
    kind = models.CharField(max_length=20, choices=Kind)
    value = models.CharField(max_length=200)
    # Generated by the database, so normalisation cannot drift between the
    # write path, the import and the scan endpoint.
    value_normalised = models.GeneratedField(
        expression=Lower(Trim("value")),
        output_field=models.CharField(max_length=200),
        db_persist=True,
    )
    history = HistoricalRecords()

    class Meta:
        ordering = ["value"]
        constraints = [
            models.UniqueConstraint(
                fields=["value_normalised"],
                name="item_identifier_unique_normalised_value",
            ),
        ]

    def __str__(self) -> str:
        # Kind(self.kind).label rather than get_kind_display(): Django generates
        # the latter at runtime, so `ty` cannot see it. See DEVELOPERS.md#typing.
        return f"{self.value} ({self.Kind(self.kind).label})"


class Vendor(models.Model):
    """Somewhere items are bought from."""

    name = models.CharField(max_length=100, unique=True)
    website = models.URLField(blank=True)
    notes = models.TextField(blank=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class VendorOffer(models.Model):
    """One vendor's listing for one item, as observed at a point in time.

    Prices are appended rather than overwritten, so a historical purchase price
    stays recoverable. The sheet kept three URL columns and wrote price
    comparisons into free text; this is that repeating group, normalised.
    """

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="offers")
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="offers")
    url = models.URLField(blank=True)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    units_per_order = models.DecimalField(max_digits=12, decimal_places=3, default=1)
    is_preferred = models.BooleanField(default=False)
    observed_at = models.DateField()
    notes = models.TextField(blank=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ["item", "vendor"]
        constraints = [
            models.UniqueConstraint(
                fields=["item"],
                condition=models.Q(is_preferred=True),
                name="vendor_offer_one_preferred_per_item",
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price__isnull=True) | models.Q(unit_price__gte=0),
                name="vendor_offer_unit_price_not_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(units_per_order__gt=0),
                name="vendor_offer_units_per_order_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.item} from {self.vendor}"


class Label(models.Model):
    """A printed QR label, identified by an opaque token.

    The token means nothing on its own, so renaming an item cannot break a
    label, and a faded label -- the complaint that started this project -- is
    revoked and reprinted without touching the thing it points at.
    """

    code = models.CharField(max_length=32, unique=True)
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.CASCADE, related_name="labels")
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.CASCADE, related_name="labels")
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=1,
        help_text="How much of the item one scan of this label stands for: a packet of 100 zip ties is 100.",
    )
    printed_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ["-printed_at"]
        constraints = [
            # A label points at exactly one thing. Two nullable foreign keys
            # rather than a generic relation: the database can then enforce
            # this, and the columns stay typed.
            models.CheckConstraint(
                condition=(
                    models.Q(item__isnull=False, location__isnull=True)
                    | models.Q(item__isnull=True, location__isnull=False)
                ),
                name="label_targets_exactly_one",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="label_quantity_positive",
            ),
            # Stated in terms of location rather than item so that it holds
            # on its own, without leaning on the exactly-one constraint above.
            models.CheckConstraint(
                condition=models.Q(location__isnull=True) | models.Q(quantity=1),
                name="label_quantity_is_one_for_a_location",
            ),
        ]

    def __str__(self) -> str:
        return self.code

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


# ---------------------------------------------------------------------------
# The stock ledger.
#
# Append-only: rows are inserted and never changed. A correction is a new,
# compensating movement, which is why neither model below carries
# HistoricalRecords -- the ledger is its own history. The rule is enforced by a
# database trigger; see the migration for why a trigger rather than REVOKE.
# ---------------------------------------------------------------------------


class StockTransaction(models.Model):
    """One recorded act: a scanning session, a delivery, a stock count.

    Batching is the point. In the system this replaces one form submission
    could carry exactly one item, so people submitted the same form repeatedly;
    here that is one transaction with many movements. See docs/data-model.md.
    """

    class Kind(models.TextChoices):
        CHECKOUT = "checkout", "Check out"
        CHECKIN = "checkin", "Check in"
        RECEIPT = "receipt", "Receipt"
        CONSUMPTION = "consumption", "Used at a job"
        TRANSFER = "transfer", "Transfer"
        ADJUSTMENT = "adjustment", "Adjustment"
        COUNT = "count", "Stock count"

    actor = models.ForeignKey(Volunteer, on_delete=models.PROTECT, related_name="transactions")
    kind = models.CharField(max_length=20, choices=Kind)
    occurred_at = models.DateTimeField(default=timezone.now)
    recorded_at = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(max_length=100, blank=True)
    job_reference = models.CharField(
        max_length=50,
        blank=True,
        db_index=True,
        help_text="Install or node this relates to, e.g. NN217.",
    )
    note = models.TextField(blank=True)
    # A phone in a basement will retry. Replaying the same batch must not
    # double-post it.
    idempotency_key = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            # This model's default ordering. The ledger only ever grows, so a
            # paginated "recent activity" view would otherwise seq-scan forever.
            models.Index(fields=["-occurred_at"], name="stock_transaction_occurred"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="stock_transaction_unique_idempotency_key",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.Kind(self.kind).label} by {self.actor} at {self.occurred_at:%Y-%m-%d %H:%M}"


class StockMovement(models.Model):
    """A quantity of one item moving between two places.

    Either side may be NULL, meaning somewhere outside the system: a vendor
    shipment arriving, or hardware fitted at an install. Direction is expressed
    by which side the location sits on, never by the sign of the quantity.
    """

    transaction = models.ForeignKey(StockTransaction, on_delete=models.PROTECT, related_name="movements")
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="movements")
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    from_location = models.ForeignKey(
        Location,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="movements_out",
    )
    to_location = models.ForeignKey(
        Location,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="movements_in",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="stock_movement_quantity_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(from_location__isnull=False) | models.Q(to_location__isnull=False),
                name="stock_movement_has_a_side",
            ),
            models.CheckConstraint(
                condition=~models.Q(from_location=models.F("to_location")),
                name="stock_movement_from_differs_from_to",
            ),
        ]
        indexes = [
            models.Index(fields=["item", "to_location"], name="stock_movement_item_to"),
            models.Index(fields=["item", "from_location"], name="stock_movement_item_from"),
        ]

    def __str__(self) -> str:
        return f"{self.quantity} x {self.item}: {self.from_location or '-'} -> {self.to_location or '-'}"


class StockBalance(models.Model):
    """Current stock per item and location, derived from the ledger.

    Backed by a database view, not a table: nothing writes a balance. At the
    observed volume this is trivially cheap, and the previous system's slowness
    came from recalculating a chain of spreadsheet formulas rather than from
    arithmetic. If it ever costs anything it can become a materialised view
    without the ledger changing.
    """

    # Composite rather than a synthetic id: see the view definition in the
    # migration for why the view must not carry a ROW_NUMBER() column.
    pk = models.CompositePrimaryKey("item", "location")
    item = models.ForeignKey(Item, on_delete=models.DO_NOTHING, related_name="balances")
    location = models.ForeignKey(Location, on_delete=models.DO_NOTHING, related_name="balances")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)

    class Meta:
        managed = False
        db_table = "inventory_stock_balance"

    def __str__(self) -> str:
        return f"{self.quantity} x {self.item} at {self.location}"
