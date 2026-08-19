"""Serializers for the API.

Two different kinds of rule live here, and the difference matters.

Most are *reporting*. The ledger's invariants are database constraints
(docs/data-model.md) and they stay that way; a constraint violation surfaces
as a 500 naming no line, and a volunteer holding a phone in a basement needs
to be told which of their 24 scans to fix. So the per-line movement rules are
stated here as well, and the database remains the thing that enforces them.

Two are the API's own, with no database counterpart: the refusal of a batch
dated in the future, and the exclusion of merged and inactive volunteers as
the actor. Neither is expressible as a check constraint -- one needs the
current time, the other another table -- so anything writing past this module,
including the admin and the planned sheet importer, is not protected by them.
The kind-to-sides rule in views.py is a third of the same kind. Closing that
gap, for all three, is inventory-tng-fi5.
"""

import datetime
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
        key = (self.field_name, data)
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


class CategorySerializer(serializers.ModelSerializer):
    """A grouping of items, nestable."""

    class Meta:
        model = Category
        fields = ["id", "name", "parent"]


class LocationSerializer(serializers.ModelSerializer):
    """Somewhere stock can be, including a volunteer holding it."""

    class Meta:
        model = Location
        fields = ["id", "name", "kind", "parent", "held_by", "active"]


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
    """One label in the map the client caches.

    Drops ``revoked_at``, which this queryset guarantees is null, a few
    hundred times over. See LabelListView for why this response's size is
    worth caring about.
    """

    class Meta(LabelResolveSerializer.Meta):
        fields = ["code", "kind", "quantity", "item", "location"]


class NotFoundSerializer(serializers.Serializer):
    """Nothing here. Said in a typed body so a client can render it."""

    detail = serializers.CharField()


class ThrottledSerializer(serializers.Serializer):
    """Too many submissions, too fast. Nothing was recorded; send it again later.

    ``code`` is constant, so a client branches on it instead of on the prose,
    and ``retry_after_seconds`` is the same number as the Retry-After header,
    so a countdown can be rendered rather than a sentence read.
    """

    detail = serializers.CharField()
    code = serializers.CharField()
    retry_after_seconds = serializers.IntegerField()
