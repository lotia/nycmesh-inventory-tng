"""How the places pages' forms are read and checked: a place's name, what
it is, and where it is, and what is wrong, in words, beside the field it
is about. The handlers in places/locations_routes.py read the request and pass
the form's fields."""

from sqlalchemy.orm import Session

from inventory_tng.core.phrases import Words, lazy_gettext
from inventory_tng.models.places import Location
from inventory_tng.web.form_problems import Problem
from inventory_tng.web.forms import chosen, name_problem

# What an administrator makes here; custody comes with a volunteer.
KINDS = {
    "store": lazy_gettext("Store"),
    "area": lazy_gettext("Area"),
    "vehicle": lazy_gettext("Vehicle"),
}
TAKEN = lazy_gettext(
    "Another place beside it, perhaps a retired one, already has that name."
)
PICK_WHAT = lazy_gettext("Pick what it is.")
PICK_WHERE = lazy_gettext("Pick where it is.")
# The rules places/locations.py refuses a place by, besides its name's,
# and the field each is shown by; any other is shown by the name.
REFUSED_FIELDS = {
    "location_area_parent": "parent_id",
    "location_parent_current": "parent_id",
    "location_no_cycle": "parent_id",
    "location_time_zone": "time_zone",
}


def new_place(
    session: Session, name: str, kind: str, parent_id: str
) -> tuple[Location | None, dict[str, Words]]:
    """Where a new place of `kind` goes, if an area, and what is wrong with
    its name, stripped, kind or place."""
    problems: dict[str, Words] = {}
    if problem := name_problem(name):
        problems["name"] = problem
    if kind not in KINDS:
        problems["kind"] = Problem(PICK_WHAT, "not_chosen")
    parent = chosen(session, Location, parent_id) if kind == "area" else None
    if kind == "area" and parent is None:
        problems["parent_id"] = Problem(PICK_WHERE, "not_chosen")
    return parent, problems


def place_change(
    session: Session, found: Location, name: str, parent_id: str
) -> tuple[Location | None, dict[str, Words]]:
    """Where a place goes, if an area, and what is wrong with its name,
    stripped, or with that."""
    problems: dict[str, Words] = {}
    if problem := name_problem(name):
        problems["name"] = problem
    parent = chosen(session, Location, parent_id) if found.kind == "area" else None
    if found.kind == "area" and parent is None:
        problems["parent_id"] = Problem(PICK_WHERE, "not_chosen")
    return parent, problems
