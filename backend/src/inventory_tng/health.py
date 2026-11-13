"""Whether the app can do its job, and which build of the pages it serves."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from inventory_tng.db import DbSession

router = APIRouter()

PATH = "/healthz"


class Health(BaseModel):
    status: str
    build: str | None = Field(
        description="The id of the build of the scan page this server "
        "serves, as its frontend log line at startup names it; null with no "
        "build, or when Vite's dev server serves the pages"
    )


@router.get(PATH)
def healthz(session: DbSession, request: Request) -> Health:
    """Answers once the database does. db.install() makes a failure a 503."""
    session.execute(text("SELECT 1"))
    return Health(status="ok", build=request.app.state.bundle.build_id())
