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

from inventory_tng.accounts import name_up_to
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
    canonical,
    whole,
)
from inventory_tng.pages import NOTICES, page, templates
from inventory_tng.phrases import gettext, lazy_gettext
from inventory_tng.sign_in import SignedIn

ADMIN = "/admin"
ADMINS = f"{ADMIN}/admins"
LABELS = f"{ADMIN}/labels"
NAME_MAX = 200

_globals = cast(dict[str, Any], templates.env.globals)
_globals["name_max"] = NAME_MAX

# The sections' names: in the navigation, in links back to them, and as
# their pages' titles.
SECTION_ITEMS = lazy_gettext("Items")
SECTION_CATEGORIES = lazy_gettext("Categories")
SECTION_LOCATIONS = lazy_gettext("Locations")
SECTION_LABELS = lazy_gettext("Labels")
SECTION_VOLUNTEERS = lazy_gettext("Volunteers")
SECTION_STOCK = lazy_gettext("Stock")
SECTION_COUNTS = lazy_gettext("Counts")
SECTION_DEVICES = lazy_gettext("Devices")
SECTION_ADMINS = lazy_gettext("Administrators")
SECTION_SETTINGS = lazy_gettext("Site settings")

# A view of those settled, beside those waiting (_views.html).
SETTLED_VIEW = lazy_gettext("Settled")

# A form for open packages sent with none ticked.
TICK_OPEN_PACKAGES = lazy_gettext("Tick the open packages first.")

# What most changes say once done; a page adds its own where the words differ.
ADDED = lazy_gettext("Added.")
SAVED = lazy_gettext("Saved.")
RETIRED_NOTICE = lazy_gettext("Retired.")
RESTORED = lazy_gettext("Brought back.")
NOTICES.update(
    {
        "added": ADDED,
        "saved": SAVED,
        "retired": RETIRED_NOTICE,
        "restored": RESTORED,
    }
)

PICK_A_ZONE = lazy_gettext("Pick a zone from the list.")
WHOLE_UNITS = lazy_gettext("This item is counted in whole units.")
# The rules domain functions check, by the name db.attempt gives one that a
# change broke (db.RuleBroken's), in words. A page passes its own words for
# "unique", which depend on what it is naming.
RULES = {
    "location_area_parent": lazy_gettext(
        "An area goes inside a store or another area."
    ),
    "location_no_cycle": lazy_gettext("That would put it inside itself."),
    "location_parent_current": lazy_gettext(
        "Its place is retired, or places are still "
        "inside it: bring that back, or move those, first."
    ),
    "location_custody_volunteer": lazy_gettext("Stop them holding stock first."),
    "location_holds_stock": lazy_gettext(
        "Move what is here elsewhere, or count it, first."
    ),
    "volunteer_merge_target": lazy_gettext(
        "Others are merged into them: merge those "
        "elsewhere first, or pick a current volunteer to merge into."
    ),
    "item_category_current": lazy_gettext(
        "Its category is retired: bring that back, or pick another, first."
    ),
    "item_unit_fixed": lazy_gettext("Its unit is fixed, as stock has moved in it."),
    "package_size_fixed": lazy_gettext(
        "Its size is fixed, as some has moved, been opened "
        "or labelled: add a package of the new size instead."
    ),
    "ck_item_package_short_below_under_size": lazy_gettext(
        "Larger than its too-short length, or change that first."
    ),
    "category_has_items": lazy_gettext("Move or retire its items first."),
    "site_settings_time_zone": PICK_A_ZONE,
    "location_time_zone": PICK_A_ZONE,
    "movement_whole_units": WHOLE_UNITS,
    "open_package_untracked": lazy_gettext(
        "That package isn't followed once opened any "
        "more: count what is on it as loose."
    ),
    "movement_item_once": lazy_gettext("An item is on the count twice: count it once."),
    "movement_open_package_closed": lazy_gettext(
        "An open package was closed meanwhile: count what is on it as loose."
    ),
    "location_countable": lazy_gettext(
        "That place is retired and holds nothing: pick another."
    ),
    "changed_since_shown": lazy_gettext(
        "Not saved: someone else saved this after you opened "
        "it. Here it is as it is now: make your change again."
    ),
    "retired_read_only": lazy_gettext(
        "Not saved: it's retired. Bring it back first to change it."
    ),
}
BREAKS_A_RULE = lazy_gettext("That breaks one of the inventory's rules.")
# A save refused as its row changed since its form was shown (db.unchanged):
# the page shows it again as it is now, with RULES' words as an alert.
CHANGED = "changed_since_shown"
# A change to a retired row, which is read only until brought back
# (docs/decisions/0019): said the same way.
RETIRED = "retired_read_only"
# The refusals said as an alert above the page, not beside a field.
ALERTS = frozenset({CHANGED, RETIRED})


def rule_words(broken: str, unique: str | None = None) -> str:
    """RULES' words for a rule broken; `unique`'s for a unique index, where
    a page can say which."""
    if broken == "unique" and unique is not None:
        return unique
    return str(RULES.get(broken, BREAKS_A_RULE))


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
    ident = row_id(typed)
    row = session.get(model, ident) if ident is not None else None
    if getattr(row, "retired_at", None) is None:
        return None
    return f"{base}/{ident}/restore"


# Who retired a record when a command did, in "Retired by …".
BY_OPERATOR = lazy_gettext("the operator")

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
        return str(BY_OPERATOR)
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


def up_to(limit: int) -> str:
    """A field's longest, refused: in characters."""
    return gettext("Up to %(max)d characters.", max=limit)


def at_most_places(places: int) -> str:
    """A quantity typed too finely, refused."""
    return gettext("Use at most %(places)d decimal places.", places=places)


def name_problem(name: str) -> str | None:
    # As stored (models.CanonicalName): °C for ℃ is a character longer.
    if not 1 <= len(canonical(name)) <= NAME_MAX:
        return name_up_to(NAME_MAX)
    return None


TYPE_A_NUMBER = lazy_gettext("Type a number, such as 12 or 0.5.")
TOO_BIG = lazy_gettext("That number is too big.")
ABOVE_ZERO = lazy_gettext("Use a number above 0.")
ZERO_OR_MORE = lazy_gettext("Use 0 or more.")
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
        return str(TYPE_A_NUMBER)
    if not value.is_finite() or value > QUANTITY_MAX:
        return str(TOO_BIG)
    if value.as_tuple().exponent < -QUANTITY_PLACES:  # ty: ignore[unsupported-operator]
        return at_most_places(QUANTITY_PLACES)
    if value < 0 or (positive and value == 0):
        return str(ABOVE_ZERO if positive else ZERO_OR_MORE)
    if unit is not None and not whole_enough(unit, value):
        return str(WHOLE_UNITS)
    return value
