"""The administrators' home page: where everything is, how much of it, what
others changed of who may sign in since one last signed in (activity.py),
and what to put right: so nobody is locked out (docs/decisions/0012), and
stock at a retired place."""

from typing import NamedTuple

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import func, select

from inventory_tng import accounts, activity
from inventory_tng.admin import needs_action
from inventory_tng.admin.categories import CATEGORIES
from inventory_tng.admin.common import ADMINS, admin_page
from inventory_tng.admin.counts import COUNTS
from inventory_tng.admin.items import ITEMS
from inventory_tng.admin.locations import LOCATIONS
from inventory_tng.admin.volunteers import VOLUNTEERS
from inventory_tng.db import DbSession
from inventory_tng.ledger import retired_places_holding_stock
from inventory_tng.local_time import site_zone
from inventory_tng.models import Category, Item, Location, Volunteer
from inventory_tng.phrases import Words, lazy_gettext
from inventory_tng.sign_in import ACCOUNT, HOME, AdminSignedIn

router = APIRouter(include_in_schema=False)

ALONE = lazy_gettext(
    "You're the only administrator: if you were locked out, nobody could let "
    "you back in."
)
INVITE_ANOTHER = lazy_gettext("Invite another")
NO_EMAIL = lazy_gettext(
    "Your account has no email address, so you can't reset your own password."
)
ADD_ONE = lazy_gettext("Add one")
HOME_TITLE = lazy_gettext("Inventory")


class Advice(NamedTuple):
    """Something to put right, and where."""

    words: Words
    path: str
    action: Words


# How many current rows of each, on the home page, by its section's path.
COUNTED = {
    ITEMS: Item,
    CATEGORIES: Category,
    LOCATIONS: Location,
    VOLUNTEERS: Volunteer,
}


@router.get(HOME)
def home(request: Request, signed: AdminSignedIn, session: DbSession) -> Response:
    current = {
        path: session.scalar(
            select(func.count()).select_from(model).where(model.retired_at.is_(None))
        )
        for path, model in COUNTED.items()
    }
    # While it is so, every administrator is told.
    warnings: list[Advice] = []
    if accounts.admins_who_can_sign_in(session) == 1:
        warnings.append(Advice(ALONE, ADMINS, INVITE_ANOTHER))
    if not signed.admin.email and request.app.state.settings.mail is not None:
        warnings.append(Advice(NO_EMAIL, ACCOUNT, ADD_ONE))
    # What others changed of who may sign in since this administrator last
    # signed in, so those without an email hear of it too.
    since = signed.row.signed_in_before
    changes = (
        activity.recent(session, since=since, not_by=signed.admin.id)
        if since is not None
        else []
    )
    return admin_page(
        request,
        signed,
        "admin_home.html",
        {
            "title": HOME_TITLE,
            "current": current,
            "warnings": warnings,
            "retired_places": retired_places_holding_stock(session),
            "counts_path": COUNTS,
            "changes": changes,
            # What waits for an administrator's decision, until it is made
            # (docs/decisions/0020): unlike changes, shown however often
            # one signs in, and never mailed.
            "needs": needs_action.needs_action(session),
            "admins_path": ADMINS,
            "zone": site_zone(session),
        },
    )
