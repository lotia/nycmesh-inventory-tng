"""What the administrator pages share: how a page is sent, how a form is
read and checked, and how the catalogue's rules are put into words."""

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.db import Base, by_name
from inventory_tng.ledger import whole_enough
from inventory_tng.local_time import site_zone
from inventory_tng.models import (
    INTEGER_MAX,
    Admin,
    CatalogueHistory,
    Category,
    Item,
    Location,
    Volunteer,
    whole,
)
from inventory_tng.pages import NOTICES, page, templates
from inventory_tng.sign_in import SignedIn

ADMIN = "/admin"
ADMINS = f"{ADMIN}/admins"
LABELS = f"{ADMIN}/labels"
NAME_MAX = 200

_globals = cast(dict[str, Any], templates.env.globals)
_globals["name_max"] = NAME_MAX

# What most changes say once done; a page adds its own where the words differ.
NOTICES.update(
    {
        "added": "Added.",
        "saved": "Saved.",
        "retired": "Retired.",
        "restored": "Brought back.",
    }
)

# The rules domain functions check, by the name db.attempt gives one that a
# change broke (db.RuleBroken's), in words. A page passes its own words for
# "unique", which depend on what it is naming.
RULES = {
    "location_area_parent": "An area goes inside a store or another area.",
    "location_no_cycle": "That would put it inside itself.",
    "location_parent_current": "Its place is retired, or places are still "
    "inside it: bring that back, or move those, first.",
    "location_custody_volunteer": "Stop them holding stock first.",
    "location_holds_stock": "Move what is here elsewhere, or count it, first.",
    "volunteer_merge_target": "Others are merged into them: merge those "
    "elsewhere first, or pick a current volunteer to merge into.",
    "item_category_current": "Its category is retired: bring that back, or "
    "pick another, first.",
    "item_unit_fixed": "Its unit is fixed, as stock has moved in it.",
    "category_has_items": "Move or retire its items first.",
    "site_settings_time_zone": "Pick a zone from the list.",
    "location_time_zone": "Pick a zone from the list.",
    "movement_whole_units": "This item is counted in whole units.",
    "location_countable": "That place is retired and holds nothing: pick another.",
    "changed_since_shown": "Not saved: someone else saved this after you opened "
    "it. Here it is as it is now: make your change again.",
    "retired_read_only": "Not saved: it's retired. Bring it back first to change it.",
}
# A save refused as its row changed since its form was shown (db.unchanged):
# the page shows it again as it is now, with RULES' words as an alert.
CHANGED = "changed_since_shown"
# A change to a retired row, which is read only until brought back
# (docs/decisions/0019): said the same way.
RETIRED = "retired_read_only"
# The refusals said as an alert above the page, not beside a field.
ALERTS = frozenset({CHANGED, RETIRED})


def rule_words(broken: str, unique: str) -> str:
    if broken == "unique":
        return unique
    return RULES.get(broken, "That breaks one of the inventory's rules.")


def now() -> datetime:
    return datetime.now(UTC)


def admin_page(
    request: Request,
    signed: SignedIn,
    template: str,
    context: Mapping[str, Any],
    status_code: int | None = None,
) -> HTMLResponse:
    """An administrator page: page(), signed in, without the context's
    unset (None) values, so page()'s defaults for problems and values hold.
    Unless given, its status says what the context does: 409 with an alert,
    such as a row changed since its form was shown (CHANGED), 400 with
    problems, else 200."""
    given = {key: value for key, value in context.items() if value is not None}
    if status_code is None:
        status_code = (
            409 if given.get("alert") else 400 if given.get("problems") else 200
        )
    return page(request, template, {"admin": signed.admin} | given, status_code)


def site_link(request: Request, path: str, **query: str) -> str:
    """A link to pass on, as a QR code or by hand: under SITE_URL when mail
    is set up; otherwise the site as this administrator reached it, which
    their own browser chose."""
    mail = request.app.state.settings.mail
    site = mail.site_url if mail else str(request.base_url).rstrip("/")
    return f"{site}{path}" + (f"?{urlencode(query)}" if query else "")


async def text_form(request: Request) -> dict[str, str]:
    """A form's fields, for a route that takes more than it names, as a
    dependency, so the route itself runs in the threadpool."""
    return {k: v for k, v in (await request.form()).items() if isinstance(v, str)}


def form_version(typed: str) -> int:
    """The version of its row an edit form showed (models.Versioned), or 0,
    which no row has, so a form sent without one is refused as changed."""
    return whole(typed, 0, INTEGER_MAX) or 0


def row_id(typed: str) -> int | None:
    """The id a form sent, or None if it is not one."""
    return whole(typed, 1, INTEGER_MAX)


def chosen[T: Base](
    session: Session, model: type[T], typed: str, *, current: bool = True
) -> T | None:
    """The row a form's select sent the id of: None if it sent none, there
    is no such row, or (if `current`) it is retired."""
    ident = row_id(typed)
    row = session.get(model, ident) if ident is not None else None
    if row is None or (current and getattr(row, "retired_at", None) is not None):
        return None
    return row


def undo_retire[T: Base](
    session: Session, model: type[T], typed: str, base: str
) -> str | None:
    """Where a list's "Retired." notice posts to bring back the row just
    retired, whose id the retire sent it (?undo=), while it is still
    retired (docs/decisions/0019); else None, and the notice offers none."""
    row = chosen(session, model, typed, current=False)
    if row is None or getattr(row, "retired_at", None) is None:
        return None
    return f"{base}/{row_id(typed)}/restore"


# A kind of record an administrator retires from its own page.
type Retirable = Item | Category | Location | Volunteer | Admin


def retired_by(session: Session, row: Retirable) -> str | None:
    """Who retired `row`, as its page says it (docs/decisions/0019): the
    administrator's username, or "the operator" for a command, from the
    history of the change that last set its retired_at; None if the
    history names nobody, as for a row retired by a migration."""
    actor = session.scalar(
        select(CatalogueHistory.actor)
        .where(
            CatalogueHistory.table_name == row.__tablename__,
            CatalogueHistory.row_id == row.id,
            CatalogueHistory.action == "update",
            CatalogueHistory.old_row["retired_at"].astext.is_(None),
            CatalogueHistory.new_row["retired_at"].astext.is_not(None),
        )
        .order_by(CatalogueHistory.id.desc())
        .limit(1)
    )
    kind, _, ident = (actor or "").partition(":")
    if kind == "system":
        return "the operator"
    admin = session.get(Admin, int(ident)) if kind == "admin" else None
    return admin.username if admin else None


def retirement(session: Session, row: Retirable) -> dict[str, Any]:
    """What a record's page needs to say it is retired (_retired_record.html):
    the site's zone, and who retired it."""
    by = retired_by(session, row) if row.retired_at is not None else None
    return {"zone": site_zone(session), "retired_by": by}


def current_rows[T: Base](session: Session, model: type[T]) -> list[T]:
    """A model's current rows, by name as people sort them."""
    table: Any = model
    return by_name(session, model, table.retired_at.is_(None))


def name_problem(name: str) -> str | None:
    if not 1 <= len(name) <= NAME_MAX:
        return f"Give it a name of up to {NAME_MAX} characters."
    return None


QUANTITY_PLACES = 3  # numeric(12,3), as models.QUANTITY keeps them.
QUANTITY_MAX = Decimal("999999999.999")


def quantity(
    text: str,
    *,
    positive: bool,
    unit: str | None = None,
    required: str | None = None,
) -> Decimal | str | None:
    """A quantity typed into a form: a Decimal, or what is wrong with it as a
    str. Blank is None, or `required` if one must be given. Up to three
    decimal places, as the ledger keeps; more than zero if `positive`, else
    zero or more; whole, for an item in `unit` "each"."""
    text = text.strip()
    if not text:
        return required
    try:
        value = Decimal(text)
    except InvalidOperation:
        return "Type a number, such as 12 or 0.5."
    if not value.is_finite() or value > QUANTITY_MAX:
        return "That number is too big."
    if value.as_tuple().exponent < -QUANTITY_PLACES:  # ty: ignore[unsupported-operator]
        return f"Use at most {QUANTITY_PLACES} decimal places."
    if value < 0 or (positive and value == 0):
        return "Use a number above 0." if positive else "Use 0 or more."
    if unit is not None and not whole_enough(unit, value):
        return RULES["movement_whole_units"]
    return value
