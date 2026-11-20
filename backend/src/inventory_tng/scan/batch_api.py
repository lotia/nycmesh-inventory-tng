"""The batch endpoint, POST /api/transactions: where the scan page sends
a volunteer's batch, for its rules (batch.record) to write
(docs/decisions/0003), and the replies to one refused."""

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse

from inventory_tng.core.db_sessions import DbSession, set_actor
from inventory_tng.devices.guards import DeviceRefusedReply, RegisteredDevice
from inventory_tng.scan import page_builds
from inventory_tng.scan.batch import BatchRefused, KeyReused, record
from inventory_tng.scan.batch_schemas import (
    Batch,
    BatchProblem,
    BatchRefusedReply,
    BodyRefusedReply,
    KeyReusedReply,
    Saved,
)
from inventory_tng.scan.page_builds import BuildTooOldReply
from inventory_tng.stock import over_recorded

router = APIRouter()


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
    item or package received, a retired package taken sealed with none
    left (docs/decisions/0003), or an item another line names in the same
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
            transaction, written = record(session, batch, device_id=device.id)
    except KeyReused as reused:
        over_recorded.raise_checks(session, reused.over)
        session.commit()
        raise
    # Read before the commit, which expires it.
    transaction_id = transaction.id
    session.commit()
    if not written:
        response.status_code = 200
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
