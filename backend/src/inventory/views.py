from typing import Any, NamedTuple, cast

from django.contrib.postgres.search import TrigramSimilarity
from django.db import IntegrityError, connection
from django.db.models import Prefetch, Q, QuerySet
from django.db.transaction import atomic
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django_filters.rest_framework import CharFilter, FilterSet
from drf_spectacular.utils import extend_schema, extend_schema_view, inline_serializer
from rest_framework import serializers, status
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateAPIView
from rest_framework.permissions import SAFE_METHODS, AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView

from inventory.models import (
    Category,
    Item,
    Label,
    Location,
    StockBalance,
    StockMovement,
    StockTransaction,
    Volunteer,
)
from inventory.permissions import VOLUNTEER_APPEND, is_administrator
from inventory.serializers import (
    BatchInconsistentSerializer,
    BatchRejectedSerializer,
    CategorySerializer,
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
from inventory.throttling import APPEND_THROTTLES

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
# section 6. No database counterpart: inventory-tng-fi5.
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

    Reported, never refused. Volunteers had no way to say "the shelf disagrees
    with the system", so they faked corrections instead; see
    docs/decisions/0008-stock-ledger-transfer-graph.md. The shelf is the
    authority, and the answer is a stock count rather than a blocked volunteer.

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
            return Response(
                {
                    "detail": "Nothing was saved. Every line that needs fixing is listed.",
                    "errors": _rejected(serializer.errors),
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
            return Response(self._body(replayed), status=status.HTTP_200_OK)

        lines = serializer.validated_data["movements"]
        kind = serializer.validated_data["kind"]
        inconsistent = _inconsistent_lines(kind, lines)
        if inconsistent:
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
            return Response(self._body(raced), status=status.HTTP_200_OK)

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
class VolunteerListCreateView(ListCreateAPIView):
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
            return super().create(request, *args, **kwargs)
        except serializers.ValidationError as rejected:
            conflict = self._unshowable_holder(rejected.detail, request.data)
            if conflict is None:
                raise
            return Response(conflict, status=status.HTTP_409_CONFLICT)

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
    takes: a merge points the duplicate at the survivor and changes nothing
    else. Chains happen -- a duplicate merged into a record later merged
    itself -- and only the end of one is worth offering.

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
# volunteers are not: this is a pick-list, and a retired item is not something
# to add to a cart. What `active=False` should mean for stock that is still
# physically on a shelf is inventory-tng-6c7, and until that is settled an
# `active` parameter would be guessing at the answer.
OFFERED_ITEMS = ITEMS.filter(active=True)


class ItemListView(ReadsAndWritesDiffer, ListCreateAPIView):
    """The catalogue, with the stock behind it. Administrators add to it.

    This is the screen the volunteer mockup is almost entirely made of: every
    item, its count, and a way to add some to the cart. See decision 0011.

    Creating one is an administrator's operation (decision 0014 point 2), so
    the two audiences meet on one endpoint and ``StaffWrites`` is what
    separates them.
    """

    filterset_class = ItemFilter
    queryset = OFFERED_ITEMS

    serializer_class = ItemSerializer
    # A create carries every field an item has; a list carries the ones a
    # phone needs a hundred times over. See ItemDetailSerializer.
    write_serializer_class = ItemDetailSerializer


class ItemDetailView(DetailView):
    """One item, read by anyone and edited by an administrator.

    Retiring an item is a PATCH setting ``active`` to false, not a delete: the
    ledger refers to it for as long as the ledger exists, and what
    ``active=False`` should mean for stock still physically on a shelf is
    inventory-tng-6c7.
    """

    serializer_class = ItemDetailSerializer
    every_row = ITEMS
    offered_rows = OFFERED_ITEMS


# Ordering comes from the model, tie-break included.
LOCATIONS = Location.objects.all()
# Retired locations are not offered, for the reason given on OFFERED_ITEMS.
OFFERED_LOCATIONS = LOCATIONS.filter(active=True)


class LocationListView(ListCreateAPIView):
    """Everywhere stock can be. A pick-list, like volunteers.

    Retired locations are not offered, and `active` is deliberately not a
    filter: the queryset already fixes it, so the parameter could only ever
    return nothing. Same shape as the volunteer pick-list, which narrows on a
    queryset rather than advertising `active` either.
    """

    serializer_class = LocationSerializer
    filterset_fields = ["kind", "parent"]
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


class CategoryListView(ListCreateAPIView):
    """The item groupings, for narrowing the catalogue."""

    serializer_class = CategorySerializer
    filterset_fields = ["parent"]
    queryset = CATEGORIES


class CategoryDetailView(DetailView):
    """One grouping, read by anyone and renamed or re-parented by an administrator.

    A category carries no ``active`` column, so nothing here is withdrawn from
    the list and both querysets are the same one. Adding such a column would
    be guessing at inventory-tng-6c7's answer, and an unwanted grouping with
    no items in it is already reachable through the Django admin, which
    decision 0014 point 4 keeps for exactly this.
    """

    serializer_class = CategorySerializer
    every_row = CATEGORIES
    offered_rows = CATEGORIES


# Every label, and the ones that still point at something. The map the client
# caches is the second; resolving a scanned code reads the first, because a
# revoked sticker still says what it pointed at.
LABELS = Label.objects.all()
LIVE_LABELS = Label.objects.live().order_by("code")


class LabelListView(ReadsAndWritesDiffer, ListCreateAPIView):
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
# which is the point -- a client that draws an editing control it is not
# allowed to use is a bug report waiting to happen (decision 0014 point 3).
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
