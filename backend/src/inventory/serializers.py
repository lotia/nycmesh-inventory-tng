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
Closing that gap is inventory-tng-fi5.
"""

import datetime
from typing import Any

from django.utils import timezone
from rest_framework import serializers

from inventory.models import StockMovement, StockTransaction, Volunteer

# Far above a real cart of a couple of dozen scans. It exists only so that one
# request cannot open an unbounded write transaction against an append-only
# ledger.
MAX_MOVEMENTS = 500

# How far ahead of the server a client's clock may be and still be believed.
CLOCK_SKEW = datetime.timedelta(minutes=5)


class StockMovementInputSerializer(serializers.ModelSerializer):
    """One line of a submitted batch."""

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
    # No uniqueness validator, deliberately. The key is *meant* to arrive
    # twice -- that is what makes a retry safe -- and the view answers a
    # replay with the transaction the first attempt created. Left to DRF, a
    # duplicate key would be a 400 instead, and two genuinely concurrent
    # retries would race past the view's lookup only to be rejected here. The
    # partial unique index stays the arbiter; see the view's recovery path.
    idempotency_key = serializers.CharField(
        max_length=64,
        required=False,
        allow_null=True,
        validators=[],
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
