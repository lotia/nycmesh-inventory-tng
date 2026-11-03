"""The administrators' home page: where everything is, how much of it, and
what to put right so nobody is locked out (docs/decisions/0012)."""

from typing import NamedTuple

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import func, select

from inventory_tng import accounts
from inventory_tng.admin.common import ADMINS, admin_page
from inventory_tng.db import DbSession
from inventory_tng.models import Category, Item, Location, Volunteer
from inventory_tng.sign_in import ACCOUNT, HOME, AdminSignedIn

router = APIRouter(include_in_schema=False)

ALONE = (
    "You're the only administrator: if you were locked out, nobody could let "
    "you back in."
)
NO_EMAIL = "Your account has no email address, so you can't reset your own password."


class Advice(NamedTuple):
    """Something to put right, and where."""

    words: str
    path: str
    action: str


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
    # While it is so, every administrator is told.
    warnings: list[Advice] = []
    if accounts.admins_who_can_sign_in(session) == 1:
        warnings.append(Advice(ALONE, ADMINS, "Invite another"))
    if not signed.admin.email and request.app.state.settings.mail is not None:
        warnings.append(Advice(NO_EMAIL, ACCOUNT, "Add one"))
    return admin_page(
        request,
        signed,
        "admin_home.html",
        {"title": "Inventory", "current": current, "warnings": warnings},
    )
