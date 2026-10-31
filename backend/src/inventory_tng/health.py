"""Whether the app can do its job."""

import structlog
from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import exc, text

from inventory_tng.db import DbSession, describe_error

log = structlog.get_logger(__name__)

router = APIRouter()


class Health(BaseModel):
    status: str


@router.get(
    "/healthz",
    responses={503: {"model": Health, "description": "The database is unreachable"}},
)
def healthz(session: DbSession, response: Response) -> Health:
    """Answers once the database does."""
    try:
        session.execute(text("SELECT 1"))
    except (exc.DBAPIError, exc.TimeoutError) as error:
        log.warning("database unreachable", **describe_error(error))
        response.status_code = 503
        return Health(status="database unreachable")
    return Health(status="ok")
