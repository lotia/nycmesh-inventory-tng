"""Which build of the scan page sent a batch, and whether this server still
takes batches from it (docs/decisions/0010, 2026-11-12).

Each build of the pages has an id: when it was made (bundle.BUILD_ID). The
scan page sends its own with each batch, in HEADER, and the batch
endpoint's reply carries this server's, so a tab of an older build learns
that a newer one is out, sends what it holds, and asks for a reload.

A field the server comes to require is added first and required only in a
later deploy, as is any other way of accepting less of a batch
(CONTRIBUTING.md, The scan page; DEPLOYMENT.md, Tightening what a batch
accepts). That later change sets OLDEST_BUILD to the build of the deploy
before it, so a batch whose header names a build before it is answered
409 build_too_old, before its body is checked: its tab keeps it and asks
for a reload, where a 422 would have it refused. A batch with no header is
from another client of the API, never the page, so it is checked as any
other: a field it lacks is a 422 that names it.
"""

from typing import Annotated, Literal, cast

import structlog
from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse
from opentelemetry import metrics
from pydantic import BaseModel, Field

from inventory_tng.bundle import BUILD_ID

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

HEADER = "X-Inventory-Build"
# The oldest build whose batches this server takes, as its id; None takes
# every build's. Set only in a change that makes a batch accept less.
OLDEST_BUILD: str | None = None

too_old = meter.create_counter(
    "inventory.transactions.too_old",
    unit="{transaction}",
    description="Batches held back as from a build of the scan page older "
    "than the server takes: kept on the phone, which is asked to reload",
)


# What a client told its build is too old does, in the reply's own words.
WHAT_TO_DO = (
    "The scan page that sent this is older than this server takes: load the "
    "page again. A client other than the scan page sends no "
    f"{HEADER} header, and sends the fields the batch now requires, as the "
    "API's schema says."
)


class BuildTooOldReply(BaseModel):
    """A batch from a build of the scan page older than this server takes:
    not checked, nor saved."""

    reason: Literal["build_too_old"]
    build: str = Field(description=f"The build the batch named, in {HEADER}")
    oldest: str = Field(
        description="The oldest build of the scan page whose batches this server takes"
    )
    message: str = Field(description="What to do")


class BuildTooOld(Exception):
    def __init__(self, build: str) -> None:
        super().__init__(build)
        self.build = build


def is_too_old(build: str) -> bool:
    """Whether a batch naming `build` is from before OLDEST_BUILD. One naming
    what is not an id is from no build of ours, so from none new enough."""
    if OLDEST_BUILD is None:
        return False
    return not BUILD_ID.fullmatch(build) or build < OLDEST_BUILD


def own_build(request: Request) -> dict[str, str]:
    """HEADER naming the build of the pages this server serves, if it has
    one, for a reply's headers."""
    own = request.app.state.bundle.build_id()
    return {} if own is None else {HEADER: own}


def checked(
    request: Request,
    response: Response,
    build: Annotated[
        str | None,
        Header(
            alias=HEADER,
            description="The id of the scan page's build that sent it: "
            "a build older than the server takes is answered 409 "
            "build_too_old, before the batch is checked. Only the scan page "
            "sends it; a batch without it is checked as any other",
        ),
    ] = None,
) -> None:
    """Say which build this server's pages are, and refuse a batch from a
    build too old. A dependency, so it runs before the body is checked."""
    response.headers.update(own_build(request))
    if build is not None and is_too_old(build):
        raise BuildTooOld(build)


def install(app: FastAPI) -> None:
    """The answer to a batch from a build too old: 409, with this server's
    build, so its tab knows a newer one is out."""

    @app.exception_handler(BuildTooOld)
    def build_too_old(request: Request, error: BuildTooOld) -> JSONResponse:
        too_old.add(1)
        log.info("batch from a build too old", oldest=OLDEST_BUILD)
        body = BuildTooOldReply(
            reason="build_too_old",
            build=error.build,
            # Set, or nothing would be too old.
            oldest=cast(str, OLDEST_BUILD),
            message=WHAT_TO_DO,
        ).model_dump()
        return JSONResponse(body, status_code=409, headers=own_build(request))
