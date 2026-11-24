"""What the scan and register pages report about themselves: POST
/api/telemetry (docs/decisions/0006; frontend/src/shared/telemetry.ts).

The schema is the list of what may be sent: closed sets of names and
clamped numbers, nothing free. Each event becomes a metric; an error says
only that one happened, and on which page. Open to any browser, since the
register page reports before its device is registered, so every report
counts against its address first: in this process's memory, not the
database's shared limits (core/rate_limits.py), which guard signing in
and registering. A page's numbers are not worth a connection, and a flood
of them must not hold one up from those.
"""

from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter
from opentelemetry import metrics
from pydantic import BaseModel, ConfigDict, Field

from inventory_tng.core.rate_limits import RateLimitedReply
from inventory_tng.web import forms
from inventory_tng.web.browser import over_limit

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

router = APIRouter()

PATH = "/telemetry"
MOST_EVENTS = 50

Page = Literal["scan", "register"]
# Seconds a page measured: a day at most.
UpToADay = Annotated[float, Field(ge=0, le=86_400)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WebVital(Strict):
    kind: Literal["web_vital"]
    page: Page
    name: Literal["LCP", "INP", "CLS"]
    # LCP and INP in milliseconds; CLS a score.
    value: Annotated[float, Field(ge=0, le=600_000)]
    rating: Literal["good", "needs-improvement", "poor"]


class PageError(Strict):
    kind: Literal["error"]
    page: Page
    source: Literal["error", "unhandledrejection"]


class OutboxRetry(Strict):
    kind: Literal["outbox_retry"]
    reason: Literal["no_answer", "unavailable", "unexpected"]


class ScanToSave(Strict):
    kind: Literal["scan_to_save"]
    # From a batch's first line to saving it.
    seconds: UpToADay


class OutOfDate(Strict):
    """A scan page's tab learned that a newer build is out (page_builds.py),
    once a tab."""

    kind: Literal["out_of_date"]
    # From another tab's, the server's reply, or a batch held back as from
    # a build too old.
    learned: Literal["other_tab", "server", "too_old"]


class OlderStorage(Strict):
    """What an older build left under its own key, saves not sent or a cart
    with lines: a scan page found it as it loaded, a volunteer put one back
    in the cart, discarded it or discarded one line of it, or the page put
    one back in an empty cart itself (frontend/src/scan/older-builds.ts)."""

    kind: Literal["older_storage"]
    held: Literal["saves", "cart"]
    # Found, unless it says: a page sending none means that.
    action: Literal[
        "found",
        "put_in_cart",
        "put_in_cart_automatically",
        "discarded",
        "line_discarded",
    ] = "found"


class CartDiscarded(Strict):
    """A volunteer discarded the scan page's cart, once they confirmed it."""

    kind: Literal["cart_discarded"]


class LineRemoved(Strict):
    """A volunteer removed a line from the scan page's cart."""

    kind: Literal["line_removed"]


class RemovalUndone(Strict):
    """A volunteer pressed Undo after removing a line from the scan page's
    cart, putting it back where it was."""

    kind: Literal["removal_undone"]


class DifferenceAdded(Strict):
    """What a save refused as another tab recorded its cart first holds
    more of, or new, put in the scan page's cart: by the page itself into
    an empty cart, or by a volunteer's tap
    (frontend/src/scan/recorded-elsewhere.ts)."""

    kind: Literal["difference_added"]
    how: Literal["automatic", "tap"]


class RestPutBack(Strict):
    """A volunteer tapped Add to cart under a batch the inventory
    refused for some of its lines, putting its other lines in the scan
    page's cart (frontend/src/scan/add-the-rest.ts)."""

    kind: Literal["rest_put_back"]


class OtherSeparator(Strict):
    """A scan page's box refused as written with the decimal separator its
    locale does not use, as Record was tapped: a point, a comma, or either
    followed by three digits after a whole part above 0, as thousands are
    grouped (web/forms.py)."""

    kind: Literal["other_separator"]
    separator: forms.OtherSeparator


LabelCount = Annotated[int, Field(ge=0, le=10_000)]


class LabelsRead(Strict):
    """Labels a camera session read, by what each names: an item, an
    item's package, a place, or nothing the catalogue has (old stickers)."""

    item: LabelCount
    package: LabelCount
    place: LabelCount
    unknown: LabelCount


class CameraSession(Strict):
    """One session of the scan page's camera, sent as it closed, or as the
    page was hidden with it open (frontend/src/scan/camera-report.ts)."""

    kind: Literal["camera_session"]
    # Opened, or what refused it; resumed when the page was shown again
    # with the camera open, after reporting as it was hidden; cancelled when
    # closed before it opened or was refused.
    outcome: Literal[
        "opened",
        "resumed",
        "cancelled",
        "not_allowed",
        "not_found",
        "in_use",
        "insecure",
        "decoder_failed",
        "other",
    ]
    # The decoder that read, which tells browsers apart with no user agent
    # read; none where the camera never opened.
    decoder: Literal["native", "ponyfill", "none"]
    # From asking for the camera to its opening: opened sessions only.
    open_seconds: Annotated[float, Field(ge=0, le=120)] | None = None
    # From asking for the camera, or the page shown again, to its closing.
    seconds: UpToADay
    labels: LabelsRead


AnyEvent = (
    WebVital
    | PageError
    | OutboxRetry
    | ScanToSave
    | OutOfDate
    | OlderStorage
    | CartDiscarded
    | LineRemoved
    | RemovalUndone
    | DifferenceAdded
    | RestPutBack
    | OtherSeparator
    | CameraSession
)
Event = Annotated[AnyEvent, Field(discriminator="kind")]


class Report(Strict):
    events: Annotated[list[Event], Field(min_length=1, max_length=MOST_EVENTS)]


page_errors = meter.create_counter(
    "inventory.page.errors",
    unit="{error}",
    description="Errors the scan and register pages caught, by page and source",
)
# Buckets around each vital's thresholds (web.dev: good up to 0.2 s for
# INP and 2.5 s for LCP, poor past 0.5 s and 4 s; good up to 0.1 for CLS,
# poor past 0.25): the SDK's own, from 5 up, would put nearly all in one.
VITAL_SECONDS = (0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1, 1.5, 2, 2.5, 3, 4, 6, 10, 20, 60)
LAYOUT_SHIFTS = (0.01, 0.025, 0.05, 0.1, 0.15, 0.25, 0.5, 1, 2.5)
vital_durations = meter.create_histogram(
    "inventory.page.web_vital.duration",
    unit="s",
    description="Largest Contentful Paint and Interaction to Next Paint",
    explicit_bucket_boundaries_advisory=VITAL_SECONDS,
)
layout_shifts = meter.create_histogram(
    "inventory.page.web_vital.layout_shift",
    unit="1",
    description="Cumulative Layout Shift",
    explicit_bucket_boundaries_advisory=LAYOUT_SHIFTS,
)
outbox_retries = meter.create_counter(
    "inventory.outbox.retries",
    unit="{attempt}",
    description="Batches the scan page will send again, by why",
)
out_of_date = meter.create_counter(
    "inventory.page.out_of_date",
    unit="{tab}",
    description="Scan page tabs that learned a newer build is out, by how",
)
older_storage = meter.create_counter(
    "inventory.page.older_storage",
    unit="{load}",
    description="Scan page loads that found saves not sent, or a cart, left "
    "by an older build, by which",
)
older_acted_on = meter.create_counter(
    "inventory.page.older_acted_on",
    unit="{entry}",
    description="Saves not sent, or carts, left by an older build that a "
    "volunteer put back in the cart, discarded or discarded a line of, or the "
    "page put back in an empty cart itself, by which and how",
)
cart_discards = meter.create_counter(
    "inventory.cart.discards",
    unit="{cart}",
    description="Carts a volunteer discarded on the scan page",
)
lines_removed = meter.create_counter(
    "inventory.cart.lines_removed",
    unit="{line}",
    description="Lines a volunteer removed from the scan page's cart",
)
removals_undone = meter.create_counter(
    "inventory.cart.removals_undone",
    unit="{line}",
    description="Lines removed from the scan page's cart that a volunteer put "
    "back with Undo",
)
differences_added = meter.create_counter(
    "inventory.cart.differences_added",
    unit="{batch}",
    description="What saves refused as another tab recorded their cart first "
    "held more of, put in the scan page's cart, by the page itself or a tap",
)
rests_put_back = meter.create_counter(
    "inventory.cart.rests_put_back",
    unit="{batch}",
    description="Batches refused for some of their lines whose other lines a "
    "volunteer put in the scan page's cart with Add to cart",
)
camera_sessions = meter.create_counter(
    "inventory.scan.camera.sessions",
    unit="{session}",
    description="Sessions of the scan page's camera, by how each ended and the "
    "decoder that read: opened, refused, closed while opening, or resumed as the "
    "page was shown again with it open",
)
# Most cameras open in a second or two; a first one waits on the
# browser's question to allow it.
OPEN_SECONDS = (0.25, 0.5, 1, 1.5, 2, 3, 5, 10, 20, 30, 60, 120)
camera_open = meter.create_histogram(
    "inventory.scan.camera.open.duration",
    unit="s",
    description="From asking for the scan page's camera to its opening",
    explicit_bucket_boundaries_advisory=OPEN_SECONDS,
)
# From a refusal in a second to a hub tablet's camera open all day: the
# SDK's own buckets stop short of a working day.
SESSION_SECONDS = (1, 5, 15, 30, 60, 120, 300, 600, 1800, 3600, 7200, 14400, 28800)
camera_session_durations = meter.create_histogram(
    "inventory.scan.camera.session.duration",
    unit="s",
    description="How long the scan page's camera stayed open, or took to be refused",
    explicit_bucket_boundaries_advisory=SESSION_SECONDS,
)
camera_labels = meter.create_counter(
    "inventory.scan.camera.labels",
    unit="{label}",
    description="Labels the scan page's camera read, by what each names",
)
scan_to_save = meter.create_histogram(
    "inventory.scan.to_save.duration",
    unit="s",
    description="From a batch's first line to saving it",
)


def record(event: AnyEvent) -> None:
    match event:
        case WebVital(name="CLS"):
            layout_shifts.record(
                event.value,
                {"page": event.page, "browser.web_vital.rating": event.rating},
            )
        case WebVital():
            vital_durations.record(
                event.value / 1000,
                {
                    "page": event.page,
                    "browser.web_vital.name": event.name,
                    "browser.web_vital.rating": event.rating,
                },
            )
        case PageError():
            page_errors.add(1, {"page": event.page, "source": event.source})
        case OutboxRetry():
            outbox_retries.add(1, {"reason": event.reason})
        case OutOfDate():
            out_of_date.add(1, {"learned": event.learned})
        case OlderStorage(action="found"):
            older_storage.add(1, {"held": event.held})
        case OlderStorage():
            older_acted_on.add(1, {"held": event.held, "action": event.action})
        case CartDiscarded():
            cart_discards.add(1)
        case LineRemoved():
            lines_removed.add(1)
        case RemovalUndone():
            removals_undone.add(1)
        case DifferenceAdded():
            differences_added.add(1, {"how": event.how})
        case RestPutBack():
            rests_put_back.add(1)
        case OtherSeparator():
            # Counted with the server's own (web/forms.quantity).
            forms.other_separators.add(1, {"separator": event.separator})
        case CameraSession():
            record_camera_session(event)
        case _:  # ScanToSave, the last kind left.
            scan_to_save.record(event.seconds)


def record_camera_session(event: CameraSession) -> None:
    session = {"outcome": event.outcome, "decoder": event.decoder}
    camera_sessions.add(1, session)
    camera_session_durations.record(event.seconds, session)
    if event.open_seconds is not None:
        camera_open.record(event.open_seconds, {"decoder": event.decoder})
    for kind, read in event.labels:
        if read:
            camera_labels.add(read, {"label.kind": kind})


# Async: it works in memory only, so it holds no thread a request for the
# database would want.
@router.post(
    PATH,
    status_code=204,
    responses={
        429: {"model": RateLimitedReply, "description": "Too many reports from here"}
    },
)
async def report(report: Report, request: Request) -> Response:
    """Record what a page reports about itself."""
    # A page flushes at FLUSH_AT events and sends PER_HOUR_IN_ALL an hour at
    # most (telemetry.ts); a few pages open at once on one network share an
    # address.
    limit = request.app.state.settings.rate_limits.page_reports
    if over_limit(request, limit, "telemetry"):
        log.info("page report refused", reason="rate_limited")
        body = RateLimitedReply(reason="rate_limited").model_dump()
        return JSONResponse(body, status_code=429)
    for event in report.events:
        record(event)
    # One line for a report's errors, however many: the counter has each.
    if errors := sum(isinstance(event, PageError) for event in report.events):
        log.warning("page errors", count=errors)
    return Response(status_code=204)


def install(app: FastAPI) -> None:
    """This app's count of reports by address, in this process's memory."""
    app.state.page_reports = MovingWindowRateLimiter(MemoryStorage())
