"""The batch endpoint, POST /api/transactions: where the scan page sends
a volunteer's batch, for the ledger's rules (ledger.record) to write
(docs/decisions/0003), and the replies to one refused."""

import structlog
from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from opentelemetry import metrics

from inventory_tng import open_packages, over_recorded, page_builds
from inventory_tng.core.db import DbSession, set_actor
from inventory_tng.devices.guards import RegisteredDevice
from inventory_tng.devices.schemas import DeviceRefusedReply
from inventory_tng.ledger import BatchRefused, KeyReused, record
from inventory_tng.page_builds import BuildTooOldReply
from inventory_tng.scan.batch_schemas import (
    Batch,
    BatchProblem,
    BatchRefusedReply,
    BodyRefusedReply,
    KeyReusedReply,
    Saved,
)

log = structlog.get_logger(__name__)
meter = metrics.get_meter(__name__)

router = APIRouter()

recorded = meter.create_counter(
    "inventory.transactions.recorded",
    unit="{transaction}",
    description="Batches written to the ledger, by kind",
)
lines_per_batch = meter.create_histogram(
    "inventory.transaction.lines",
    unit="{line}",
    description="Lines in each batch written, by kind: an item is on one "
    "line for each package and one loose",
)
packages_opened = meter.create_counter(
    "inventory.packages.opened",
    unit="{package}",
    description="Sealed packages opened, by what each became: an open package "
    "followed until used up, or loose units",
)
taken_from = meter.create_counter(
    "inventory.open_packages.taken",
    unit="{line}",
    description="Lines of batches taken from an open package, by how: cut "
    "loose, or the package moved whole",
)
packages_reported = meter.create_counter(
    "inventory.packages.reported",
    unit="{package}",
    description="Open packages volunteers reported used up, by how: asked as a "
    "cut took all one showed, or by its button",
)
at_retired_place = meter.create_counter(
    "inventory.transactions.at_retired_place",
    unit="{transaction}",
    description="Batches written to or from a retired place, by side",
)


PATH = "/transactions"


@router.post(
    PATH,
    status_code=201,
    responses={
        200: {"model": Saved, "description": "Already written with this key"},
        403: {
            "model": DeviceRefusedReply,
            "description": "This device is not registered",
        },
        422: {
            "model": BatchRefusedReply | BodyRefusedReply,
            "description": "Refused: each problem's kind (type) and where "
            "(loc), never in words, which the page makes from the kind. A "
            "body that cannot be a batch at all gets FastAPI's own 422 "
            "(BodyRefusedReply), with pydantic's types and words, a line's "
            "package size among them (SizeProblem)",
        },
        409: {
            "model": KeyReusedReply | BuildTooOldReply,
            "description": "The key was used for another batch "
            "(key_reused), or the page's build is too old for this server "
            "(build_too_old): keep the batch, and load the page again",
        },
    },
    dependencies=[Depends(page_builds.checked)],
)
def save_batch(
    batch: Batch, device: RegisteredDevice, session: DbSession, response: Response
) -> Saved:
    """Write a whole batch, or none of it. A 422 names each bad line, as
    FastAPI's own do: an item, package, location or volunteer that does not
    exist, a fraction of an item counted in each or of a package, a retired
    item or package received, or an item another line names in the same
    package, or loose, too (repeated): send each item on one line for each
    package it is in, sealed, and one loose (docs/decisions/0020). The
    reply's X-Inventory-Build header names the build of the pages this
    server serves, when it has one (page_builds.py)."""
    # So the history of anything the batch changes, such as on_order, says who.
    set_actor(session, f"volunteer:{batch.volunteer_id}")
    try:
        # In a savepoint, so nothing of a batch refused for its key is kept,
        # an opening's open packages made before the key was found taken
        # too: only what it took off a line already recorded, for an
        # administrator to correct.
        with session.begin_nested():
            transaction, written, retired, opened, reported, checks = record(
                session, batch, device_id=device.id
            )
    except KeyReused as reused:
        raised = over_recorded.raise_checks(session, reused.over)
        session.commit()
        over_recorded.measure_raised(raised)
        raise
    # Read before the commit, which expires it.
    transaction_id = transaction.id
    session.commit()
    if not written:
        response.status_code = 200
        return Saved(transaction_id=transaction_id)
    # Counted once committed, so a failed commit and its retry count once.
    recorded.add(1, {"kind": batch.kind})
    if batch.lines:
        lines_per_batch.record(len(batch.lines), {"kind": batch.kind})
    for into, packages in opened.items():
        packages_opened.add(packages, {"into": into})
    if batch.kind != "open":
        for line in batch.lines:
            if line.open_package_key is not None:
                taken_from.add(1, {"how": "whole" if line.whole else "cut"})
    for how, packages in reported.items():
        packages_reported.add(packages, {"how": how})
    for how, raised in checks.items():
        open_packages.measure_checks_raised(how, raised)
    log.info(
        "transaction recorded",
        transaction_id=transaction_id,
        kind=batch.kind,
        lines=len(batch.lines),
        volunteer_id=batch.volunteer_id,
    )
    if retired:
        for side in retired:
            at_retired_place.add(1, {"side": side})
        # Accepted, as a volunteer's work is never lost; the place shows as
        # holding stock until it is counted (docs/decisions/0003).
        log.warning(
            "transaction at a retired place",
            transaction_id=transaction_id,
            from_location_id=batch.from_location_id,
            to_location_id=batch.to_location_id,
        )
    return Saved(transaction_id=transaction_id)


def install(app: FastAPI) -> None:
    """The responses for a refused batch: a 422 shaped like FastAPI's own, so
    the scan page reads one kind, and a 409 for a reused key."""

    @app.exception_handler(BatchRefused)
    def batch_refused(_request: Request, error: BatchRefused) -> JSONResponse:
        detail = [
            BatchProblem(type=kind, loc=["body", *loc]) for loc, kind in error.problems
        ]
        body = BatchRefusedReply(detail=detail)
        return JSONResponse(body.model_dump(mode="json"), status_code=422)

    @app.exception_handler(KeyReused)
    def key_reused(_request: Request, error: KeyReused) -> JSONResponse:
        body = KeyReusedReply(reason="key_reused", recorded=error.recorded)
        return JSONResponse(body.model_dump(mode="json"), status_code=409)
