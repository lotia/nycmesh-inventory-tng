"""Locations: stores, the areas inside them, and vehicles
(docs/decisions/0003). A volunteer's custody is a location too, made and
retired with the volunteer, so it is shown here but changed there. The tree's
rules are the database's (migration 0002); this page says them in words."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng.admin.common import ADMIN, name_problem, now, section
from inventory_tng.db import DbSession, attempt
from inventory_tng.models import Location, Volunteer
from inventory_tng.pages import NOTICES, not_found, page, redirect
from inventory_tng.sign_in import AdminSignedIn, SignedIn

router = APIRouter(include_in_schema=False)

LOCATIONS = section(f"{ADMIN}/locations", "Locations")
# What an administrator makes here; custody comes with a volunteer.
KINDS = {"store": "Store", "area": "Area inside another", "vehicle": "Vehicle"}
# The database's rules, by the name it gives when one is broken, in words.
RULES = {
    "unique": "Another place beside it already has that name.",
    "location_area_parent": "An area goes inside a store or another area.",
    "location_no_cycle": "That would put it inside itself.",
}

NOTICES.update(
    {
        "location_added": "Location added.",
        "location_saved": "Saved.",
        "location_retired": "Retired. Bring it back from the list if you need it.",
        "location_restored": "Brought back.",
    }
)


@dataclass
class Place:
    """A location with the current areas inside it, for the tree."""

    location: Location
    areas: list[Place] = field(default_factory=list)


def _tree(locations: list[Location]) -> list[Place]:
    places = {location.id: Place(location) for location in locations}
    roots: list[Place] = []
    for place in places.values():
        parent = places.get(place.location.parent_id or 0)
        (parent.areas if parent else roots).append(place)
    return roots


def _current(session: Session) -> list[Location]:
    return list(
        session.scalars(
            select(Location)
            .where(Location.retired_at.is_(None))
            .order_by(func.lower(Location.name))
        )
    )


def _parents(session: Session, moving: Location | None = None) -> list[Location]:
    """Where an area may go: current stores and areas, but not itself."""
    return [
        location
        for location in _current(session)
        if location.kind in {"store", "area"}
        and (moving is None or location.id != moving.id)
    ]


def _list_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    problems: Mapping[str, str] | None = None,
    values: Mapping[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    current = _current(session)
    volunteers = {v.id: v.name for v in session.scalars(select(Volunteer))}
    return page(
        request,
        "locations.html",
        {
            "title": "Locations",
            "admin": signed.admin,
            "tree": _tree([loc for loc in current if loc.kind != "custody"]),
            "custody": [
                (loc, volunteers.get(loc.volunteer_id or 0, ""))
                for loc in current
                if loc.kind == "custody"
            ],
            "retired": session.scalars(
                select(Location)
                .where(Location.retired_at.is_not(None))
                .order_by(func.lower(Location.name))
            ).all(),
            "kinds": KINDS,
            "parents": _parents(session),
            "problems": problems or {},
            "values": values or {},
        },
        status_code,
    )


def _location_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    location: Location,
    *,
    problem: str | None = None,
    values: Mapping[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    areas = session.scalars(
        select(Location)
        .where(Location.parent_id == location.id, Location.retired_at.is_(None))
        .order_by(func.lower(Location.name))
    ).all()
    return page(
        request,
        "location.html",
        {
            "title": location.name,
            "admin": signed.admin,
            "location": location,
            "kind": KINDS.get(location.kind, "Volunteer's custody"),
            "areas": areas,
            "parents": _parents(session, location),
            "problems": {"name": problem} if problem else {},
            "values": values or {},
        },
        status_code,
    )


def _parent(session: Session, parent_id: str) -> Location | None:
    return session.get(Location, int(parent_id)) if parent_id.isdigit() else None


@router.get(LOCATIONS)
def locations(request: Request, signed: AdminSignedIn, session: DbSession) -> Response:
    return _list_page(request, session, signed)


@router.post(LOCATIONS)
def add_location(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    name: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "",
    parent_id: Annotated[str, Form()] = "",
) -> Response:
    name = name.strip()
    form = {"name": name, "kind": kind, "parent_id": parent_id}
    problems: dict[str, str] = {}
    if problem := name_problem(name):
        problems["name"] = problem
    if kind not in KINDS:
        problems["kind"] = "Pick what it is."
    parent = _parent(session, parent_id) if kind == "area" else None
    if kind == "area" and (parent is None or parent.retired_at is not None):
        problems["parent_id"] = "Pick where it is."
    location = Location(name=name, kind=kind, parent_id=parent.id if parent else None)
    if not problems and (broken := attempt(session, lambda: session.add(location))):
        problems["name"] = RULES.get(broken, "That breaks a rule of the tree.")
    if problems:
        return _list_page(
            request, session, signed, problems=problems, values=form, status_code=400
        )
    session.commit()
    return redirect(f"{LOCATIONS}/{location.id}", notice="location_added")


@router.get(f"{LOCATIONS}/{{location_id}}")
def location(
    request: Request, signed: AdminSignedIn, session: DbSession, location_id: int
) -> Response:
    found = session.get(Location, location_id)
    if found is None:
        return not_found()
    return _location_page(request, session, signed, found)


@router.post(f"{LOCATIONS}/{{location_id}}")
def save_location(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    location_id: int,
    name: Annotated[str, Form()] = "",
    parent_id: Annotated[str, Form()] = "",
) -> Response:
    found = session.get(Location, location_id, with_for_update=True)
    if found is None or found.kind == "custody":
        return not_found()
    name = name.strip()
    problem = name_problem(name)
    parent = _parent(session, parent_id) if found.kind == "area" else None
    if found.kind == "area" and (parent is None or parent.retired_at is not None):
        problem = "Pick where it is."

    def save() -> None:
        found.name = name
        if parent is not None:
            found.parent_id = parent.id

    if problem is None and (broken := attempt(session, save)):
        problem = RULES.get(broken, "That breaks a rule of the tree.")
    if problem is not None:
        values = {"name": name, "parent_id": parent_id}
        return _location_page(
            request,
            session,
            signed,
            found,
            problem=problem,
            values=values,
            status_code=400,
        )
    session.commit()
    return redirect(f"{LOCATIONS}/{location_id}", notice="location_saved")


@router.post(f"{LOCATIONS}/{{location_id}}/retire")
def retire_location(
    request: Request, signed: AdminSignedIn, session: DbSession, location_id: int
) -> Response:
    found = session.get(Location, location_id, with_for_update=True)
    if found is None or found.retired_at is not None or found.kind == "custody":
        return not_found()

    def retire() -> None:
        found.retired_at = now()

    if attempt(session, retire):
        problem = "Retire or move the areas inside it first."
        return _location_page(
            request, session, signed, found, problem=problem, status_code=400
        )
    session.commit()
    return redirect(LOCATIONS, notice="location_retired")


@router.post(f"{LOCATIONS}/{{location_id}}/restore")
def restore_location(
    request: Request, signed: AdminSignedIn, session: DbSession, location_id: int
) -> Response:
    found = session.get(Location, location_id, with_for_update=True)
    if found is None or found.retired_at is None or found.kind == "custody":
        return not_found()

    def restore() -> None:
        found.retired_at = None

    if broken := attempt(session, restore):
        problem = (
            "Its parent is retired: bring that back first, or move it."
            if broken == "location_parent_current"
            else "A current place beside it already has its name: rename one first."
        )
        return _location_page(
            request, session, signed, found, problem=problem, status_code=400
        )
    session.commit()
    return redirect(f"{LOCATIONS}/{location_id}", notice="location_restored")
