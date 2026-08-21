"""Serializers for the API.

Two different kinds of rule live here, and the difference matters.

Most are *reporting*. The invariants are the database's -- check constraints
and triggers, listed in docs/data-model.md -- and they stay that way; a
violation surfaces as a 500 naming no line, and a volunteer holding a phone in
a basement needs to be told which of their 24 scans to fix. So those rules are
stated here as well, and the database remains the thing that enforces them.
Every rule below that names a row's own columns is one of these, including the
four that reach across tables or across time and so are triggers rather than
constraints (migration 0008):

- a batch may not be dated in the future
  (``StockTransactionCreateSerializer.validate_occurred_at``);
- a merged or retired volunteer may not be the actor, may not be named as
  holding custody of stock, and may not be the survivor a merge points at
  (``VolunteerDetailSerializer.validate_merged_into``,
  ``LocationSerializer.validate_held_by``, and the ``actor`` field's queryset).
  The reverse is checked too, in ``VolunteerDetailSerializer.validate``:
  merging or retiring somebody who already holds an active custody location is
  refused, naming the location, rather than leaving it pointing at a record
  the pick-list no longer offers;
- a label's code may not change once it is printed
  (``LabelSerializer.validate``);
- and, in views.py, a movement carries the sides its transaction's kind calls
  for.

The rest are the API's own, and are about *who may supply a value* rather than
about which values are allowed. The database cannot hold either, because the
admin is an authorised writer that legitimately does what a client may not,
and there is no column recording which writer a value came from. Decision 0016
records them as client contracts:

1. A client does not choose a label's code. It is minted here, by
   ``Label.mint_unique_code``, and a submitted one is refused rather than
   ignored.
2. A client does not time a revocation. It sends the boolean ``revoked`` and
   the server reads its own clock.

Anything writing past this module -- the admin, a fixture load, the planned
sheet importer -- is held to everything in the first list and to neither of
these two.
"""

import datetime
import decimal
from typing import Any

from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator

from inventory.models import (
    Category,
    Item,
    Label,
    Location,
    StockMovement,
    StockTransaction,
    Volunteer,
)

# Far above a real cart of a couple of dozen scans. It exists only so that one
# request cannot open an unbounded write transaction against an append-only
# ledger.
MAX_MOVEMENTS = 500

# How far ahead of the server a client's clock may be and still be believed.
CLOCK_SKEW = datetime.timedelta(minutes=5)


class CachedPrimaryKeyRelatedField(serializers.PrimaryKeyRelatedField):
    """A related field that looks each distinct id up once per request.

    Cached in the serializer context, which DRF builds fresh for every
    serializer and shares across its whole field tree. Keyed by field, so two
    fields over the same table do not share an entry -- worth at most one
    extra query, against having to reason about whether they mean the same
    thing. Only successful lookups are cached: an unknown id rejects the whole
    batch anyway.

    Lines naming the same id therefore hold the *same* instance, so nothing
    per-line may mutate a resolved object.
    """

    def to_internal_value(self, data: Any) -> Any:
        cache = self.context.setdefault("resolved_related", {})
        # The type is part of the key, not decoration. Python hashes True and 1
        # alike and calls them equal, so without it a line sending `true` where
        # an id belongs would hit the entry an earlier line made for `1` and be
        # recorded against that item -- silently, into a ledger that cannot be
        # edited -- instead of being rejected the way the base class rejects a
        # bool.
        key = (self.field_name, type(data), data)
        try:
            hit = key in cache
        except TypeError:
            # An id that cannot be hashed is not a pk at all -- a client sent
            # an object or a list. Let the base class reject it by index.
            return super().to_internal_value(data)
        if not hit:
            cache[key] = super().to_internal_value(data)
        return cache[key]


class StockMovementInputSerializer(serializers.ModelSerializer):
    """One line of a submitted batch."""

    # Any item or location, including retired ones the read API no longer
    # offers. That asymmetry is deliberate only in the sense that nobody has
    # decided it yet: inventory-tng-6c7.
    item = CachedPrimaryKeyRelatedField(queryset=Item.objects.all())
    from_location = CachedPrimaryKeyRelatedField(
        queryset=Location.objects.all(),
        required=False,
        allow_null=True,
    )
    to_location = CachedPrimaryKeyRelatedField(
        queryset=Location.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = StockMovement
        fields = ["item", "quantity", "from_location", "to_location"]

    def validate_quantity(self, value: Any) -> Any:
        # Direction is which side the location sits on, never the sign of the
        # quantity -- so a negative here is a client bug, not a return.
        if value <= 0:
            raise serializers.ValidationError("Quantity must be greater than zero.")
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        from_location = attrs.get("from_location")
        to_location = attrs.get("to_location")
        if from_location is None and to_location is None:
            raise serializers.ValidationError(
                "A movement needs somewhere to come from or somewhere to go to.",
            )
        if from_location is not None and from_location == to_location:
            raise serializers.ValidationError("A movement cannot start and end in the same place.")
        return attrs


class StockMovementSerializer(serializers.ModelSerializer):
    """One recorded line, as it is read back."""

    class Meta:
        model = StockMovement
        fields = ["id", "item", "quantity", "from_location", "to_location"]


class StockTransactionWarningSerializer(serializers.Serializer):
    """Stock went negative. The movement was recorded anyway; see decision
    0011, "Insufficient stock is a warning, not a rejection".
    """

    item = serializers.IntegerField()
    location = serializers.IntegerField()
    balance = serializers.DecimalField(max_digits=14, decimal_places=3)
    detail = serializers.CharField()


class StockTransactionSerializer(serializers.ModelSerializer):
    """A recorded batch, as it is read back.

    Renders a response the view has assembled rather than a bare model: both
    ``lines`` and ``warnings`` are attributes the view attaches, so the rows
    are read once and the balances behind the warnings once.
    """

    movements = StockMovementSerializer(many=True, read_only=True, source="lines")
    warnings = StockTransactionWarningSerializer(many=True, read_only=True)

    class Meta:
        model = StockTransaction
        fields = [
            "id",
            "idempotency_key",
            "kind",
            "actor",
            "occurred_at",
            "recorded_at",
            "reason",
            "job_reference",
            "note",
            "movements",
            "warnings",
        ]


class StockTransactionCreateSerializer(serializers.ModelSerializer):
    """A batch as it is submitted: one act, many lines.

    Movements name items by id, never by label code. The client has already
    resolved every code it scanned, and accepting codes here would add a second
    resolution path that fails after the volunteer thinks they are done.
    """

    # max_length reaches the ListSerializer many=True builds, which the stubs
    # for the wrapped serializer do not describe. See DEVELOPERS.md#typing.
    movements = StockMovementInputSerializer(
        many=True,
        allow_empty=False,
        max_length=MAX_MOVEMENTS,  # ty: ignore[unknown-argument]
    )
    # Why the key carries no uniqueness validator is stated once, in
    # get_unique_together_validators below.
    idempotency_key = serializers.CharField(
        max_length=64,
        required=False,
        allow_null=True,
    )
    # Merged and inactive volunteers are not choices; the rule and the reason
    # live once, on the queryset the pick-list uses too.
    actor = serializers.PrimaryKeyRelatedField(queryset=Volunteer.objects.selectable())

    def validate_occurred_at(self, value: Any) -> Any:
        # The ledger is append-only, so a wrong timestamp can never be
        # corrected -- only compensated, which does not move it. It is also
        # the default ordering key, so one bad clock sits at the top of every
        # recent-activity view for as long as the row exists. Backdating is
        # ordinary (a volunteer records yesterday's install); postdating is a
        # broken clock or a typo.
        #
        # Mirrors `stock_transaction_occurred_at_not_in_the_future`, which
        # allows the same CLOCK_SKEW so that the database cannot refuse a batch
        # this method has just accepted.
        if value > timezone.now() + CLOCK_SKEW:
            raise serializers.ValidationError("A batch cannot have happened in the future.")
        return value

    def get_unique_together_validators(self) -> list[UniqueTogetherValidator]:
        """No uniqueness validator for the idempotency key.

        DRF builds one from the partial unique index on
        ``(actor, idempotency_key)``, which makes the key a required field and
        so rejects the ordinary batch that carries none. The key is also
        *meant* to arrive twice -- that is what makes a retry safe -- and two
        genuinely concurrent retries would race past the view's lookup only to
        be rejected here. The index stays the arbiter, and the view answers a
        collision with the transaction the first attempt created.

        Only that one is dropped, not the whole set. Blanking ``Meta.validators``
        would discard every model-derived validator, and so would returning an
        empty list here: a constraint added to StockTransaction later should
        still be reported as a 400 rather than escaping as a 500.
        """
        return [
            validator
            for validator in super().get_unique_together_validators()
            if "idempotency_key" not in validator.fields
        ]

    class Meta:
        model = StockTransaction
        fields = [
            "idempotency_key",
            "kind",
            "actor",
            "occurred_at",
            "reason",
            "job_reference",
            "note",
            "movements",
        ]


class ApiErrorSerializer(serializers.Serializer):
    """One thing wrong, and where.

    ``index`` is the position in the submitted ``movements`` array, or null
    when the problem is with the batch itself rather than one of its lines.
    """

    index = serializers.IntegerField(allow_null=True)
    field = serializers.CharField()
    detail = serializers.CharField()


class BatchRejectedSerializer(serializers.Serializer):
    """Nothing was saved. Every bad line is listed, so one pass fixes them all."""

    detail = serializers.CharField()
    errors = ApiErrorSerializer(many=True)


class BatchInconsistencySerializer(serializers.Serializer):
    """One line that disagrees with the batch's declared kind."""

    index = serializers.IntegerField()
    detail = serializers.CharField()


class BatchInconsistentSerializer(serializers.Serializer):
    """Every line is valid on its own, but they do not add up to the kind claimed."""

    detail = serializers.CharField()
    kind = serializers.CharField()
    inconsistent = BatchInconsistencySerializer(many=True)


class VolunteerSerializer(serializers.ModelSerializer):
    """A volunteer as the pick-list shows them.

    ``email`` and ``slack_id`` are here because two people called Sean are the
    whole reason this list is searched before anyone adds themselves; without
    something to tell them apart the picker cannot help.
    """

    class Meta:
        model = Volunteer
        fields = ["id", "display_name", "email", "slack_id"]


class VolunteerConflictSerializer(serializers.Serializer):
    """The identifier is taken, by a record the pick-list will not show.

    A plain 400 is the right answer when the holder is somebody the searcher
    could have found for themselves. When the holder is a merged duplicate or a
    retired record it is a dead end -- the endpoint exists to serve
    self-registration *after* a search found nothing, and the search does not
    offer either. So the holder is named here instead, resolved forward through
    the merge to whoever survived it, and ``code`` and ``selectable`` are
    constants a client branches on rather than prose it has to read. See
    decision 0015.

    ``volunteer`` is the record to act on, not the one that holds the
    identifier: the survivor of the merge, or -- for a retired record, which
    nothing survived -- the record itself.
    """

    detail = serializers.CharField()
    code = serializers.ChoiceField(choices=["volunteer_merged", "volunteer_inactive"])
    field = serializers.ChoiceField(choices=["email", "slack_id"])
    volunteer = VolunteerSerializer()
    # Whether the named volunteer can be picked as they stand. False whenever
    # an administrator has to act first, which is every retired record and the
    # occasional merge whose survivor was later retired too.
    selectable = serializers.BooleanField()


class VolunteerDetailSerializer(VolunteerSerializer):
    """A volunteer as an administrator reads and repairs them.

    Adds the two fields that decide whether the pick-list offers somebody.
    Merging is a first-class operation rather than a cleanup script
    (docs/data-model.md), and this is where it happens: ``merged_into`` points
    a duplicate at whoever survived, and the ledger is left exactly as it was.
    """

    class Meta(VolunteerSerializer.Meta):
        fields = [*VolunteerSerializer.Meta.fields, "active", "merged_into"]

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Neither route out of the pick-list leaves a custody location behind.

        ``LocationSerializer.validate_held_by`` refuses a *new* custody
        location for somebody merged or retired, on the grounds that it would
        be the second generation of the duplicate a merge existed to remove.
        The reverse was allowed: merging or retiring somebody who already held
        one left that location active, named after them, and still offered in
        the pick-list. Both routes reached it -- ``merged_into`` and
        ``active`` -- because the guard was placed where the naming happens
        rather than where the state is created.

        Refused rather than repaired, and deliberately. Repointing the location
        at the survivor is the obvious move and fails exactly when it matters:
        ``location_one_custody_per_volunteer`` stops it the moment the survivor
        already holds one, which is the ordinary shape of a real duplicate.
        Retiring the location instead has no answer for the stock still
        recorded there, which is the open question in inventory-tng-6c7. So
        this says what to do first and lets a person decide, which is what the
        409 in decision 0015 does with a name that is already taken.
        """
        if self.instance is None:
            return attrs

        leaving = attrs.get("merged_into") is not None or attrs.get("active") is False
        if not leaving:
            return attrs

        held = self.instance.custody_locations.filter(active=True).first()
        if held is not None:
            raise serializers.ValidationError(
                f"{self.instance.display_name} still holds the custody location "
                f"{held.name!r}. Move the stock recorded there and retire it, or "
                f"hand it to somebody else, before merging or retiring them."
            )
        return attrs

    def validate_merged_into(self, value: Volunteer | None) -> Volunteer | None:
        """A merge points at somebody the pick-list will actually offer.

        Mirrors `volunteer_merged_into_selectable`, so a client sees a 400
        rather than a 500; the trigger is what holds it against the admin and
        the planned sheet importer.

        This does not stop a chain: merging A into B and later B into C is two
        valid merges, which is why decision 0015 follows ``merged_into``
        forward rather than reading it once. What it does stop is a cycle --
        every target has no merge of its own at the moment it is chosen, so no
        edge can ever point backwards along the chain, and the model forbids
        only the single-record case.
        """
        if value is None:
            return value
        if self.instance is not None and value.pk == self.instance.pk:
            raise serializers.ValidationError("A volunteer cannot be merged into themselves.")
        if not value.is_selectable:
            raise serializers.ValidationError(
                "Merge into whoever is left: this record has itself been merged, or has been retired."
            )
        return value


def _would_cycle(parent: Any, instance: Any) -> bool:
    """Whether making ``parent`` the parent of ``instance`` closes a loop.

    Mirrors the ``inventory_reject_tree_cycle`` trigger that Category and
    Location share (migration 0001). Walked upwards from the proposed parent,
    which is the direction the trigger walks, and bounded by a visited set:
    the trigger stops a cycle being created, but a request must not hang if
    one is somehow already there.
    """
    if instance is None or instance.pk is None:
        return False
    seen: set[Any] = set()
    node = parent
    while node is not None and node.pk not in seen:
        if node.pk == instance.pk:
            return True
        seen.add(node.pk)
        node = node.parent
    return False


class TreeSerializer(serializers.ModelSerializer):
    """The half Category and Location share: both are nestable trees.

    Stated once because the rule is one rule -- the database enforces both
    with a single trigger function.
    """

    def validate_parent(self, value: Any) -> Any:
        """Refuse what the trigger would refuse, as a 400 rather than a 500."""
        if _would_cycle(value, self.instance):
            raise serializers.ValidationError("That parent sits below this one, so it would make a loop.")
        return value


class CategorySerializer(TreeSerializer):
    """A grouping of items, nestable."""

    class Meta:
        model = Category
        fields = ["id", "name", "parent"]


class LocationSerializer(TreeSerializer):
    """Somewhere stock can be, including a volunteer holding it."""

    class Meta:
        model = Location
        fields = ["id", "name", "kind", "parent", "held_by", "active"]

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Mirrors ``location_held_by_iff_custody``, so a client sees a 400.

        The constraint remains what enforces it; this only decides what the
        caller is told, which is the distinction this module's docstring draws.
        """
        kind = attrs.get("kind", getattr(self.instance, "kind", None))
        # Falls back to the id column, not the relation: this asks only
        # whether there is a holder, and following it would fetch a volunteer
        # to find out. `.get` rather than a membership test, because an
        # explicit null and an absent key mean the same thing here.
        held_by = attrs.get("held_by", getattr(self.instance, "held_by_id", None))
        custody = kind == Location.Kind.VOLUNTEER_CUSTODY
        if custody and held_by is None:
            raise serializers.ValidationError(
                "A custody location is somewhere a volunteer is holding stock, so it has to say who."
            )
        if not custody and held_by is not None:
            raise serializers.ValidationError("Only a custody location names a volunteer.")

        # Bringing one back is naming a holder again, whatever the request
        # says. validate_held_by only sees a holder the request supplies, and
        # `location_held_by_selectable` returns early when held_by is
        # unchanged -- so retire the location, merge its holder, then set
        # active back to true, and the rule is gone in three legal steps. The
        # revival is where that is caught.
        reviving = attrs.get("active") is True and self.instance is not None
        if reviving and custody and "held_by" not in attrs:
            holder = self.instance.held_by
            if holder is not None and not holder.is_selectable:
                raise serializers.ValidationError(
                    f"{holder.display_name} has since been merged or retired, so this "
                    f"cannot be brought back as theirs. Name whoever holds the stock now."
                )
        return attrs

    def validate_held_by(self, value: Volunteer | None) -> Volunteer | None:
        """Custody is recorded against somebody the pick-list will still offer.

        Mirrors `location_held_by_selectable`, the same trigger that holds the
        rule for a merge and for the actor of a batch.

        The field is a plain relation over every volunteer row, and
        ``Volunteer.is_selectable`` is the row-level half of the one rule
        about who may be recorded against; see VolunteerManager.selectable().
        A custody location attached to a merged duplicate is the second
        generation of the duplicate the merge existed to remove.
        """
        if value is not None and not value.is_selectable:
            raise serializers.ValidationError(
                "Stock is held by somebody the list still offers, not by a merged or retired record."
            )
        return value


class ItemBalanceSerializer(serializers.Serializer):
    """How much of one item is at one location, derived from the ledger."""

    # The id column rather than the relation: typed in the schema without a
    # model to derive it from, and no row fetched to render a number the
    # balance already carries.
    location = serializers.IntegerField(source="location_id")
    quantity = serializers.DecimalField(max_digits=14, decimal_places=3)


class ItemLabelSerializer(serializers.Serializer):
    """An active label on this item, and what one scan of it means.

    The distinct quantities across an item's labels *are* its packaging, and
    are what the list offers as one-tap chips. See decision 0011 section 5.
    """

    code = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3)


class ItemSerializer(serializers.ModelSerializer):
    """A catalogue entry with the stock behind it.

    Balances and labels are attached by the view in bulk. Reading them per
    item would be two queries per row on the screen the volunteer looks at
    most.
    """

    balances = ItemBalanceSerializer(many=True, read_only=True)
    labels = ItemLabelSerializer(many=True, read_only=True)

    class Meta:
        model = Item
        fields = [
            "id",
            "name",
            "category",
            "unit_of_measure",
            "minimum_stock",
            "reorder_quantity",
            "active",
            "balances",
            "labels",
        ]

    def validate_minimum_stock(self, value: decimal.Decimal) -> decimal.Decimal:
        """Mirrors ``item_minimum_stock_not_negative``; see LocationSerializer.validate."""
        if value < 0:
            raise serializers.ValidationError("A minimum stock level is how little may be left, so it is not negative.")
        return value

    def validate_reorder_quantity(self, value: decimal.Decimal) -> decimal.Decimal:
        """Mirrors ``item_reorder_quantity_positive``; see LocationSerializer.validate."""
        if value <= 0:
            raise serializers.ValidationError("Reordering none of something is not an order.")
        return value


class ItemDetailSerializer(ItemSerializer):
    """One catalogue entry, as an administrator reads and edits it.

    Adds the two fields the list deliberately leaves out. ``description`` is
    free text and ``attributes`` is a specification blob; the item list is a
    hundred rows fetched over a phone connection (see ItemListView), so
    carrying either there would pay for them a hundred times over to render
    neither.
    """

    class Meta(ItemSerializer.Meta):
        fields = [*ItemSerializer.Meta.fields, "description", "attributes"]


class LabelResolveSerializer(serializers.ModelSerializer):
    """What a scanned code points at.

    ``quantity`` is part of the contract because the client cannot spell out
    the cart line without it. See decision 0011 section 5.
    """

    kind = serializers.SerializerMethodField()

    class Meta:
        model = Label
        fields = ["code", "kind", "quantity", "revoked_at", "item", "location"]

    @extend_schema_field(serializers.ChoiceField(choices=["item", "location"]))
    def get_kind(self, label: Label) -> str:
        return "item" if label.item_id is not None else "location"  # ty: ignore[unresolved-attribute]


class LabelMapSerializer(LabelResolveSerializer):
    """One label in the map the client caches, and what a scan needs of it.

    Drops ``revoked_at``, which this queryset guarantees is null, a few
    hundred times over. See LabelListView for why this response's size is
    worth caring about.

    Carries the item's name and unit instead. A cart line needs both, and
    without them the client had to hold the whole catalogue as well -- which
    is paginated, so the prefetch became four round trips fetching eighty
    kilobytes of balances and labels to keep three fields per item, on the
    connection decision 0011 section 1 exists for. Forty bytes a row here
    replaces all of it with the one unpaginated GET this endpoint is for.
    """

    item_name = serializers.CharField(source="item.name", read_only=True, default=None)
    unit_of_measure = serializers.CharField(source="item.unit_of_measure", read_only=True, default=None)

    class Meta(LabelResolveSerializer.Meta):
        fields = [
            "code",
            "kind",
            "quantity",
            "item",
            "location",
            "item_name",
            "unit_of_measure",
        ]


class LabelSerializer(LabelResolveSerializer):
    """A label as an administrator prints and revokes it.

    ``code`` is read-only in both directions of a write: it is minted here,
    from ``Label.mint_unique_code``, and printing is the one moment it is
    decided. A client that could choose one could choose a code outside the
    alphabet the resolver's folding depends on, or one already on a sticker
    somewhere -- and a client that could change one would 404 a sticker
    already out on a shelf.

    ``revoked`` is a boolean rather than a writable ``revoked_at`` because the
    server owns the clock: a client that could name the moment could revoke a
    sticker in the future, and a sticker is either honoured or it is not.
    Setting it back to false un-revokes, so revoking the wrong label is undone
    rather than worked around with a reprint.
    """

    # This docstring is the schema's description of the component, so what is
    # true of the database rather than of the request belongs here instead: a
    # date ahead of the clock is refused below the API too, by
    # `label_revoked_at_not_in_the_future`. What no trigger can tell is whose
    # clock a plausible date came from, which is why the boolean above is the
    # contract -- decision 0016.
    revoked = serializers.BooleanField(
        write_only=True,
        required=False,
        help_text="True revokes this label, false restores it. The timestamp is the server's.",
    )

    class Meta(LabelResolveSerializer.Meta):
        fields = [*LabelResolveSerializer.Meta.fields, "revoked"]
        read_only_fields = ["revoked_at", "code"]

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Everything that decides whether this label may be written as asked.

        Three of the rules mirror Label's check constraints --
        ``label_targets_exactly_one``, ``label_quantity_positive`` and
        ``label_quantity_iff_item``. The constraints remain the
        thing that enforces those; this only decides what a client is told,
        which is the distinction this module's docstring draws.

        The fourth constraint, ``label_code_is_crockford_base32``, has no
        mirror here and needs none: no client input reaches that column, so
        the refusal below is the whole of what a client can be told about it.

        The first two rules are the two halves of a code, and they are not the
        same kind of rule. Refusing a *changed* code mirrors the
        `label_code_is_printed` trigger, which holds it against every writer.
        Refusing a code at all on a create is this API's own contract: the
        admin supplies codes legitimately, so the database cannot tell a
        client's choice from an authorised one, and decision 0016 records why
        that stays here. The minting between them is not a rule at all, and
        neither is the last step: one fills in the column DRF left out because
        it is read-only, the other turns the boolean a client sends into the
        column the model stores.
        """
        submitted = self._submitted()
        if self.instance is None:
            if "code" in submitted:
                # Refused rather than quietly dropped, which is what a
                # read-only field does on its own: a client that chose a code,
                # got a 201 and printed what it asked for would be printing a
                # sticker this API cannot resolve.
                raise serializers.ValidationError(
                    {"code": "A label's code is minted when it is printed, and is not the client's to choose."}
                )
            attrs["code"] = Label.mint_unique_code()
        elif "code" in submitted and Label.normalise_code(str(submitted["code"])) != self.instance.code:
            # Sending the code back unchanged is a client returning the whole
            # row it read, which is a correction and not a rename -- so only a
            # different one is refused. Refused, again, rather than ignored:
            # the code is printed on a sticker already out on a shelf, and
            # changing it would 404 that sticker for good. A reprint is a new
            # label and a revocation of this one.
            raise serializers.ValidationError(
                {"code": "A label's code is printed on it and cannot be changed. Revoke it and print another."}
            )
        on_item = self._points_at(attrs, "item")
        on_location = self._points_at(attrs, "location")
        if on_item == on_location:
            raise serializers.ValidationError("A label points at exactly one item or one location.")
        quantity = attrs.get("quantity", getattr(self.instance, "quantity", None))
        if quantity is not None and quantity <= 0:
            raise serializers.ValidationError(
                "A scan of a label stands for some of something, so its quantity is positive."
            )
        if on_item and "quantity" in submitted and submitted["quantity"] is None:
            # The other half of `label_quantity_iff_item`. Before that
            # constraint the column was NOT NULL, so DRF refused this itself;
            # widening it for location labels took that refusal away and left
            # an item label with an explicit null reaching the database.
            raise serializers.ValidationError(
                {"quantity": "An item label says how much of its item one scan stands for, so it carries a quantity."}
            )
        if on_location:
            if "quantity" in submitted and submitted["quantity"] is not None:
                raise serializers.ValidationError(
                    "A location label stands for no quantity of anything, so it carries none."
                )
            # Emptied rather than left absent. DRF does not supply a default
            # here -- a model default makes the field `required=False` and
            # nothing more, so `quantity` is simply missing from attrs and
            # Django applies `default=1` when it instantiates the model. That
            # default serves the common case, an item label standing for one of
            # its item, and is exactly what `label_quantity_iff_item` refuses on
            # the other. Setting it to None is what stops the default arriving.
            attrs["quantity"] = None
        # Turned into the column the model stores here rather than in create()
        # and update(): `revoked` is not a field of Label, so it has to leave
        # the validated data before it reaches the model either way, and doing
        # it once is two methods fewer than doing it on both paths.
        revoked = attrs.pop("revoked", None)
        if revoked is not None:
            attrs["revoked_at"] = timezone.now() if revoked else None
        return attrs

    def _submitted(self) -> dict[str, Any]:
        """What the client actually sent, before DRF dropped its read-only keys.

        The only way to refuse a field rather than ignore it: ``code`` never
        reaches ``attrs``, so the submission itself is what has to be asked.
        """
        submitted = getattr(self, "initial_data", None)
        return submitted if isinstance(submitted, dict) else {}

    def _points_at(self, attrs: dict[str, Any], field: str) -> bool:
        """Whether the label points at ``field`` once this change is applied.

        Read off the id column when the submission does not carry the field:
        following the relation would fetch a whole item or location to ask
        whether there is one.
        """
        if field in attrs:
            return attrs[field] is not None
        return getattr(self.instance, f"{field}_id", None) is not None


class DetailSerializer(serializers.Serializer):
    """A refusal, said in a typed body so a client can render it.

    One shape for every refusal DRF renders as a bare sentence -- nothing here,
    and not for you. Two identical components would drift the moment either
    grew a machine-readable ``code``, which is the shape ThrottledSerializer
    already uses and decision 0015 argues for.

    A volunteer reaching an administrator's operation is told so rather than
    shown nothing (decision 0014 point 2), so the refusal is part of the
    contract and is described like any other response.

    ``code`` is the branch a client actually takes, and it is optional because
    only the 403 carries one: two refusals share that status and mean opposite
    things -- ``forbidden`` is a control to hide, ``reauthentication_required``
    is a prompt to show somebody who is entitled to the thing (decision 0014
    point 5). Nothing here is missing on a 404, where the sentence is the whole
    answer. The values are attached by ``exception_handler`` in inventory/api.py.
    """

    detail = serializers.CharField()
    code = serializers.CharField(
        required=False,
        help_text="`forbidden`, or `reauthentication_required` when signing in again is what fixes it.",
    )


class ThrottledSerializer(serializers.Serializer):
    """Too many submissions, too fast. Nothing was recorded; send it again later.

    ``code`` is constant, so a client branches on it instead of on the prose,
    and ``retry_after_seconds`` is the same number as the Retry-After header,
    so a countdown can be rendered rather than a sentence read.
    """

    detail = serializers.CharField()
    code = serializers.CharField()
    retry_after_seconds = serializers.IntegerField()
