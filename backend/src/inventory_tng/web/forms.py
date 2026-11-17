"""How every page's forms read and check what was typed: versions, ids,
names and quantities, and what is wrong with them in words. It loads
neither FastAPI nor the templates, so each feature's forms.py may import
it (docs/decisions/0023)."""

from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from inventory_tng.accounts import name_up_to
from inventory_tng.core.db import Base
from inventory_tng.core.phrases import Words, gettext, lazy_gettext
from inventory_tng.ledger import whole_enough
from inventory_tng.models import INTEGER_MAX, canonical, whole

NAME_MAX = 200

WHOLE_UNITS = lazy_gettext("This item is counted in whole units.")


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
    required: Words | None = None,
) -> Decimal | str | None:
    """A quantity typed into a form: a Decimal, or what is wrong with it as a
    str. Blank is None, or `required` if one must be given. Up to three
    decimal places, as the ledger keeps; more than zero if `positive`, else
    zero or more; whole, for an item in `unit` "each"."""
    text = text.strip()
    if not text:
        return None if required is None else str(required)
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
