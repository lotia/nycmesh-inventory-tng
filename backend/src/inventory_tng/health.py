"""Whether the app can do its job."""

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from inventory_tng.db import DbSession

router = APIRouter()

PATH = "/healthz"


class Health(BaseModel):
    status: str


@router.get(PATH, responses={503: {"description": "The database is unreachable"}})
def healthz(session: DbSession) -> Health:
    """Answers once the database does. db.install() makes a failure a 503."""
    session.execute(text("SELECT 1"))
    return Health(status="ok")
