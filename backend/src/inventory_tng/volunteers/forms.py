"""How the volunteers pages' forms are read and checked: a volunteer's
name and email, and who a duplicate is merged into, and what is wrong, in
words. The handlers in admin/volunteers.py read the request and pass the
form's fields."""

from sqlalchemy.orm import Session

from inventory_tng.accounts import email_problem
from inventory_tng.core.phrases import Words, lazy_gettext
from inventory_tng.models import Volunteer
from inventory_tng.web.forms import chosen, name_problem

TAKEN = lazy_gettext("Another current volunteer already has that name or email.")
PICK_INTO = lazy_gettext("Pick who they duplicate.")
STILL_HOLD = lazy_gettext("They still hold stock: move it back to a store first.")


def volunteer_problems(name: str, email: str) -> dict[str, Words]:
    """What is wrong with a volunteer's name and email, stripped."""
    problems = {}
    if problem := name_problem(name):
        problems["name"] = problem
    if problem := email_problem(email):
        problems["email"] = problem
    return problems


def merging(
    session: Session, volunteer_id: int, into_id: str
) -> tuple[Volunteer | None, Volunteer | None]:
    """The duplicate, if current, and who it is merged into, if another
    current volunteer."""
    found = session.get(Volunteer, volunteer_id)
    # Before `into` is checked, so a retired one is not found whatever it is.
    if found is None or found.retired_at is not None:
        return None, None
    into = chosen(session, Volunteer, into_id)
    return found, (None if into is None or into.id == found.id else into)
