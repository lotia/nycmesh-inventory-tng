"""Whether the app can do its job."""

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import exc, text

from inventory_tng.db import DbSession

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
    except exc.DBAPIError, exc.TimeoutError:
        response.status_code = 503
        return Health(status="database unreachable")
    return Health(status="ok")
