"""Inventory models: the catalogue and the stock ledger built on top of it.

The entity model and the reasoning behind it are documented once, in
docs/data-model.md and docs/decisions/0008-stock-ledger-transfer-graph.md.
This module implements it; it does not re-explain it.
"""

import secrets
from typing import Any

from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models.functions import Lower, Trim
from django.utils import timezone
from simple_history.models import HistoricalRecords

# Imported for the side effect of defining its models, which Django registers
# only from a module reached while this one loads. It is a separate module
# because the import's staging tables are not part of the entity model this
# one implements, and `inventory/staging.py` says why that matters.
from inventory import staging  # noqa: F401


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
        # pk breaks the tie: a name is unique only within its parent. Same
        # reasoning as Volunteer.Meta.
        ordering = ["name", "pk"]
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

        ``Volunteer.is_selectable`` answers the same question about one row
        already in memory. The two are deliberately adjacent: a third condition
        has to be added to both, and they are the only two places it lives.
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
    # two fields only. What that buys, and what keeps it true, is stated once on
    # NULL_WHEN_BLANK below.
    email = models.EmailField(null=True, blank=True)  # noqa: DJ001
    slack_id = models.CharField(max_length=50, null=True, blank=True)  # noqa: DJ001
    active = models.BooleanField(default=True)
    # Points at somebody selectable(), and the database says so: the
    # `volunteer_merged_into_selectable` trigger in migration 0008 refuses a
    # merge into a record that has itself been merged or been retired. Without
    # it a chain forms in the wrong direction and a cycle can be built out of
    # two ordinary merges. Checked when the column is written, so merging or
    # retiring the survivor afterwards stays an ordinary edit.
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

    # Absence is NULL, never "". The unique constraints below are partial, so
    # they cover volunteers who supplied an identifier and nobody else:
    # PostgreSQL treats NULLs as distinct, so every volunteer who supplied
    # nothing can coexist -- and most will not supply one, as 45% of the
    # historical rows did not. A stored "" is a value, so the second such
    # volunteer would collide and get an error naming a constraint they cannot
    # act on, for the most ordinary submission there is. Normalised in save()
    # rather than at the API, which covers the admin as well -- but not
    # bulk_create() or queryset update(), so the sheet import will have to
    # normalise its own rows or go through save().
    NULL_WHEN_BLANK = ("email", "slack_id")

    class Meta:
        # pk breaks the tie, and is not decoration: display names are
        # deliberately not unique -- two volunteers really are both called
        # Sean -- and PostgreSQL is free to return tied rows in any order, so
        # a paginated list without a tie-break can show one volunteer twice
        # and never show another at all.
        ordering = ["display_name", "pk"]
        constraints = [
            # Partial, for the reason given on NULL_WHEN_BLANK above.
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
            # second generation of the sheet's 102 spellings.
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
        # Only fields this save will actually write. A partial save that
        # normalised in memory without persisting would leave the object
        # describing a row that does not exist.
        writing = kwargs.get("update_fields")
        for field in self.NULL_WHEN_BLANK:
            if (writing is None or field in writing) and getattr(self, field) == "":
                setattr(self, field, None)
        super().save(*args, **kwargs)

    @property
    def is_selectable(self) -> bool:
        """Whether the list would still offer this volunteer.

        The row-level half of VolunteerManager.selectable(), for callers
        holding the object rather than building a query -- a validator whose
        related field has already fetched it, or the conflict body in views.py.
        Asking the queryset again would be a round trip to re-read two columns
        that are on the row in hand.
        """
        return self.merged_into_id is None and self.active  # ty: ignore[unresolved-attribute]


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
    # A volunteer the pick-list still offers, enforced by the trigger named on
    # Volunteer.merged_into. What that prevents, and what is refused in the
    # other direction, is docs/data-model.md under Location.
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
        # pk breaks the tie, for the reason given on Category.Meta: the
        # constraint below scopes a name to its parent, so two shelves may both
        # be "Shelf 1" and a paginated pick-list needs a total order.
        ordering = ["name", "pk"]
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


# An importable alias for the choices above, derived rather than restated so
# the two cannot drift. Why it exists at all is with the setting that consumes
# it: SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"] in inventory_tng/settings.py.
LOCATION_KIND_CHOICES = Location.Kind.choices


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


# Crockford's Base32: the digits and the uppercase letters, less I, L, O and U.
# Module level rather than an attribute of Label, because Label.Meta needs it
# and a class body is not in scope inside its own Meta.
#
# The exclusions are the whole point of the choice, and they are two different
# arguments. I, L and O are excluded so that the folds in Label.TYPO_FOLDS are
# unambiguous -- nothing minted contains a character those folds rewrite. U is
# excluded so that no minted code can spell an English obscenity, which matters
# for a token printed on a sticker and read aloud across a room.
CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

# Ten characters of that alphabet is fifty bits, and still short enough to read
# back off a dying label. What fifty bits buys is decision 0011 section 3.
CODE_LENGTH = 10

# What the check constraint and the minter both mean by "a code". Built from
# the alphabet rather than written out, so there is one definition of it; the
# alphabet contains no regular-expression metacharacter, so it is a character
# class as it stands.
CODE_PATTERN = rf"^[{CODE_ALPHABET}]{{{CODE_LENGTH}}}$"

# How many times minting will draw again before giving up. A collision needs
# two of fifty bits to match, so one retry is already generous; the loop exists
# so that the day the alphabet or the length is shortened, the failure is an
# error naming the cause rather than an IntegrityError from the unique index.
MINT_ATTEMPTS = 5


class LabelManager(models.Manager["Label"]):
    """Queries over labels that more than one caller needs."""

    def live(self) -> models.QuerySet[Label]:
        """Labels that still point at something.

        Stated once because two callers must agree: the map the client caches
        and the packaging chips on the item list are the same set seen twice,
        and a revoked sticker must be missing from both.
        """
        return self.filter(revoked_at__isnull=True)


class Label(models.Model):
    """A printed QR label, identified by an opaque token.

    The token means nothing on its own, so renaming an item cannot break a
    label, and a faded label -- the complaint that started this project -- is
    revoked and reprinted without touching the thing it points at.
    """

    # What a human typing a code off a faded label gets wrong. The alphabet is
    # Crockford's, which excludes I, L, O and U precisely so that these folds
    # are unambiguous: no minted code contains the letters being folded away.
    # See decision 0011.
    TYPO_FOLDS = str.maketrans({"I": "1", "L": "1", "O": "0"})

    # The column is wider than a code, and the constraint below is what says
    # how long a code is. Both would have to change together to change the
    # format, and only one of them rewrites the table.
    #
    # Immutable once the row exists: the `label_code_is_printed` trigger in
    # migration 0008 refuses to change it, for the reason decision 0016 point 4
    # gives. Who *supplies* a code is a different question, and one the
    # database cannot answer -- see decision 0016.
    code = models.CharField(max_length=32, unique=True)
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.CASCADE, related_name="labels")
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.CASCADE, related_name="labels")
    # NULL on a location label, where the column does not apply, rather than a
    # sentinel 1 every reader has to know means "not applicable". That is how
    # `held_by` resolves the same problem on Location, and decision 0011
    # section 5 now follows it rather than arguing the other way.
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        default=1,
        help_text=(
            "How much of the item one scan of this label stands for: a packet of 100 zip ties "
            "is 100. Null on a location label, which stands for no quantity of anything."
        ),
    )
    printed_at = models.DateTimeField(auto_now_add=True)
    # Never in the future, by the same function as StockTransaction.occurred_at:
    # LabelManager.live() asks only whether this is set, so a date ahead of the
    # clock revokes the sticker now and misdates why. Whose clock supplied it
    # is the API's to decide -- decision 0016.
    revoked_at = models.DateTimeField(null=True, blank=True)
    history = HistoricalRecords()

    objects = LabelManager()

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
                condition=models.Q(quantity__isnull=True) | models.Q(quantity__gt=0),
                name="label_quantity_positive",
            ),
            # Present exactly when the label names an item. Stated over both
            # columns rather than one, so it holds on its own without leaning
            # on the exactly-one constraint above: a quantity on a location
            # label means nothing, and a label with no quantity cannot say what
            # one scan of an item stands for.
            models.CheckConstraint(
                condition=(
                    models.Q(item__isnull=False, quantity__isnull=False)
                    | models.Q(item__isnull=True, quantity__isnull=True)
                ),
                name="label_quantity_iff_item",
            ),
            # The folding in TYPO_FOLDS only works while every stored code is
            # Crockford, and decision 0011 section 3 says what a code outside
            # that alphabet costs. So the alphabet and the length are the
            # database's to enforce rather than the minter's: the minter is
            # one write path, and the admin, the fixtures and the planned
            # sheet importer are others.
            models.CheckConstraint(
                condition=models.Q(code__regex=CODE_PATTERN),
                name="label_code_is_crockford_base32",
            ),
        ]

    def __str__(self) -> str:
        return self.code

    def save(self, *args: Any, **kwargs: Any) -> None:
        # Applied on the way in, not only when resolving: a code stored in any
        # other form would be unreachable for the life of the object carrying
        # it, because the resolver folds the very characters it holds. The
        # admin and the planned sheet import are held to this too. What stops
        # a code outside the alphabet being stored at all is
        # `label_code_is_crockford_base32` above. Skipped when a partial save is
        # not writing the code, for the reason on Volunteer.save.
        writing = kwargs.get("update_fields")
        if writing is None or "code" in writing:
            self.code = self.normalise_code(self.code)
        super().save(*args, **kwargs)

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    @classmethod
    def normalise_code(cls, raw: str) -> str:
        """The canonical form of a code, however it arrived."""
        return raw.strip().upper().translate(cls.TYPO_FOLDS)

    @classmethod
    def mint_code(cls) -> str:
        """One code drawn from the alphabet, without asking whether it is free.

        ``secrets`` rather than ``random``: this is a bearer token printed on a
        sticker, and ``random`` is a Mersenne twister whose next draw is
        recoverable from a few previous ones -- which, for codes minted in a
        batch and stuck on a shelf, is exactly the situation.
        """
        return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))

    @classmethod
    def mint_unique_code(cls) -> str:
        """A code no label holds yet.

        The unique index is still what guarantees it -- two requests can draw
        the same code between this query and their inserts -- and this is what
        keeps that from ever being how a volunteer finds out.
        """
        for _ in range(MINT_ATTEMPTS):
            code = cls.mint_code()
            if not cls.objects.filter(code=code).exists():
                return code
        raise RuntimeError(
            f"Minted {MINT_ATTEMPTS} codes and every one was already taken. "
            f"{CODE_LENGTH} characters of this alphabet is no longer enough for the number of labels printed."
        )


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

    # Selectable, and the database says so -- the same function as
    # Location.held_by and Volunteer.merged_into. Recording new work against a
    # merged duplicate is how the next generation of duplicates starts.
    actor = models.ForeignKey(Volunteer, on_delete=models.PROTECT, related_name="transactions")
    # The kind decides the shape of every movement below it, and the
    # `stock_movement_matches_kind` trigger in migration 0008 holds the two
    # together: see StockMovement.
    kind = models.CharField(max_length=20, choices=Kind)
    # Never in the future. A trigger, not a check constraint, because
    # timezone.now() is not immutable -- and worth enforcing at all because
    # the ledger is append-only, so a wrong timestamp is never corrected, only
    # compensated, and it is the key this model is ordered by.
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
    # double-post it. Scoped to the actor by the constraint below; decision
    # 0011 says why.
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
                fields=["actor", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="stock_transaction_unique_idempotency_key",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.Kind(self.kind).label} by {self.actor} at {self.occurred_at:%Y-%m-%d %H:%M}"


# The counterpart of LOCATION_KIND_CHOICES above, for the same consumer.
TRANSACTION_KIND_CHOICES = StockTransaction.Kind.choices


class StockMovement(models.Model):
    """A quantity of one item moving between two places.

    Either side may be NULL, meaning somewhere outside the system: a vendor
    shipment arriving, or hardware fitted at an install. What the two columns
    mean together, and why a quantity is always positive, is
    docs/data-model.md.

    Which sides each kind of transaction requires and which it forbids is a
    cross-table rule -- these two columns against the parent's ``kind`` -- so a
    check constraint cannot see it. The `stock_movement_matches_kind` trigger
    in migration 0008 enforces it and KIND_SIDES in views.py reports it per
    line; decision 0011 section 6 is the rule itself.
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
