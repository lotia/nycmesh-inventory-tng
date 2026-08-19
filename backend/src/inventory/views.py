from typing import Any

from django.contrib.postgres.search import TrigramSimilarity
from django.db import IntegrityError, connection
from django.db.models import Q, QuerySet
from django.db.transaction import atomic
from django_filters.rest_framework import CharFilter, FilterSet
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.generics import ListCreateAPIView
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView

from inventory.models import StockBalance, StockMovement, StockTransaction, Volunteer
from inventory.serializers import (
    BatchInconsistentSerializer,
    BatchRejectedSerializer,
    StockTransactionCreateSerializer,
    StockTransactionSerializer,
    VolunteerSerializer,
)

# The one place the endpoint index is declared. The response body, the schema
# and the discovery test are all derived from it, so an endpoint cannot be
# advertised without being described, or described without being advertised.
#
# What it promises is narrower than it looks: every entry must answer a GET,
# because the discovery test follows each link. A write-only endpoint has no
# place to appear, which is inventory-tng-vr8.
ENDPOINTS = {
    "health": "healthz",
    "volunteers": "volunteers",
    "schema": "schema",
    "docs": "docs",
}


class ApiRootView(APIView):
    """The list of endpoints this API offers.

    Exists so the API is discoverable without reading the source or knowing the
    URL layout in advance: fetch this, follow the links.
    """

    # Deliberately public: an index of endpoint names is not sensitive, and a
    # client that cannot discover the login route cannot authenticate.
    permission_classes = [AllowAny]

    @extend_schema(
        summary="List the available endpoints",
        responses=inline_serializer(
            name="ApiRoot",
            # One field instance per entry, not dict.fromkeys: a serializer
            # field is bound to its name, so a single instance shared across
            # the keys keeps whichever name bound it first and the schema
            # describes one property instead of all of them.
            fields={key: serializers.URLField() for key in ENDPOINTS},
        ),
    )
    def get(self, request: Request) -> Response:
        return Response({key: reverse(name, request=request) for key, name in ENDPOINTS.items()})


class HealthCheckView(APIView):
    """Liveness and readiness probe used by Kubernetes.

    Issues a trivial query so the check fails when the database is
    unreachable, rather than reporting healthy while unable to serve.
    See docs/deployment.md.
    """

    # Deliberately public: probes run before authentication exists.
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Liveness and readiness probe",
        responses=inline_serializer(name="HealthCheck", fields={"status": serializers.CharField()}),
    )
    def get(self, request: Request) -> Response:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return Response({"status": "ok"})


# Which sides a movement must carry for the batch to mean what its kind says.
# Stated per kind rather than derived, because the mapping is a domain fact --
# a check-out that takes stock from nowhere is not a check-out -- and reading
# it should not require reconstructing an argument. The rules, and why
# adjustments and counts are absent, are in decision 0011 section 6.
KIND_REQUIRES: dict[str, tuple[tuple[str, ...], str]] = {
    StockTransaction.Kind.CHECKOUT: (
        ("from_location",),
        "A check out moves stock out of somewhere.",
    ),
    StockTransaction.Kind.CONSUMPTION: (
        ("from_location",),
        "Stock used at a job comes out of somewhere.",
    ),
    StockTransaction.Kind.CHECKIN: (
        ("to_location",),
        "A check in brings stock back to somewhere.",
    ),
    StockTransaction.Kind.RECEIPT: (
        ("to_location",),
        "A receipt brings stock in from outside, so it must land somewhere.",
    ),
    StockTransaction.Kind.TRANSFER: (
        ("from_location", "to_location"),
        "A transfer moves stock between two places.",
    ),
}


def _messages(value: object) -> list[str]:
    """DRF states an error as a string or as a list of them."""
    if isinstance(value, list):
        return [message for item in value for message in _messages(item)]
    return [str(value)]


def _errors(field: str, value: object, index: int | None = None) -> list[dict[str, object]]:
    """One rendered error per message DRF reported for a field."""
    return [{"index": index, "field": field, "detail": message} for message in _messages(value)]


def _line_errors(detail: object) -> list[dict[str, object]]:
    """Errors from the movements list, each tagged with the line it came from.

    DRF keys a nested list's failures by position and includes only the lines
    that failed, so a string key is a complaint about the list itself -- that
    it was empty, say. Anything else is reported against the batch, which
    turns a shape we did not expect into a 400 the volunteer can read rather
    than a 500 they cannot.
    """
    if not isinstance(detail, dict):
        return _errors("movements", detail)
    errors: list[dict[str, object]] = []
    for index, line in detail.items():
        if isinstance(index, int) and isinstance(line, dict):
            for field, value in line.items():
                errors.extend(_errors(field, value, index))
        else:
            errors.extend(_errors("movements", line))
    return errors


def _rejected(errors: dict[str, object]) -> list[dict[str, object]]:
    """Flatten DRF's error dict into one list the client can render in order."""
    flattened: list[dict[str, object]] = []
    for field, detail in errors.items():
        flattened.extend(_line_errors(detail) if field == "movements" else _errors(field, detail))
    return flattened


def _inconsistent_lines(kind: str, movements: list[dict[str, object]]) -> list[dict[str, object]]:
    """Lines that are valid on their own but disagree with the batch's kind."""
    required, _ = KIND_REQUIRES.get(kind, ((), ""))
    return [
        {"index": index, "detail": f"has no {side}"}
        for index, movement in enumerate(movements)
        for side in required
        if movement.get(side) is None
    ]


def _drained_by(movements: list[StockMovement]) -> set[tuple[int, int]]:
    """The (item, location) pairs a recorded batch took stock out of."""
    return {
        # The id columns, not the relations: reading movement.item would fetch
        # a row nothing here needs.
        (movement.item_id, movement.from_location_id)  # ty: ignore[unresolved-attribute]
        for movement in movements
        if movement.from_location_id is not None  # ty: ignore[unresolved-attribute]
    }


def _negative_balances(drained: set[tuple[int, int]]) -> list[dict[str, object]]:
    """Where stock stands below zero at the places a batch drew from.

    Reported, never refused. Volunteers had no way to say "the shelf disagrees
    with the system", so they faked corrections instead; see
    docs/decisions/0008-stock-ledger-transfer-graph.md. The shelf is the
    authority, and the answer is a stock count rather than a blocked volunteer.
    """
    if not drained:
        return []
    return [
        {
            "item": balance.item.pk,
            "location": balance.location.pk,
            "balance": balance.quantity,
            "detail": f"{balance.item} at {balance.location} is now {balance.quantity}. Count it when you can.",
        }
        for balance in StockBalance.objects.filter(pk__in=list(drained), quantity__lt=0).select_related(
            "item",
            "location",
        )
    ]


def _is_duplicate_key(error: IntegrityError) -> bool:
    """Whether this integrity failure is the idempotency key colliding.

    psycopg reports the constraint by name, so the recovery path can be sure
    it is absorbing the retry it was written for rather than any other
    constraint that happens to fire at the same moment.
    """
    diagnostic = getattr(error.__cause__, "diag", None)
    return getattr(diagnostic, "constraint_name", None) == "stock_transaction_unique_idempotency_key"


class StockTransactionCreateView(APIView):
    """Record one batch: one transaction, one movement per line, one commit.

    This is the endpoint the project exists for. The system it replaces could
    carry one item per form submission, so people submitted the same form over
    and over; here that is one request. See
    docs/decisions/0011-qr-batch-scanning.md.
    """

    # JSON only. The payload's required field is an array of objects, which a
    # form encoding cannot carry, so advertising those parsers would publish a
    # request shape that is guaranteed to fail.
    parser_classes = [JSONParser]

    @extend_schema(
        summary="Record a batch of stock movements",
        request=StockTransactionCreateSerializer,
        responses={
            201: StockTransactionSerializer,
            200: StockTransactionSerializer,
            400: BatchRejectedSerializer,
            409: BatchInconsistentSerializer,
        },
    )
    def post(self, request: Request) -> Response:
        # Validation first, so a bare key never reaches the lookup: answering
        # a request carrying nothing but a key with somebody else's
        # transaction would hand out a batch to whoever guesses one.
        serializer = StockTransactionCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    "detail": "Nothing was saved. Every line that needs fixing is listed.",
                    "errors": _rejected(serializer.errors),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # The replay is matched on the key alone, with no hash of the body.
        # Two carts under one key is an invisible client bug; turning it into
        # an error the volunteer cannot act on helps nobody. See decision 0011.
        key = serializer.validated_data.get("idempotency_key")
        replayed = self._already_recorded(key)
        if replayed is not None:
            return Response(self._body(replayed), status=status.HTTP_200_OK)

        lines = serializer.validated_data["movements"]
        kind = serializer.validated_data["kind"]
        inconsistent = _inconsistent_lines(kind, lines)
        if inconsistent:
            return Response(
                {
                    "detail": KIND_REQUIRES[kind][1],
                    "kind": kind,
                    "inconsistent": inconsistent,
                },
                status=status.HTTP_409_CONFLICT,
            )

        try:
            recorded = self._record(serializer.validated_data)
        except IntegrityError as error:
            # Two retries of the same cart arrived at once and the unique
            # index caught the second. The first one won; return what it made.
            # Checked against the constraint that actually fired, because
            # answering any other integrity failure with somebody's earlier
            # transaction would report success for a batch that wrote nothing.
            raced = self._already_recorded(key) if _is_duplicate_key(error) else None
            if raced is None:
                raise
            return Response(self._body(raced), status=status.HTTP_200_OK)

        return Response(self._body(recorded), status=status.HTTP_201_CREATED)

    @staticmethod
    def _already_recorded(key: str | None) -> StockTransaction | None:
        """Find the transaction a key already recorded, if there is one.

        The key arrives here already validated, so it is the value the write
        path would store rather than the raw request value -- a lookup on the
        latter could miss the very row the unique index is about to reject,
        and the retry this exists to absorb would surface as a 500.

        order_by() drops the model's default ordering: this reads at most one
        row through a unique index and has no use for a sort.
        """
        if not key:
            return None
        return StockTransaction.objects.filter(idempotency_key=key).order_by().first()

    @staticmethod
    @atomic
    def _record(batch: dict[str, Any]) -> StockTransaction:
        """All or nothing. A partly posted batch writes ledger rows nobody
        intended, and the ledger is append-only, so they could only ever be
        compensated.
        """
        recorded = StockTransaction.objects.create(
            **{name: value for name, value in batch.items() if name != "movements"},
        )
        StockMovement.objects.bulk_create(StockMovement(transaction=recorded, **line) for line in batch["movements"])
        return recorded

    @staticmethod
    def _body(recorded: StockTransaction) -> dict[str, Any]:
        """The response for a batch, whether just recorded or replayed.

        Warnings are read from the balances as they stand now rather than
        remembered from the write. A retry exists because the first response
        was lost, and a volunteer who never saw "the shelf is negative" still
        needs telling; reading again reports what is true now, which is the
        thing worth acting on either way.

        Read outside the write transaction, deliberately. A warning is
        advisory, and failing to compute one must not discard 24 scans the
        volunteer has already pressed Save on.
        """
        # Read once and used twice: the serializer renders these lines and the
        # warnings are derived from the same rows.
        # Ordered here rather than on the model: the submitted order matters
        # to this response, and a default ordering would attach itself to
        # every StockMovement query in the project.
        lines = list(recorded.movements.order_by("id"))  # ty: ignore[unresolved-attribute]
        recorded.lines = lines  # ty: ignore[unresolved-attribute]
        recorded.warnings = _negative_balances(_drained_by(lines))  # ty: ignore[unresolved-attribute]
        return StockTransactionSerializer(recorded).data


class VolunteerFilter(FilterSet):
    """Fuzzy name search over the pick-list.

    Two lookups, not one. ``icontains`` is what makes typing the first few
    letters work at all -- trigram similarity to a two-letter fragment is near
    zero -- while the similarity match is what finds Shaun when the ledger
    says Sean.

    Only the similarity half uses the GIN trigram index: ``icontains``
    compiles to ``UPPER(display_name) LIKE ...``, which an index on the bare
    column cannot serve, and the two are ORed, so the plan scans the table and
    sorts. That is the right trade at this size -- 65 volunteers -- and the
    alternative, a case-sensitive ``contains``, would stop "sean" finding
    "Sean", which is how the picker is actually used.
    """

    search = CharFilter(method="by_name", label="Fuzzy name search")

    def by_name(self, queryset: QuerySet[Volunteer], name: str, value: str) -> QuerySet[Volunteer]:
        return (
            queryset.filter(Q(display_name__icontains=value) | Q(display_name__trigram_similar=value))
            .annotate(similarity=TrigramSimilarity("display_name", value))
            # Closest first; the rest is the model's ordering, tie-break and
            # all, because a search result is paginated like any other list.
            .order_by("-similarity", "display_name", "pk")
        )


class VolunteerListCreateView(ListCreateAPIView):
    """The volunteer pick-list, and the way onto it.

    Volunteers are a pick-list with no password (decision 0008 point 5). The
    client searches this before offering to add anyone, which is what stops a
    second generation of the duplicate spellings that decision counts.
    """

    serializer_class = VolunteerSerializer
    filterset_class = VolunteerFilter
    # Ordering comes from the model, tie-break included.
    queryset = Volunteer.objects.selectable()
