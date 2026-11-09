"""What the scan and register pages report about themselves: POST
/api/telemetry (docs/decisions/0006; frontend/src/telemetry.ts).

The schema is the list of what may be sent: closed sets of names and
clamped numbers, nothing free. Each event becomes a metric; an error says
only that one happened, and on which page. Open to any browser, since the
register page reports before its device is registered, so every report
counts against its address first.
"""

from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from limits import RateLimitItemPerMinute
from opentelemetry import metrics
from pydantic import BaseModel, ConfigDict, Field

from inventory_tng.db import DbSession
from inventory_tng.devices import RateLimitedReply
from inventory_tng.rate_limits import limiter
from inventory_tng.web import rate_key

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

router = APIRouter()

PATH = "/telemetry"
# A page flushes at ten events and sends fifty at most; a few pages open at
# once on one network share an address.
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


Event = Annotated[
    WebVital | PageError | OutboxRetry | ScanToSave, Field(discriminator="kind")
]


class Report(Strict):
    events: Annotated[list[Event], Field(min_length=1, max_length=MOST_EVENTS)]


page_errors = meter.create_counter(
    "inventory.page.errors",
    unit="{error}",
    description="Errors the scan and register pages caught, by page and source",
)
vital_durations = meter.create_histogram(
    "inventory.page.web_vital.duration",
    unit="s",
    description="Largest Contentful Paint and Interaction to Next Paint",
)
layout_shifts = meter.create_histogram(
    "inventory.page.web_vital.layout_shift",
    unit="1",
    description="Cumulative Layout Shift",
)
outbox_retries = meter.create_counter(
    "inventory.outbox.retries",
    unit="{attempt}",
    description="Batches the scan page will send again, by why",
)
scan_to_save = meter.create_histogram(
    "inventory.scan.to_save.duration",
    unit="s",
    description="From a batch's first line to saving it",
)


def record(event: WebVital | PageError | OutboxRetry | ScanToSave) -> None:
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
            log.warning("page error", page=event.page, source=event.source)
        case OutboxRetry():
            outbox_retries.add(1, {"reason": event.reason})
        case _:  # ScanToSave, the last kind left.
            scan_to_save.record(event.seconds)


@router.post(
    PATH,
    status_code=204,
    responses={
        429: {"model": RateLimitedReply, "description": "Too many reports from here"}
    },
)
def report(report: Report, request: Request, session: DbSession) -> Response:
    """Record what a page reports about itself."""
    if not limiter(session).hit(REPORTS, "telemetry", rate_key(request)):
        log.info("page report refused", reason="rate_limited")
        body = RateLimitedReply(reason="rate_limited").model_dump()
        return JSONResponse(body, status_code=429)
    for event in report.events:
        record(event)
    return Response(status_code=204)
