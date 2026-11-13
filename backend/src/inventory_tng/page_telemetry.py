"""What the scan and register pages report about themselves: POST
/api/telemetry (docs/decisions/0006; frontend/src/telemetry.ts).

The schema is the list of what may be sent: closed sets of names and
clamped numbers, nothing free. Each event becomes a metric; an error says
only that one happened, and on which page. Open to any browser, since the
register page reports before its device is registered, so every report
counts against its address first: in this process's memory, not the
database's shared limits (rate_limits.py), which guard signing in and
registering. A page's numbers are not worth a connection, and a flood of
them must not hold one up from those.
"""

from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from limits import RateLimitItemPerMinute
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter
from opentelemetry import metrics
from pydantic import BaseModel, ConfigDict, Field

from inventory_tng.devices import RateLimitedReply
from inventory_tng.web import rate_key

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

router = APIRouter()

PATH = "/telemetry"
# A page flushes at ten events and sends 54 an hour at most (telemetry.ts,
# PER_HOUR); a few pages open at once on one network share an address.
REPORTS = RateLimitItemPerMinute(30)
MOST_EVENTS = 50

Page = Literal["scan", "register"]


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
    # From a batch's first line to saving it: a day at most.
    seconds: Annotated[float, Field(ge=0, le=86_400)]


class OutOfDate(Strict):
    """A scan page's tab learned that a newer build is out (page_builds.py),
    once a tab."""

    kind: Literal["out_of_date"]
    # From another tab's, the server's reply, or a batch held back as from
    # a build too old.
    learned: Literal["other_tab", "server", "too_old"]


class OlderStorage(Strict):
    """A scan page found what an older build left under its own key, which
    it keeps but does not read: saves not sent, or a cart with lines."""

    kind: Literal["older_storage"]
    held: Literal["saves", "cart"]


AnyEvent = WebVital | PageError | OutboxRetry | ScanToSave | OutOfDate | OlderStorage
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
        case OlderStorage():
            older_storage.add(1, {"held": event.held})
        case _:  # ScanToSave, the last kind left.
            scan_to_save.record(event.seconds)


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
    reports: MovingWindowRateLimiter = request.app.state.page_reports
    if not reports.hit(REPORTS, "telemetry", rate_key(request)):
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
