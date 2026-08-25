from typing import Any, NamedTuple, cast

import structlog
from django.conf import settings
from django.contrib.postgres.search import TrigramSimilarity
from django.db import IntegrityError, connection
from django.db.models import Prefetch, Q, QuerySet
from django.db.transaction import atomic
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django_filters.rest_framework import CharFilter, FilterSet
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateAPIView
from rest_framework.permissions import SAFE_METHODS, AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView

from inventory import telemetry
from inventory.labels import refusal_page, sheet
from inventory.models import (
    Category,
    Device,
    Item,
    Label,
    Location,
    StockBalance,
    StockMovement,
    StockTransaction,
    Volunteer,
)
from inventory.permissions import (
    DEVICE_STATES,
    VOLUNTEER_APPEND,
    is_administrator,
    presented_device,
    recently_authenticated,
)
from inventory.serializers import (
    BatchInconsistentSerializer,
    BatchRejectedSerializer,
    CategorySerializer,
    ClientFailureSerializer,
    DeviceEnrolmentSerializer,
    ItemDetailSerializer,
    ItemSerializer,
    LabelMapSerializer,
    LabelResolveSerializer,
    LabelSerializer,
    LocationSerializer,
    StockTransactionCreateSerializer,
    StockTransactionSerializer,
    VolunteerConflictSerializer,
    VolunteerDetailSerializer,
    VolunteerSerializer,
)
from inventory.throttling import APPEND_THROTTLES, DEVICE_ENROLMENT_THROTTLES, REPORT_THROTTLES
from inventory_tng import debugging, devices
from inventory_tng.forwarded import address_or_none, client_address

# Named for the module, which is what puts `inventory.views` in the `logger`
# column. Every record it writes is inside a request, so it carries that
# request's id and route without being told -- `inventory_tng.context`.
log = structlog.get_logger(__name__)

# The one place the entry points are declared. The response body, the schema
# and the discovery test are all derived from it, so an entry point cannot be
# advertised without being described, or described without being advertised.
#
# Entry points, not every endpoint. Two kinds of endpoint cannot be a name and
# a URL: one addressed per row, whose URL is a template rather than a link,
# and one reached by a method other than GET. The second used to keep
# `POST /api/stock/transactions` -- the endpoint this project exists for --
# out of the index entirely, because the discovery test GET each advertised
# link and asserted 200.
#
# So a collection appears here whatever methods it takes, and what those
# methods are is the schema's to say. `schema` is itself an entry, which is
# what makes that reachable from here; the discovery test asserts that every
# link is described there, rather than that every link answers a GET.
# Resolved as inventory-tng-vr8.
ENDPOINTS = {
    "health": "healthz",
    "volunteers": "volunteers",
    "items": "items",
    "locations": "locations",
    "categories": "categories",
    "labels": "labels",
    "stock": "stock-transactions",
    "me": "me",
    "devices": "devices",
    "schema": "schema",
    "docs": "docs",
}


# ty reads django-stubs' signature for method_decorator, which does not
# describe a decorator this generic. See DEVELOPERS.md#typing.
@method_decorator(ensure_csrf_cookie, name="get")  # ty: ignore[invalid-argument-type]
class ApiRootView(APIView):
    """Where this API starts.

    Exists so a client does not have to know the URL layout in advance: fetch
    this, and every collection is a link. What each one accepts, and the
    endpoints addressed per row, are in the schema this also links to --
    the index says where things are, the schema says what they take.

    Fetching it also hands the browser a CSRF token. Session authentication
    enforces CSRF on every write, and a single-page app never renders a Django
    template, so without this nothing would set the cookie and no browser
    could post anything. The index is the right place for it: it is what a
    client fetches first, and it is public, so the token is available before
    anyone has logged in.
    """

    # Deliberately public: an index of endpoint names is not sensitive, and a
    # client that cannot discover the login route cannot authenticate.
    permission_classes = [AllowAny]

    @extend_schema(
        summary="List the API's entry points",
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
    """Readiness probe: whether this process can serve a request.

    Issues a trivial query so the check fails when the database is
    unreachable, rather than reporting healthy while unable to serve. Failing
    it stops traffic reaching this pod rather than restarting it, which is
    what a dependency's outage calls for.

    Not the liveness probe. That is `GET /api/livez`, and what the two ask,
    what pointing both at this one costs, and what the split gives up in
    exchange are docs/deployment.md#health-checks.
    """

    # Deliberately public: probes run before authentication exists.
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Readiness probe",
        responses=inline_serializer(name="HealthCheck", fields={"status": serializers.CharField()}),
    )
    def get(self, request: Request) -> Response:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return Response({"status": "ok"})


class LivenessCheckView(APIView):
    """Liveness probe: whether this process is still running.

    Reaches for nothing of its own -- no database, no cache, no disk -- and
    that is the whole of its design. A failed liveness probe is answered by
    killing the container, so it may only report a fault that killing the
    container repairs; every dependency this process has belongs on the
    readiness probe at `GET /api/healthz` instead. Why that division is not a
    matter of taste is docs/deployment.md#health-checks.

    "Of its own" is exact rather than modest. What surrounds a request still
    costs what it costs: a caller presenting a session cookie is looked up by
    the middleware before this method is reached, and a caller asking for HTML
    is answered by the browsable API. A kubelet does neither, which is why the
    probe is free; anything else pointed at this path is not a kubelet and
    should be weighed as what it is.
    """

    # Deliberately public: probes run before authentication exists.
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Liveness probe",
        responses=inline_serializer(name="LivenessCheck", fields={"status": serializers.CharField()}),
    )
    def get(self, request: Request) -> Response:
        # A different word from the readiness probe's, so that a person with
        # curl and one of these responses knows which of the two they reached.
        # Both words are written down in docs/deployment.md#health-checks,
        # because a distinction nobody can look up is not one.
        return Response({"status": "alive"})


class KindSides(NamedTuple):
    """What a kind means for the two sides of each of its movements."""

    required: tuple[str, ...]
    forbidden: tuple[str, ...]
    detail: str


# Which sides a movement must carry, and which it must not, for the batch to
# mean what its kind says. Stated per kind rather than derived, because the
# mapping is a domain fact -- a check-out that takes stock from nowhere is not
# a check-out -- and reading it should not require reconstructing an argument.
# The rules, and why adjustments and counts are absent, are in decision 0011
# section 6. The database holds this too, in stock_movement_matches_kind
# (migration 0008, decision 0016), so what is here reports the rule per line
# and what is there enforces it for every writer. The two are written twice
# and a test walks this mapping against the triggers to stop them drifting.
#
# Named arguments, not positional: `required` and `forbidden` are two tuples of
# the same type sitting next to each other, and transposing them would invert a
# domain rule silently -- a receipt that must not land anywhere.
KIND_SIDES: dict[str, KindSides] = {
    StockTransaction.Kind.CHECKOUT: KindSides(
        required=("from_location",),
        forbidden=(),
        detail="A check out moves stock out of somewhere.",
    ),
    StockTransaction.Kind.CONSUMPTION: KindSides(
        required=("from_location",),
        forbidden=("to_location",),
        detail="Stock used at a job comes out of somewhere and does not arrive anywhere.",
    ),
    StockTransaction.Kind.CHECKIN: KindSides(
        required=("to_location",),
        forbidden=(),
        detail="A check in brings stock back to somewhere.",
    ),
    StockTransaction.Kind.RECEIPT: KindSides(
        required=("to_location",),
        forbidden=("from_location",),
        detail="A receipt brings stock in from outside, so it lands somewhere and leaves nowhere.",
    ),
    StockTransaction.Kind.TRANSFER: KindSides(
        required=("from_location", "to_location"),
        forbidden=(),
        detail="A transfer moves stock between two places.",
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
    it was empty, say -- and is reported against the batch. A position always
    keeps its index, because pointing at the line is the whole promise of this
    response; a line whose failure is a bare message rather than a field map
    (``null`` in the array, say) is reported against ``movements`` at that
    index. Anything else falls back to the batch, which turns a shape we did
    not expect into a 400 the volunteer can read rather than a 500 they
    cannot.
    """
    if not isinstance(detail, dict):
        return _errors("movements", detail)
    errors: list[dict[str, object]] = []
    for index, line in detail.items():
        if not isinstance(index, int):
            errors.extend(_errors("movements", line))
        elif isinstance(line, dict):
            for field, value in line.items():
                errors.extend(_errors(field, value, index))
        else:
            errors.extend(_errors("movements", line, index))
    return errors


def _rejected(errors: dict[str, object]) -> list[dict[str, object]]:
    """Flatten DRF's error dict into one list the client can render in order."""
    flattened: list[dict[str, object]] = []
    for field, detail in errors.items():
        flattened.extend(_line_errors(detail) if field == "movements" else _errors(field, detail))
    return flattened


def _inconsistent_lines(kind: str, movements: list[dict[str, object]]) -> list[dict[str, object]]:
    """Lines that are valid on their own but disagree with the batch's kind,
    in either direction: a missing side it needs, or a side it must not have.
    """
    sides = KIND_SIDES.get(kind)
    if sides is None:
        return []
    return [
        {"index": index, "detail": detail}
        for index, movement in enumerate(movements)
        for detail in (
            *(f"has no {side}" for side in sides.required if movement.get(side) is None),
            *(f"has a {side}" for side in sides.forbidden if movement.get(side) is not None),
        )
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

    Reported, never refused, for the reason
    docs/decisions/0011-qr-batch-scanning.md gives under "Insufficient stock is
    a warning, not a rejection".

    Ordered, because a set has no order and the warnings are a list the client
    renders: without this a replay could show the same warnings rearranged,
    reading as a change when nothing changed. By the id columns rather than
    the relations, for the reason on ItemListView.
    """
    if not drained:
        return []
    return [
        {
            # The id columns, not the relations, for the reason in _drained_by:
            # the rows are joined for the sentence below, not for these two
            # numbers, which the balance already carries.
            "item": balance.item_id,  # ty: ignore[unresolved-attribute]
            "location": balance.location_id,  # ty: ignore[unresolved-attribute]
            "balance": balance.quantity,
            "detail": f"{balance.item} at {balance.location} is now {balance.quantity}. Count it when you can.",
        }
        # sorted(), not list(): `pk__in` on a composite primary key rejects a
        # set outright, so the conversion is required, and sorting it costs
        # nothing while making the emitted SQL stable. The row order the
        # response needs comes from the order_by below, not from here.
        for balance in StockBalance.objects.filter(pk__in=sorted(drained), quantity__lt=0)
        .select_related("item", "location")
        .order_by("item_id", "location_id")
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

    # One of the two writes a volunteer makes; see VOLUNTEER_APPEND.
    permission_classes = VOLUNTEER_APPEND

    # Rate limited; see inventory/throttling.py.
    throttle_classes = APPEND_THROTTLES

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
            # Which fields were wrong and how many lines, and not the messages
            # themselves: DRF builds those from the values submitted -- "object
            # with code=… does not exist" -- so they carry whatever somebody
            # typed. The field names are the serializer's own and are static.
            # Walked once and used twice: `_rejected` visits every movement,
            # and a batch carries up to five hundred.
            rejected = _rejected(serializer.errors)
            log.warning("batch rejected", reason=sorted(serializer.errors), lines=len(rejected))
            telemetry.APPENDS.add(1, {"outcome": "rejected"})
            return Response(
                {
                    "detail": "Nothing was saved. Every line that needs fixing is listed.",
                    "errors": rejected,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # The replay is matched on the key and the actor, with no hash of the
        # body. Two carts under one key is an invisible client bug; turning it
        # into an error the volunteer cannot act on helps nobody. But the key
        # is minted by the client, so it is only unique to the person who
        # minted it -- matching across everybody would answer one volunteer's
        # batch with another's. See decision 0011.
        key = serializer.validated_data.get("idempotency_key")
        actor = serializer.validated_data["actor"]
        replayed = self._already_recorded(actor, key)
        if replayed is not None:
            log.info("batch replayed", volunteer=actor.pk, transaction=replayed.pk)
            telemetry.APPENDS.add(1, {"outcome": "replayed"})
            return Response(self._body(replayed), status=status.HTTP_200_OK)

        lines = serializer.validated_data["movements"]
        kind = serializer.validated_data["kind"]
        inconsistent = _inconsistent_lines(kind, lines)
        if inconsistent:
            log.warning("batch inconsistent", kind=kind, volunteer=actor.pk, lines=len(inconsistent))
            telemetry.APPENDS.add(1, {"outcome": "inconsistent", "kind": kind})
            return Response(
                {
                    "detail": KIND_SIDES[kind].detail,
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
            raced = self._already_recorded(actor, key) if _is_duplicate_key(error) else None
            if raced is None:
                raise
            log.info("batch raced", volunteer=actor.pk, transaction=raced.pk)
            telemetry.APPENDS.add(1, {"outcome": "raced"})
            return Response(self._body(raced), status=status.HTTP_200_OK)

        log.info("batch recorded", kind=kind, volunteer=actor.pk, transaction=recorded.pk, lines=len(lines))
        telemetry.APPENDS.add(1, {"outcome": "recorded", "kind": kind})
        telemetry.MOVEMENTS.add(len(lines), {"kind": kind})
        return Response(self._body(recorded), status=status.HTTP_201_CREATED)

    @staticmethod
    def _already_recorded(actor: Volunteer, key: str | None) -> StockTransaction | None:
        """Find the transaction this actor already recorded under this key.

        The key arrives here already validated, so it is the value the write
        path would store rather than the raw request value -- a lookup on the
        latter could miss the very row the unique index is about to reject,
        and the retry this exists to absorb would surface as a 500.

        order_by() drops the model's default ordering: this reads at most one
        row through a unique index and has no use for a sort.
        """
        if not key:
            return None
        return StockTransaction.objects.filter(actor=actor, idempotency_key=key).order_by().first()

    @staticmethod
    @atomic
    def _record(batch: dict[str, Any]) -> StockTransaction:
        """All or nothing, for the reason
        docs/decisions/0011-qr-batch-scanning.md gives under "All or nothing,
        but the rejection names every bad line".
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


class ReadsAndWritesDiffer:
    """A collection whose write shape is not its read shape.

    Three of these endpoints have one, for three unrelated reasons -- a list
    row is smaller than the item behind it, the cached label map drops what it
    can, and a scan resolves a label rather than editing it. The rule that
    picks between them is the same rule each time, so it is stated here and the
    views state only the two serializers.
    """

    #: What a read answers with. DRF's own attribute, so a view with no write
    #: shape needs nothing from this class.
    serializer_class: type[serializers.BaseSerializer]
    #: What an unsafe method takes and answers with.
    write_serializer_class: type[serializers.BaseSerializer]
    request: Request

    def get_serializer_class(self) -> type[serializers.BaseSerializer]:
        if self.request.method in SAFE_METHODS:
            return self.serializer_class
        return self.write_serializer_class


class WithdrawnRows:
    """``?withdrawn=true`` lists what the collection has taken out of the list.

    DetailView lets an administrator read and repair a row the list beside it
    has withdrawn -- a retired item or location, a merged volunteer -- but only
    if they already know its id, and nothing offered a way to arrive at one.
    Undoing a retirement was therefore reachable only through the Django admin,
    which decision 0014 point 4 keeps for "a broken deployment, three in the
    morning" rather than for routine repair, and that undercuts point 1 of the
    same decision: editing happens in place, where the thing already is.

    A parameter rather than widening what an administrator's list returns. The
    pick-list is the cart's, and an administrator filling a batch must see the
    same rows a volunteer does or they will scan something nobody else can.
    So the default is unchanged for everybody, and this asks a different
    question: not "what may I pick" but "what did I withdraw".

    Refused rather than ignored for anybody else. Quietly serving the offered
    rows to a volunteer who asked for the withdrawn ones would answer a
    question they did not ask.
    """

    #: Every row, including the withdrawn ones. The collection's own queryset.
    every_row: QuerySet[Any]
    #: DRF's own, declared for the same reason ReadsAndWritesDiffer declares it.
    request: Request

    #: For the schema, since nothing generates a parameter a view reads by hand
    #: -- and an undocumented parameter is a contract no client can discover.
    #: Applied as @WITHDRAWN_SCHEMA below. See DEVELOPERS.md#the-api-schema.
    WITHDRAWN_PARAMETER = OpenApiParameter(
        name="withdrawn",
        type=OpenApiTypes.BOOL,
        location=OpenApiParameter.QUERY,
        description=(
            "List the rows this collection has withdrawn -- retired, or merged "
            "away -- instead of the ones it offers. Administrators only."
        ),
    )

    def get_queryset(self) -> QuerySet[Any]:
        # The collection's own, whatever the view beside this decided it is.
        # Reached through the MRO rather than by naming a base: this is a mixin
        # in front of whichever generic view the collection uses, and it has no
        # base of its own to declare.
        offered = cast("QuerySet[Any]", super().get_queryset())  # ty: ignore[unresolved-attribute]
        asked = self.request.query_params.get("withdrawn")
        if asked is None:
            return offered
        # The schema says boolean, so a generated client will send `false` for
        # the default and must get the default rather than a 400. The values
        # are DRF's own BooleanField vocabulary, so what the parameter accepts
        # is what every other boolean in this API accepts.
        try:
            wanted = serializers.BooleanField().to_internal_value(asked)
        except ValidationError as refused:
            # Re-keyed, so the body names the parameter rather than answering
            # with a bare sentence the caller has to guess the subject of.
            raise ValidationError({"withdrawn": refused.detail}) from refused
        if not wanted:
            return offered
        # Asked after the value is understood: a volunteer who sends nonsense
        # is told it is nonsense, and one who asks properly is told they may
        # not. The other order explains an administrators-only parameter to
        # somebody whose real problem was a typo.
        if not is_administrator(self.request.user):
            raise PermissionDenied("Only an administrator may list withdrawn rows.")
        # The difference, rather than a second statement of what withdrawal
        # means. `active=False`, `merged_into`, `revoked_at` are each defined
        # once, on the offered queryset, and asking for everything-except-those
        # cannot drift from them.
        return self.every_row.exclude(pk__in=offered.values("pk"))


#: One decorator rather than the same line on three views.
WITHDRAWN_SCHEMA = extend_schema_view(get=extend_schema(parameters=[WithdrawnRows.WITHDRAWN_PARAMETER]))


class RecordsWhatItCreated:
    """Say that a row was added, on whichever collection this is mixed into.

    A mixin rather than four `perform_create` overrides, because what is worth
    recording is the same sentence in each: an administrator added a row to a
    named collection. The volunteer list is not one of these -- it is written
    by a volunteer, it has three outcomes rather than one, and it says so
    itself.

    The row's identifier and nothing else. A catalogue row holds names --
    an item's, a location's -- and a record of one being added is not a reason
    for those to reach a collector.
    """

    def perform_create(self, serializer: Any) -> None:
        # A mixin sits in front of the generic view, so what this defers to is
        # whatever it was mixed into and cannot be named here -- which a type
        # checker is right to point out and which the annotation admits.
        creating: Any = super()
        creating.perform_create(serializer)
        collection = serializer.Meta.model._meta.model_name
        log.info("row added", collection=collection, code=serializer.instance.pk)
        telemetry.CATALOGUE_EDITS.add(1, {"collection": collection})


class DetailView(RetrieveUpdateAPIView):
    """One row of a collection, read by anyone and edited by an administrator.

    An administrator sees rows the list beside this one has withdrawn --
    putting a retired one back is the reason a withdrawn row is reachable at
    all -- and everybody else sees exactly what that list offers.

    Both querysets are the collection's own, named once above the pair of
    views that share them. Restating either here would be a second statement
    of what is visible and of how a row is shaped, and the copy that drifts is
    the one that leaks.
    """

    #: Every row, shaped as the collection beside this shapes them.
    every_row: QuerySet[Any]
    #: The subset that collection offers.
    offered_rows: QuerySet[Any]

    # No PUT. Every one of these rows carries a field that decides whether the
    # list still offers it -- `active`, `merged_into`, `revoked_at` -- and a
    # replacement that omitted one would withdraw or restore a row without
    # saying so anywhere in the request. A row is corrected, never replaced.
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self) -> QuerySet[Any]:
        chosen = self.every_row if is_administrator(self.request.user) else self.offered_rows
        # Re-evaluated per request, the way DRF's own get_queryset is, so a
        # class attribute cannot serve one request's rows to the next.
        return chosen.all()

    def perform_update(self, serializer: Any) -> None:
        """Say what was edited, from where the serializer is already in hand.

        Here rather than in `update` below, because the row and the fields are
        both already built at this point: asking `update` for a serializer of
        its own to read `.fields` off made a third one per request, and every
        one of those deep-copies every declared field.
        """
        super().perform_update(serializer)
        # One record for every edit to a row that already existed, whichever
        # collection it is in. The collection is the model's own name rather
        # than the URL, so it does not change when a route does; the fields
        # changed are named and their values are not, because a catalogue row
        # holds a volunteer's name.
        collection = self.get_queryset().model._meta.model_name
        # Intersected with the serializer's own fields, and that is the whole
        # of the fix: `request.data`'s keys are the CALLER's, so a PATCH body
        # carrying an unknown key -- which DRF ignores -- put arbitrary text of
        # unbounded length onto the record. `reason` is an allowlisted key and
        # the allowlist checks names rather than values, so it went out
        # verbatim. `redaction.ALLOWED_LOG_KEYS` states the rule: a reason is a
        # word chosen in this code, never one chosen by whoever called.
        named = sorted(set(self.request.data) & set(serializer.fields))
        log.info("row edited", collection=collection, code=self.kwargs.get(self.lookup_field), reason=named)
        telemetry.CATALOGUE_EDITS.add(1, {"collection": collection})

    def update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        response = super().update(request, *args, **kwargs)
        # DRF empties the prefetch cache before rendering the row it just
        # wrote, so anything prefetched would come back through the plain
        # related managers instead: for an item that is every sticker, revoked
        # ones included, where the list and the GET both promise the packaging.
        # Re-read through the collection's own queryset, so one row is the same
        # shape however it was asked for.
        response.data = self.get_serializer(self.get_object()).data
        return response


# Ordering comes from the model, tie-break included. Who is offered is
# VolunteerManager.selectable()'s to say and is not restated here: merged and
# retired records exist for whoever may repair them, which is the reason a
# clash with one is answered by the 409 in decision 0015 rather than a 400.
VOLUNTEERS = Volunteer.objects.all()
OFFERED_VOLUNTEERS = Volunteer.objects.selectable()


class VolunteerFilter(FilterSet):
    """Fuzzy search over the pick-list, by name or by identifier.

    Two lookups over the name, not one. ``icontains`` is what makes typing the
    first few letters work at all -- trigram similarity to a two-letter
    fragment is near zero -- while the similarity match is what finds Shaun
    when the ledger says Sean.

    The identifiers are searched as well, and exactly. VolunteerSerializer
    carries ``email`` and ``slack_id`` because two people called Sean are why
    the list is searched before anybody adds themselves; a volunteer who types
    their own address and is shown nobody adds a duplicate, which is the one
    outcome this endpoint exists to prevent, arrived at through the fields
    added to prevent it. Substring is not offered on them. Typing an identifier
    is a deliberate identification and a whole one, and half an address is
    nobody's: matching a fragment would let a near-miss read as a hit, on
    exactly the field that is there to tell two people apart. Not a privacy
    measure -- the pick-list already shows every volunteer's identifiers to
    anybody who may read it.

    Only the similarity half uses the GIN trigram index: ``icontains``
    compiles to ``UPPER(display_name) LIKE ...``, which an index on the bare
    column cannot serve, and the lookups are ORed, so the plan scans the table
    and sorts. That is the right trade at this size -- under a hundred -- and the
    alternative, a case-sensitive ``contains``, would stop "sean" finding
    "Sean", which is how the picker is actually used.
    """

    search = CharFilter(method="by_name_or_identifier", label="Fuzzy name search, or an exact identifier")

    def by_name_or_identifier(
        self,
        queryset: QuerySet[Volunteer],
        name: str,
        value: str,
    ) -> QuerySet[Volunteer]:
        return (
            queryset.filter(
                Q(display_name__icontains=value)
                | Q(display_name__trigram_similar=value)
                | Q(email__iexact=value)
                | Q(slack_id__iexact=value)
            )
            .annotate(similarity=TrigramSimilarity("display_name", value))
            # Closest first; the rest is the model's ordering, tie-break and
            # all, because a search result is paginated like any other list.
            # An identifier match sorts to a similarity of near zero, which is
            # right: it is either the only row or the odd one out among names
            # that actually look like what was typed.
            .order_by("-similarity", "display_name", "pk")
        )


# What each identifier is called in a sentence a volunteer reads.
IDENTIFIER_NOUNS = {"email": "email address", "slack_id": "Slack ID"}


@extend_schema_view(
    post=extend_schema(
        summary="Add a volunteer to the pick-list",
        responses={
            201: VolunteerSerializer,
            409: VolunteerConflictSerializer,
        },
    ),
)
@WITHDRAWN_SCHEMA
class VolunteerListCreateView(WithdrawnRows, ListCreateAPIView):
    """The volunteer pick-list, and the way onto it.

    Volunteers are a pick-list with no password (decision 0008 point 5). The
    client searches this before offering to add anyone, which is what stops a
    second generation of the duplicate spellings that decision counts.

    The one place the two halves disagree is an identifier held by somebody the
    list does not offer, which is answered with a 409 naming them rather than a
    400 the volunteer cannot act on. See decision 0015.
    """

    serializer_class = VolunteerSerializer
    filterset_class = VolunteerFilter
    queryset = OFFERED_VOLUNTEERS
    every_row = VOLUNTEERS

    # The other; see VOLUNTEER_APPEND.
    permission_classes = VOLUNTEER_APPEND

    # Rate limited; see inventory/throttling.py. Searching is not counted --
    # the client does that as somebody types.
    throttle_classes = APPEND_THROTTLES

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Ordinary creation, except when the clash is with somebody unshowable.

        Wrapped rather than reimplemented: the uniqueness check that raises is
        DRF's, derived from the model's partial indexes, and everything after a
        successful validation -- the write, the 201, the Location header --
        should stay whatever the base class does with it.
        """
        try:
            created = super().create(request, *args, **kwargs)
        except serializers.ValidationError as rejected:
            conflict = self._unshowable_holder(rejected.detail, request.data)
            if conflict is None:
                # An ordinary uniqueness refusal: the identifier belongs to
                # somebody the list still offers, so the client can show them.
                #
                # THE TWO SHAPES ARE READ AS TWO. `detail` is a dict keyed by
                # field name when the refusal is about a field, and a list of
                # messages when it is not -- and DRF composes several of those
                # messages out of what was submitted. Stringifying both shapes
                # alike put a submitted value under `reason`, which is the one
                # thing `redaction.ALLOWED_LOG_KEYS` says that key never holds.
                # A non-field refusal is named for being one; which validator
                # raised it is in the response the caller gets.
                detail = rejected.detail
                reason = sorted(detail) if isinstance(detail, dict) else ["non_field"]
                log.info("volunteer refused", reason=reason)
                telemetry.VOLUNTEERS.add(1, {"outcome": "refused"})
                raise
            # Decision 0015: the clash is with a record the list does not
            # offer, so the volunteer cannot see who they collided with. The
            # code says which kind; the name is in the response and not here.
            log.warning("volunteer conflicts with a withdrawn record", reason=conflict["code"])
            telemetry.VOLUNTEERS.add(1, {"outcome": "conflict"})
            return Response(conflict, status=status.HTTP_409_CONFLICT)
        log.info("volunteer added", volunteer=created.data.get("id"))
        telemetry.VOLUNTEERS.add(1, {"outcome": "added"})
        return created

    @classmethod
    def _unshowable_holder(cls, errors: Any, submitted: Any) -> dict[str, Any] | None:
        """The 409 body, if this rejection is the dead end described above.

        None for every other rejection -- a blank name, a clash with a live
        volunteer -- so those stay the 400 they were. A live holder is
        deliberately not named: the searcher could have found them, and
        answering with somebody's address who did not ask to be told would make
        this endpoint a lookup for anybody who can guess one.

        The clash has to be the *only* complaint, and every clashing field has
        to be a dead end. A submission with a blank name as well is not at the
        dead end yet, and neither is one whose other identifier is held by
        somebody live -- answering either with a conflict would hide a field
        the volunteer still has to fix.
        """
        # A rejection is a field map or a bare list of messages, and only the
        # first can be this -- a list normalises to no complaints rather than
        # to a branch of its own.
        complaints = errors if isinstance(errors, dict) else {}
        # Every complaint has to be one of the identifiers, and has to be this
        # one thing: read off the error's ``code``, which DRF attaches to
        # everything it raises, rather than off the sentence, which is a
        # translation away from being something else.
        clashes = {
            field
            for field, reported in complaints.items()
            if field in IDENTIFIER_NOUNS and all(getattr(error, "code", None) == "unique" for error in reported)
        }
        # All of them, and at least one. The second half is what lets the
        # answer below assume there is a field left to name.
        if len(clashes) != len(complaints) or not clashes:
            return None
        unshowable: dict[str, Volunteer] = {}
        for field in IDENTIFIER_NOUNS:
            if field not in clashes:
                continue
            # Stripped because that is the value DRF compared: CharField trims,
            # and an unstripped lookup would miss the very row it rejected.
            holder = Volunteer.objects.filter(**{field: str(submitted.get(field, "")).strip()}).order_by().first()
            # A holder of None is the row disappearing between the validator's
            # query and this one. Rare enough to be a race, cheap enough to
            # answer as the plain rejection it was -- and a holder the list
            # still offers keeps the whole rejection plain, so the field it
            # names stays visible. Who is offered is Volunteer.is_selectable's to say,
            # not restated here: the pick-list and this have to agree, which is
            # the drift decision 0015 exists to prevent.
            if holder is None or holder.is_selectable:
                return None
            unshowable[field] = holder
        # At least one, because every complaint was an identifier and each was
        # either recorded here or answered with None above. IDENTIFIER_NOUNS
        # order, so a submission clashing on both is always answered about the
        # same field.
        field, holder = next(iter(unshowable.items()))
        return cls._conflict(field, holder)

    @staticmethod
    def _conflict(field: str, holder: Volunteer) -> dict[str, Any]:
        """Name whoever the volunteer should be looking at, and say what to do.

        Rendered through the serializer that declares this response rather than
        assembled as a literal, so the body and the schema cannot describe
        different things.
        """
        noun = IDENTIFIER_NOUNS[field]
        survivor = _survivor_of(holder)
        merged = survivor is not holder
        found = (
            f"on a duplicate record since merged into {survivor.display_name}"
            if merged
            else f"on {survivor.display_name}'s record"
        )
        next_step = (
            "Continue as them rather than adding yourself again."
            if survivor.is_selectable
            else "That record has been retired, and an administrator can restore it."
        )
        return dict(
            VolunteerConflictSerializer(
                {
                    "detail": f"That {noun} is already recorded, {found}. {next_step}",
                    "code": "volunteer_merged" if merged else "volunteer_inactive",
                    "field": field,
                    "volunteer": survivor,
                    "selectable": survivor.is_selectable,
                }
            ).data
        )


def _survivor_of(volunteer: Volunteer) -> Volunteer:
    """Follow ``merged_into`` forward to whoever is left.

    Forward, because that is the direction docs/data-model.md says a reader
    takes, and decision 0015 point 1 says why. Chains happen -- a duplicate
    merged into a record later merged itself -- and only the end of one is
    worth offering.

    The visited set is not decoration. The model forbids merging a record into
    itself and nothing forbids a longer cycle, and a cycle here would hang the
    request rather than return a 409.
    """
    seen = {volunteer.pk}
    survivor = volunteer
    while survivor.merged_into is not None and survivor.merged_into.pk not in seen:
        survivor = survivor.merged_into
        seen.add(survivor.pk)
    return survivor


class VolunteerDetailView(DetailView):
    """One volunteer, read by anyone and repaired by an administrator.

    Merging duplicates and retiring a record are the operations decision 0012
    reserves for somebody signed in, and they happen here rather than at a URL
    of their own: a merge is an edit to the duplicate, saying who it turned out
    to be. There is no delete, because the ledger attributes work to this row
    for as long as the ledger exists.
    """

    serializer_class = VolunteerDetailSerializer
    every_row = VOLUNTEERS
    offered_rows = OFFERED_VOLUNTEERS


class ItemFilter(FilterSet):
    """What the item list can be narrowed by."""

    search = CharFilter(field_name="name", lookup_expr="icontains", label="Name contains")

    class Meta:
        model = Item
        fields = ["category"]


# Every item, with the two things the screen needs attached in bulk. Shared by
# the list and the detail so that one item read on its own is the same shape as
# the same item read in a page of a hundred -- the labels in particular, which
# are neither every sticker nor the revoked ones.
#
# Prefetched, not walked per row: a hundred items on a phone would otherwise be
# two hundred queries behind one screen. No select_related on the category: it
# is rendered as its id, which the item row already carries, so joining it
# would fetch a row nothing reads.
#
# Ordered by the id columns, not the relations. Ordering by `location` would
# follow the foreign key to Location's own Meta.ordering, joining that table to
# sort balances by a name the response does not even carry.
ITEMS = Item.objects.prefetch_related(
    Prefetch("balances", queryset=StockBalance.objects.order_by("location_id")),
    # One row per distinct quantity, not per printed sticker -- see
    # ItemLabelSerializer. DISTINCT ON is scoped to item_id as well, because
    # this one query carries every listed item's labels and deduping on
    # quantity alone would strip most of the page.
    Prefetch(
        "labels",
        queryset=Label.objects.live().order_by("item_id", "quantity", "id").distinct("item_id", "quantity"),
    ),
)

# Retired items are not offered, the same way retired locations and merged
# volunteers are not: this is a pick-list, and decision 0019's context says
# what belongs on one. `active` is not a parameter here because `?withdrawn=true`
# already lists what this collection took out, which is decision 0019's read
# half -- retirement says a row is not offered, not that its stock is gone.
OFFERED_ITEMS = ITEMS.filter(active=True)


@WITHDRAWN_SCHEMA
class ItemListView(RecordsWhatItCreated, WithdrawnRows, ReadsAndWritesDiffer, ListCreateAPIView):
    """The catalogue, with the stock behind it. Administrators add to it.

    This is the screen the volunteer mockup is almost entirely made of: every
    item, its count, and a way to add some to the cart. See decision 0011.

    Creating one is an administrator's operation (decision 0014 point 2), so
    the two audiences meet on one endpoint and ``StaffWrites`` is what
    separates them.
    """

    filterset_class = ItemFilter
    queryset = OFFERED_ITEMS
    every_row = ITEMS

    serializer_class = ItemSerializer
    # A create carries every field an item has; a list carries the ones a
    # phone needs a hundred times over. See ItemDetailSerializer.
    write_serializer_class = ItemDetailSerializer


class ItemDetailView(DetailView):
    """One item, read by anyone and edited by an administrator.

    Retiring an item is a PATCH setting ``active`` to false, not a delete: the
    ledger refers to it for as long as the ledger exists. What that means for
    stock still physically on a shelf is
    [decision 0019](../../../docs/decisions/0019-retired-means-not-offered.md) --
    it stops being offered and stays countable.
    """

    serializer_class = ItemDetailSerializer
    every_row = ITEMS
    offered_rows = OFFERED_ITEMS


# Ordering comes from the model, tie-break included.
LOCATIONS = Location.objects.all()
# Retired locations are not offered, for the reason given on OFFERED_ITEMS.
OFFERED_LOCATIONS = LOCATIONS.filter(active=True)


@WITHDRAWN_SCHEMA
class LocationListView(RecordsWhatItCreated, WithdrawnRows, ListCreateAPIView):
    """Everywhere stock can be. A pick-list, like volunteers.

    Retired locations are not offered, and `active` is deliberately not a
    filter: the queryset already fixes it, so the parameter could only ever
    return nothing. Same shape as the volunteer pick-list, which narrows on a
    queryset rather than advertising `active` either.
    """

    serializer_class = LocationSerializer
    filterset_fields = ["kind", "parent"]
    every_row = LOCATIONS
    queryset = OFFERED_LOCATIONS


class LocationDetailView(DetailView):
    """One location, read by anyone and edited by an administrator.

    Retiring one is a PATCH setting ``active`` to false, for the reason given
    on ItemDetailView: the ledger refers to it forever.
    """

    serializer_class = LocationSerializer
    every_row = LOCATIONS
    offered_rows = OFFERED_LOCATIONS


# Nothing withdraws a category from the list, so there is one queryset rather
# than a pair; see CategoryDetailView.
CATEGORIES = Category.objects.all()


class CategoryListView(RecordsWhatItCreated, ListCreateAPIView):
    """The item groupings, for narrowing the catalogue."""

    serializer_class = CategorySerializer
    filterset_fields = ["parent"]
    queryset = CATEGORIES


class CategoryDetailView(DetailView):
    """One grouping, read by anyone and renamed or re-parented by an administrator.

    A category carries no ``active`` column, so nothing here is withdrawn from
    the list and both querysets are the same one. Decision 0019 gives retirement
    a meaning for rows that hold stock, and a category holds none, so the column
    would buy nothing; an unwanted grouping with no items in it is already
    reachable through the Django admin, which decision 0014 point 4 keeps for
    exactly this.
    """

    serializer_class = CategorySerializer
    every_row = CATEGORIES
    offered_rows = CATEGORIES


# Every label, and the ones that still point at something. The map the client
# caches is the second; resolving a scanned code reads the first, because a
# revoked sticker still says what it pointed at.
LABELS = Label.objects.all()
# select_related, because the map now carries the item's name and unit: a few
# hundred rows would otherwise be a few hundred queries behind one response.
LIVE_LABELS = Label.objects.live().select_related("item").order_by("code")


class LabelListView(RecordsWhatItCreated, ReadsAndWritesDiffer, ListCreateAPIView):
    """Every label that still points at something.

    Unpaginated, deliberately. This exists to be fetched once and cached, so
    that a scan resolves without a round trip from a basement (decision 0011);
    handing it back in pages would make the client stitch them together for no
    benefit at a few hundred rows.
    """

    pagination_class = None
    queryset = LIVE_LABELS

    serializer_class = LabelMapSerializer
    # The cached map drops what it can (see LabelMapSerializer); printing a
    # label needs the whole row back.
    write_serializer_class = LabelSerializer

    def perform_create(self, serializer: Any) -> None:
        super().perform_create(serializer)
        # BESIDE the catalogue edit the mixin records, not instead of it. The
        # two counters answer two questions: how much an administrator changed
        # across every collection, and what became of stickers. `minted` and
        # `revoked` were declared on `inventory.labels` and never recorded by
        # anything, so the only outcome it ever carried was `printed` and the
        # other two were a promise in a comment.
        telemetry.LABELS.add(1, {"outcome": "minted"})


# Far above a real print run, which is a page or two of stickers somebody is
# about to stand up and apply. It exists so one request cannot hold a worker
# encoding symbols for as long as a query string can be made long. Same shape
# of limit, and the same reasoning, as MAX_MOVEMENTS in serializers.py.
MAX_SHEET_LABELS = 200


# The one media type in this API that is not JSON, named once because the
# response, the refusal and the schema all have to agree on it. The encoding
# travels with it on the wire -- the sheet says so in a meta tag as well, but a
# refusal is built here and has only the header to say it in.
SHEET_MEDIA_TYPE = "text/html"
SHEET_CONTENT_TYPE = f"{SHEET_MEDIA_TYPE}; charset=utf-8"


@extend_schema(
    summary="Render labels as a printable sheet",
    parameters=[
        OpenApiParameter(
            name="code",
            description="Codes to print, comma separated. Required: a sheet is a batch, not the estate.",
            required=True,
            type=OpenApiTypes.STR,
        ),
    ],
    # Both bodies are the document, refusals included: this endpoint renders
    # its own 403 rather than answering a browser with JSON. See
    # handle_exception below.
    responses={
        (200, SHEET_MEDIA_TYPE): OpenApiTypes.STR,
        (400, SHEET_MEDIA_TYPE): OpenApiTypes.STR,
        (403, SHEET_MEDIA_TYPE): OpenApiTypes.STR,
    },
)
class LabelSheetView(APIView):
    """A page of stickers, ready to print.

    Unreadable labels are a printing failure, not a decoding one, so what a
    label carries -- error correction level Q, a quiet zone, a module size with
    a floor under it, the code in text under the symbol and the date it was
    printed -- is fixed in ``inventory.labels`` and asserted by tests there.
    This view only decides which labels are on the page.

    Live labels only. A revoked sticker is superseded, and reprinting one would
    put back the very thing revoking it was meant to withdraw; the whole point
    of an opaque code is that the replacement is a new label rather than a
    correction to this one.

    The codes have to be named. A sheet is a batch somebody is about to stick
    on things -- the ones just minted, or the faded ones being replaced -- and
    "every label there has ever been" is not a print run: it is already stuck
    to the shelves, and asking for it lays out one symbol per live label for
    nobody.

    A code that names nothing is simply not printed, rather than failing the
    sheet: refusing the whole batch and naming the bad code would lose a print
    run of forty stickers over one typo in a query string nobody typed by hand.
    Codes are normalised on the way in, because every other way of naming one
    here is -- ``LabelResolveView`` folds and uppercases a scanned or typed
    code -- and a sheet that answered a lowercase code with a blank page would
    be the one place in this API where the canonical form is the caller's
    problem.

    Bounded, and not only by what was asked for. Every code costs a QR encode
    and a symbol on the page -- about two milliseconds each -- so a query
    string full of them is a synchronous worker held for as long as it takes.
    MAX_SHEET_LABELS is far above a real sheet, for the same reason
    MAX_MOVEMENTS is far above a real cart.

    HTML, and the only endpoint here that is. It is a page to be printed rather
    than data to be parsed, and the alternative -- a JSON body of SVG strings
    for a client to lay out -- would put the sizes that decide whether a label
    scans on the far side of the API from the tests that assert them.
    """

    # An APIView, not a ListAPIView. What comes back is one document and not a
    # page of rows, so there is no serializer to name, no paginator to switch
    # off and no filter backend whose parameters the schema then cannot read --
    # while everything DRF is being kept for is still inherited: permissions,
    # throttles, the exception handler, and get_permissions, which
    # RequireSecondFactor asks of this class. Was inventory-tng-s0m.
    #
    # No docstring on the handler: drf-spectacular describes an operation with
    # the handler's docstring where there is one and the view's only otherwise,
    # and it is the class docstring above that is written for a schema reader.
    def get(self, request: Request) -> HttpResponse:
        asked = [code for code in request.query_params.get("code", "").split(",") if code.strip()]
        if not asked:
            raise ValidationError(
                {"detail": "Name the codes to print, comma separated: /api/labels/sheet?code=7QK3M2XV9A,4NP8R7T2WQ"}
            )
        if len(asked) > MAX_SHEET_LABELS:
            raise ValidationError(
                {"detail": f"A sheet carries at most {MAX_SHEET_LABELS} labels; this asked for {len(asked)}."}
            )
        codes = [Label.normalise_code(code) for code in asked]
        # Evaluated once. `count()` was a round trip of its own and `sheet()`
        # then evaluated the queryset again, so a print cost two queries and
        # the number recorded could disagree with the rows drawn if a label
        # was revoked between them.
        printing = list(LIVE_LABELS.filter(code__in=codes))
        # Asked-for and found, because they differ when a code has been
        # revoked since the list was fetched, and a sheet that came back short
        # is a complaint somebody makes about the printer.
        found = len(printing)
        log.info("sheet printed", lines=found, reason=f"{len(codes)} asked for")
        telemetry.LABELS.add(found, {"outcome": "printed"})
        # An HttpResponse rather than a Response: DRF's finalize_response passes
        # anything that is not a Response through untouched, so the document
        # reaches the browser as written and no renderer is involved.
        return HttpResponse(sheet(printing), content_type=SHEET_CONTENT_TYPE)

    # DRF's stubs narrow this to a Response, but `dispatch` only ever hands the
    # result to `finalize_response`, which takes any HttpResponseBase and passes
    # a non-Response straight through -- which is the whole point here.
    def handle_exception(self, exc: Exception) -> HttpResponse:  # ty: ignore[invalid-method-override]
        """The refusal as a page, because a browser is this endpoint's only client.

        DRF builds the body, the status and the headers; this keeps all three
        and swaps the JSON for the document. The page itself is in
        inventory.labels, beside the sheet it is standing in for.
        """
        refused = super().handle_exception(exc)
        detail = refused.data.get("detail", "") if isinstance(refused.data, dict) else refused.data
        page = HttpResponse(refusal_page(str(detail)), status=refused.status_code, content_type=SHEET_CONTENT_TYPE)
        for header, value in refused.items():
            page[header] = value
        return page


@extend_schema_view(
    # Summaries only: get_serializer_class already tells the schema which
    # shape each method answers with.
    get=extend_schema(summary="Resolve a scanned label code"),
    patch=extend_schema(summary="Revoke, restore or correct a label"),
)
class LabelResolveView(ReadsAndWritesDiffer, DetailView):
    """One label, found by the code printed on it.

    A revoked label resolves rather than 404s: the sticker is superseded, but
    it still says what it pointed at, and refusing the scan would block a
    volunteer over bookkeeping. The client is told, through ``revoked_at``,
    and can say so.

    Revoking is an administrator's operation (decision 0012) and happens here
    rather than at a URL of its own, because the code is the label's identity
    and a faded sticker is the complaint this project started with. It is a
    PATCH of ``revoked``, never a delete: the ledger's history refers to what
    the sticker pointed at.
    """

    # A revoked label is still offered here, so there is nothing withdrawn and
    # both querysets are the same one. Its code cannot be changed once printed
    # (LabelSerializer.validate), which is the other half of why there is no
    # PUT: a replacement could only ever be this label with other fields.
    every_row = LABELS
    offered_rows = LABELS
    lookup_field = "code"
    serializer_class = LabelResolveSerializer
    write_serializer_class = LabelSerializer

    def get_object(self) -> Label:
        # Normalised before the lookup, so a code copied by hand off a dying
        # label resolves; see Label.normalise_code. Everything else -- the
        # 404, object permissions, filter backends -- is the base class's.
        self.kwargs["code"] = Label.normalise_code(self.kwargs["code"])
        return super().get_object()

    def perform_update(self, serializer: Any) -> None:
        # The other half of `inventory.labels`. Read before and after rather
        # than off the request body, because what is worth counting is a
        # sticker that STOPPED being live -- a PATCH re-sending `revoked` on
        # one already revoked has changed nothing and is not a second
        # revocation.
        #
        # Both readings come off the instance the serializer is already
        # holding, which is what makes the before free: asking `get_object`
        # for it was a second SELECT of the row, and a second object-permission
        # check with it, on every edit.
        was_live = serializer.instance.revoked_at is None
        super().perform_update(serializer)
        if was_live and serializer.instance.revoked_at is not None:
            telemetry.LABELS.add(1, {"outcome": "revoked"})


class CapabilityProbe:
    """One request, asked about a method it did not use.

    A permission class reads two things: who is asking, and what they are
    asking to do. Reporting a capability means asking the second question of an
    operation the caller has not made, so the caller is passed through
    untouched and only the method is substituted.

    It answers "may this caller make this kind of request", not "would this
    exact body be accepted": the body and the query string are still the GET's,
    because inventing either would be a larger lie than leaving them.
    """

    def __init__(self, request: Request, method: str) -> None:
        self._probed = request
        self.method = method

    def __getattr__(self, name: str) -> Any:
        return getattr(self._probed, name)


class Operation(NamedTuple):
    """One endpoint and one method, whose own permissions answer for it."""

    view: type[APIView]
    method: str


# What the interface asks about, and the operations that answer it.
#
# Not a second declaration of who may do what: each entry names real
# operations, and the answer is those operations' own permission classes run
# against this caller. Change what guards an endpoint and this changes with it,
# which is the point: decision 0014 point 3 says what a client drawing a
# control it may not use costs.
#
# A capability may name several operations, because the interface's vocabulary
# is coarser than the URL layout: "may I edit the catalogue" is one control and
# six endpoints. It is granted only if every operation behind it is, so a
# control this offers is one that will work throughout.
#
# The names are the interface's vocabulary, so they are stable in a way URLs
# are not, and every one of them appears in every answer -- a capability
# missing from the response would be indistinguishable from one this server has
# never heard of. That no administrator's endpoint is missing from this map is
# a test, not a promise: see test_capabilities.py.
CAPABILITIES: dict[str, tuple[Operation, ...]] = {
    "append_stock": (Operation(StockTransactionCreateView, "POST"),),
    "add_volunteer": (Operation(VolunteerListCreateView, "POST"),),
    "edit_catalogue": (
        Operation(ItemListView, "POST"),
        Operation(ItemDetailView, "PATCH"),
        Operation(LocationListView, "POST"),
        Operation(LocationDetailView, "PATCH"),
        Operation(CategoryListView, "POST"),
        Operation(CategoryDetailView, "PATCH"),
    ),
    "print_label": (Operation(LabelListView, "POST"),),
    "revoke_label": (Operation(LabelResolveView, "PATCH"),),
    "merge_volunteers": (Operation(VolunteerDetailView, "PATCH"),),
}


class ClientFailureView(APIView):
    """Where a volunteer's browser says something went wrong on their phone.

    THE THIRD ENDPOINT THAT TAKES NO CREDENTIAL, and decision 0012's own
    consequence says one of those has to be argued against that record rather
    than added beside it. The argument is in
    docs/decisions/0012-two-populations.md under "A third endpoint, and what
    made it arguable"; the short of it is that this writes no row, corrects
    nothing and is bounded in what it may say.

    It records rather than stores. The failure becomes a log record at ERROR,
    in the same stream every other record goes to, so whatever collects that
    stream has it -- there is no table, nothing to correct, and nothing for a
    later reader to have to clean up.

    Rate limited, which is what stands in for the credential none of the three
    ask for -- but on a budget of its own rather than the append endpoints',
    because DRF keys a bucket on the scope and the client rather than on the
    view. `inventory.throttling.ReportThrottle` argues why sharing one meant a
    backend having a bad minute could spend a volunteer's whole allowance on
    reports about it.
    """

    permission_classes = [AllowAny]
    throttle_classes = REPORT_THROTTLES

    @extend_schema(
        summary="Report a failure a browser could not handle",
        request=ClientFailureSerializer,
        responses={204: None},
    )
    def post(self, request: Request) -> Response:
        # DRF's own refusal, which is the field map every other write in this
        # API answers with. A bespoke body here would be a second shape for a
        # client to handle and the schema test would say so.
        reported = ClientFailureSerializer(data=request.data)
        reported.is_valid(raise_exception=True)
        failure = reported.validated_data
        # At ERROR because it is one: something threw on a volunteer's phone
        # and nobody caught it. `where` is this application's own word for
        # what was happening, so a graph can group by it.
        log.error("browser failure", kind=failure["kind"], reason=failure["where"], detail=failure["detail"])
        telemetry.CLIENT_FAILURES.add(1, {"kind": failure["kind"], "reason": failure["where"]})
        return Response(status=status.HTTP_204_NO_CONTENT)


class DebugTraceVerifyView(APIView):
    """Whether nginx should forward this post to the collector.

    A subrequest rather than a rule nginx could apply on its own: the token is
    a Django signature over a random id with an expiry, and nothing in an nginx
    configuration can check one. `inventory_tng.debugging` is what it is and
    why the ingest path needs it at all.

    Open to anybody, deliberately, because the token in the header is the
    credential -- volunteers do not sign in, so requiring a session here would
    lock out exactly the person an administrator sent the link to.

    It does NOT count against the token's rate limit. That limit is on how much
    tracing one token may make this server do, and answering a subrequest is
    not that; charging it here would make the browser's own exporter spend the
    allowance meant for the requests it is tracing.
    """

    permission_classes = [AllowAny]

    @extend_schema(
        summary="Verify a debug-tracing token",
        description=(
            "Answers 204 when the request carries a token this deployment signed and has not expired, and 403 "
            "otherwise. It exists for nginx's `auth_request`, which cannot check a signature itself, and is what "
            "keeps the collector's ingest path from being a write anybody can make. It reads nothing but the "
            "header and answers with no body."
        ),
        responses={204: None, 403: None},
    )
    def get(self, request: Request) -> Response:
        if debugging.minted(request.headers.get(debugging.HEADER, "")):
            return Response(status=status.HTTP_204_NO_CONTENT)
        log.info("debug ingest refused")
        telemetry.REFUSALS.add(1, {"reason": "unsigned_ingest"})
        return Response(status=status.HTTP_403_FORBIDDEN)


class DeviceEnrolmentView(APIView):
    """Mint the opaque name a device is told apart by, and cut off by.

    THE FOURTH ENDPOINT THAT TAKES NO CREDENTIAL, and decision 0012's own
    consequence asks that one of those be argued rather than added quietly.
    The argument is that it has to take none: a device has nothing to present
    until it has enrolled, and a credential that could only be obtained by
    somebody already carrying one would admit nobody. It writes one row holding
    no person, and what that row is for -- attribution, not admission -- is
    `inventory_tng.devices`, which also says plainly what it does not buy.

    SO THE GUARD IS THE THROTTLE, NOT THE SIGNATURE, and that is the sentence
    to keep hold of when reading this. Fifty calls buy fifty buckets and every
    signature checks out; `DEVICE_ENROLMENT_THROTTLES` is the thing standing in
    the way, out of a bucket of its own, and `.env.sample` sizes it.

    AND EVERY MINT RECORDS WHERE IT WAS ASKED FROM. Not preventable on a flat
    network, and not meant to be: it is what turns "fifty devices appeared this
    afternoon" from something nobody notices into one query and one bulk
    revoke. `inventory_tng.forwarded.client_address` is the reading, so this
    agrees with decision 0023 about whose address it is rather than trusting a
    header nobody vouched for.
    """

    permission_classes = [AllowAny]
    throttle_classes = DEVICE_ENROLMENT_THROTTLES

    @extend_schema(
        summary="Enrol this device and receive the token it carries",
        request=None,
        responses={201: DeviceEnrolmentSerializer},
    )
    def post(self, request: Request) -> Response:
        identifier = devices.new_identifier()
        # An address the deployment vouches for, or nothing. `client_address`
        # answers with the peer when no proxy is trusted, which in a checkout
        # is the loopback address and in a cluster is the ingress -- neither of
        # which says anything about a caller. Stored anyway rather than
        # second-guessed here: what makes it worth having is that two mints
        # sharing one is visible, and that holds whichever of those it is.
        #
        # `address_or_none` and not the bare answer, because that answer is
        # allowed to be a string a caller invented -- and this column is typed
        # `inet`, so one that is not an address at all would be a 500 on a
        # credential-free endpoint rather than a row.
        Device.objects.create(
            identifier=identifier,
            enrolled_from=address_or_none(client_address(request.META, settings.TRUSTED_PROXIES)),
        )
        # The identifier and never the token. A token in a log is a credential
        # in a log, and `redaction` is what keeps this one to a surrogate.
        log.info("device enrolled", device=identifier)
        telemetry.DEVICES_ENROLLED.add(1)
        return Response(
            {"token": devices.mint(identifier), "device": identifier},
            status=status.HTTP_201_CREATED,
        )


class CurrentUserView(APIView):
    """Who the caller is, and what this server will let them do.

    Decision 0014 point 3: the interface renders administrative controls from
    this answer rather than guessing at them.

    Deliberately public, and deliberately not a 403 for a caller with no
    session. The volunteer app fetches this on load, and decision 0012 says a
    volunteer never signs in -- so "you are nobody, and here is what nobody may
    do" is the ordinary case, not an error to be handled on the way to the
    real one.
    """

    permission_classes = [AllowAny]

    @extend_schema(
        summary="What the caller is and what they may do",
        responses=inline_serializer(
            name="CurrentUser",
            fields={
                "authenticated": serializers.BooleanField(),
                # The account's name, not a person's: volunteers are a
                # pick-list and are never accounts (decision 0012 point 5), so
                # there is nobody to name until somebody has signed in.
                "username": serializers.CharField(allow_null=True),
                "administrator": serializers.BooleanField(),
                # Whether this session has proved who it is recently enough to
                # change something. A capability below is what the caller may
                # do *now*, so a false one can mean either "not you" or "not
                # until you sign in again"; this is what tells them apart, and
                # it is why the interface can offer to re-authenticate rather
                # than hide a control the person is entitled to.
                "recently_authenticated": serializers.BooleanField(),
                # What this server makes of the device token the request
                # carried, in one word. A 403 cannot say "this device was
                # removed" as against "not you", and the two want opposite
                # screens -- a button that enrols again, or a wall. This
                # answers before anything has been refused, which is why the
                # endpoint saying it is one a revoked device is still served.
                #
                # NOTHING READS IT YET, and that is stated rather than left to
                # be discovered: the app carries the header and does not act on
                # either `revoked` or `unknown`. `inventory-tng-wpf2` is that
                # work. The field ships first because the alternative is a
                # client change that cannot be written until the server one has
                # landed.
                "device": serializers.ChoiceField(choices=DEVICE_STATES),
                "capabilities": inline_serializer(
                    name="Capabilities",
                    # One field instance per entry, for the reason spelled out
                    # in ApiRootView.
                    fields={name: serializers.BooleanField() for name in CAPABILITIES},
                ),
            },
        ),
    )
    def get(self, request: Request) -> Response:
        user = request.user
        return Response(
            {
                "authenticated": user.is_authenticated,
                "username": user.get_username() if user.is_authenticated else None,
                "administrator": is_administrator(user),
                "recently_authenticated": recently_authenticated(request),
                "device": presented_device(request)[0],
                "capabilities": {
                    name: all(self._permitted(request, operation) for operation in operations)
                    for name, operations in CAPABILITIES.items()
                },
            }
        )

    @staticmethod
    def _permitted(request: Request, operation: Operation) -> bool:
        """Run one endpoint's permission classes without running the endpoint.

        ``get_permissions`` is asked of an instance because a view may build
        its list rather than declare it, and the view is given the probe as its
        request because a permission class is handed both and may read either.
        Every class is consulted: DRF requires all of them to agree.

        The view is built with no URL captured, so ``kwargs`` is empty. Nothing
        here reads it, but a permission class that did -- or an object-level
        one, which ``has_permission`` alone cannot ask -- would be answered
        from an emptier context than a real request carries.
        """
        view = operation.view()
        probe = cast(Request, CapabilityProbe(request, operation.method))
        view.request = probe
        view.args = ()
        view.kwargs = {}
        return all(permission.has_permission(probe, view) for permission in view.get_permissions())
