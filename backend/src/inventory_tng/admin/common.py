"""What the administrator pages share: how a page is sent, how a form is
read and checked, and how the catalogue's rules are put into words."""

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng.db import Base
from inventory_tng.ledger import whole_enough
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
        "retired": "Retired. Bring it back from the list if you need it.",
        "restored": "Brought back.",
    }
)

# The catalogue's rules, by the name db.attempt gives one that a change
# broke (db.RuleBroken's), in words. A page passes its own words for
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
    "category_has_items": "Move or retire its items first.",
}


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
    status_code: int = 200,
) -> HTMLResponse:
    """An administrator page: page(), signed in, without the context's
    unset (None) values, so page()'s defaults for problems and values hold."""
    given = {key: value for key, value in context.items() if value is not None}
    return page(request, template, {"admin": signed.admin} | given, status_code)


def site_link(request: Request, path: str, **query: str) -> str:
    """A link to pass on, as a QR code or by hand: under SITE_URL when mail
    is set up; otherwise the site as this administrator reached it, which
    their own browser chose."""
    mail = request.app.state.settings.mail
    site = mail.site_url if mail else str(request.base_url).rstrip("/")
    return f"{site}{path}" + (f"?{urlencode(query)}" if query else "")


def whole(typed: str, low: int, high: int) -> int | None:
    """A whole number typed, from low to high. Plain digits only: isdigit()
    alone lets through "²", which int() refuses, and a number too long to
    read."""
    typed = typed.strip()
    if not (typed.isascii() and typed.isdigit() and len(typed) <= len(str(high))):
        return None
    number = int(typed)
    return number if low <= number <= high else None


async def text_form(request: Request) -> dict[str, str]:
    """A form's fields, for a route that takes more than it names, as a
    dependency, so the route itself runs in the threadpool."""
    return {k: v for k, v in (await request.form()).items() if isinstance(v, str)}


def chosen[T: Base](
    session: Session, model: type[T], typed: str, *, current: bool = True
) -> T | None:
    """The row a form's select sent the id of: None if it sent none, there
    is no such row, or (if `current`) it is retired."""
    row = session.get(model, int(typed)) if typed.isdigit() else None
    if row is None or (current and getattr(row, "retired_at", None) is not None):
        return None
    return row


def current_rows[T: Base](session: Session, model: type[T]) -> list[T]:
    """A model's current rows, by name as people sort them."""
    table: Any = model
    return list(
        session.scalars(
            select(model)
            .where(table.retired_at.is_(None))
            .order_by(func.lower(table.name))
        )
    )


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
        return "This item is counted in whole units."
    return value
