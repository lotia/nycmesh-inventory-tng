"""The administrators' home page: where everything is, and how much of it."""

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import func, select

from inventory_tng.db import DbSession
from inventory_tng.models import Category, Item, Location, Volunteer
from inventory_tng.pages import page
from inventory_tng.sign_in import HOME, AdminSignedIn

router = APIRouter(include_in_schema=False)

# How many current rows of each, on the home page.
COUNTED = {
    "Items": Item,
    "Categories": Category,
    "Locations": Location,
    "Volunteers": Volunteer,
}


@router.get(HOME)
def home(request: Request, signed: AdminSignedIn, session: DbSession) -> Response:
    current = {
        label: session.scalar(
            select(func.count()).select_from(model).where(model.retired_at.is_(None))
        )
        for label, model in COUNTED.items()
    }
    return page(
        request,
        "admin_home.html",
        {"title": "Inventory", "admin": signed.admin, "current": current},
    )
