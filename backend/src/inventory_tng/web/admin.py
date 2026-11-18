"""What the administrator pages share: how a page is sent and how a form
is read. How a form is checked, and the catalogue's rules put into words,
are web/forms.py's; their navigation is web/navigation.py's.

Administrators' pages for the catalogue, places, volunteers and devices
(inventory-tng-8i4.14): server-rendered forms, signed in
(admins/guards.py), each change recorded in the history as the
administrator who made it, and logged by id.

Every catalogue page follows one pattern: a list with a form to add, a page
per row to change it, retire it (nothing is deleted) and bring it back. A
page passes its own paths to its templates, so a page shown again after a
refused form still links where it should.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng.admins.guards import SignedIn
from inventory_tng.core.db import Base, by_name
from inventory_tng.core.local_time import site_zone
from inventory_tng.core.phrases import Words, gettext, lazy_gettext
from inventory_tng.models import (
    Admin,
    CatalogueHistory,
    Category,
    Item,
    Location,
    Volunteer,
)
from inventory_tng.web.forms import NAME_MAX, row_id
from inventory_tng.web.pages import add_notices, page, templates

_globals = cast(dict[str, Any], templates.env.globals)
_globals["name_max"] = NAME_MAX

# A view of those settled, beside those waiting (_views.html).
SETTLED_VIEW = lazy_gettext("Settled")

# A form for open packages sent with none ticked.
TICK_OPEN_PACKAGES = lazy_gettext("Tick the open packages first.")

# What most changes say once done; a page adds its own where the words differ.
ADDED = lazy_gettext("Added.")
SAVED = lazy_gettext("Saved.")
RETIRED_NOTICE = lazy_gettext("Retired.")
RESTORED = lazy_gettext("Brought back.")
add_notices(
    {
        "added": ADDED,
        "saved": SAVED,
        "retired": RETIRED_NOTICE,
        "restored": RESTORED,
    }
)


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


def undo_retire[T: Base](
    session: Session, model: type[T], typed: str, base: str
) -> str | None:
    """Where a list's "Retired." notice posts to bring back the row just
    retired, whose id the retire sent it (?undo=), while it is still
    retired (docs/decisions/0019); else None, and the notice offers none."""
    ident = row_id(typed)
    row = session.get(model, ident) if ident is not None else None
    if getattr(row, "retired_at", None) is None:
        return None
    return f"{base}/{ident}/restore"


# Who retired a record when a command did, in "Retired by …".
BY_OPERATOR = lazy_gettext("the operator")

# A kind of record an administrator retires from its own page.
type Retirable = Item | Category | Location | Volunteer | Admin


def retired_by(session: Session, row: Retirable) -> Words | None:
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
        return BY_OPERATOR
    admin = session.get(Admin, int(ident)) if kind == "admin" else None
    return admin.username if admin else None


def retirement(session: Session, row: Retirable) -> dict[str, Any]:
    """What a record's page needs to say it is retired (_retired_record.html):
    the site's zone, and who retired it."""
    if row.retired_at is None:
        return {}
    return {"zone": site_zone(session), "retired_by": retired_by(session, row)}


def current_rows[T: Base](session: Session, model: type[T]) -> list[T]:
    """A model's current rows, by name as people sort them."""
    table: Any = model
    return by_name(session, model, table.retired_at.is_(None))


def revoke_title(name: str) -> str:
    """The title of a page asking before something is revoked."""
    return gettext("Revoke %(name)s?", name=name)
