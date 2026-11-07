"""Locations: stores, the areas inside them, and vehicles
(docs/decisions/0003). A volunteer's custody is a location too, made and
retired with the volunteer, so it is shown here but changed there. The tree's
rules are the database's (migration 0002); this page says them in words."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Annotated

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng.admin.common import (
    ADMIN,
    admin_page,
    chosen,
    current_rows,
    holds_stock,
    locked_row,
    name_problem,
    now,
    rule_words,
)
from inventory_tng.db import DbSession, attempt
from inventory_tng.models import STORE_KINDS, Location, Volunteer
from inventory_tng.pages import not_found, redirect
from inventory_tng.sign_in import AdminSignedIn, SignedIn

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

LOCATIONS = f"{ADMIN}/locations"
# What an administrator makes here; custody comes with a volunteer.
KINDS = {"store": "Store", "area": "Area inside another", "vehicle": "Vehicle"}
TAKEN = "Another place beside it already has that name."


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


def _parents(current: list[Location], moving: Location | None = None) -> list[Location]:
    """Where an area may go: current stores and areas, but not itself."""
    return [
        location
        for location in current
        if location.kind in STORE_KINDS and (moving is None or location.id != moving.id)
    ]


def _list_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    problems: Mapping[str, str] | None = None,
    values: Mapping[str, str] | None = None,
) -> HTMLResponse:
    current = current_rows(session, Location)
    custody = [loc for loc in current if loc.kind == "custody"]
    volunteers = dict(
        session.execute(
            select(Volunteer.id, Volunteer.name).where(
                Volunteer.id.in_([loc.volunteer_id for loc in custody])
            )
        ).all()
    )
    return admin_page(
        request,
        signed,
        "locations.html",
        {
            "title": "Locations",
            "here": LOCATIONS,
            "tree": _tree([loc for loc in current if loc.kind != "custody"]),
            "custody": [(loc, volunteers.get(loc.volunteer_id, "")) for loc in custody],
            "retired": session.scalars(
                select(Location)
                .where(Location.retired_at.is_not(None))
                .order_by(func.lower(Location.name))
            ).all(),
            "kinds": KINDS,
            "parents": _parents(current),
            "problems": problems,
            "values": values,
        },
        400 if problems else 200,
    )


def _location_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    location: Location,
    problem: str | None = None,
    values: Mapping[str, str] | None = None,
) -> HTMLResponse:
    areas = session.scalars(
        select(Location)
        .where(Location.parent_id == location.id, Location.retired_at.is_(None))
        .order_by(func.lower(Location.name))
    ).all()
    return admin_page(
        request,
        signed,
        "location.html",
        {
            "title": location.name,
            "here": f"{LOCATIONS}/{location.id}",
            "list": LOCATIONS,
            "location": location,
            "kind": KINDS.get(location.kind, "Volunteer's custody"),
            "areas": areas,
            "parents": _parents(current_rows(session, Location), location),
            "problems": {"name": problem} if problem else None,
            "values": values,
        },
        400 if problem else 200,
    )


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
    parent = chosen(session, Location, parent_id) if kind == "area" else None
    if kind == "area" and parent is None:
        problems["parent_id"] = "Pick where it is."
    location = Location(name=name, kind=kind, parent_id=parent.id if parent else None)
    if not problems and (broken := attempt(session, lambda: session.add(location))):
        problems["name"] = rule_words(broken, TAKEN)
    if problems:
        return _list_page(request, session, signed, problems=problems, values=form)
    session.commit()
    log.info("location added", location_id=location.id)
    return redirect(f"{LOCATIONS}/{location.id}", notice="added")


@router.get(f"{LOCATIONS}/{{location_id}}")
def location(
    request: Request, signed: AdminSignedIn, session: DbSession, location_id: int
) -> Response:
    found = session.get(Location, location_id)
    if found is None:
        return not_found()
    return _location_page(request, session, signed, found)


def _changeable(session: Session, location_id: int) -> Location | None:
    """A location this page changes, locked: not a volunteer's custody."""
    found = locked_row(session, Location, location_id)
    return None if found is None or found.kind == "custody" else found


@router.post(f"{LOCATIONS}/{{location_id}}")
def save_location(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    location_id: int,
    name: Annotated[str, Form()] = "",
    parent_id: Annotated[str, Form()] = "",
) -> Response:
    found = _changeable(session, location_id)
    if found is None:
        return not_found()
    name = name.strip()
    problem = name_problem(name)
    parent = chosen(session, Location, parent_id) if found.kind == "area" else None
    if found.kind == "area" and parent is None:
        problem = "Pick where it is."

    def save() -> None:
        found.name = name
        if parent is not None:
            found.parent_id = parent.id

    if problem is None and (broken := attempt(session, save)):
        problem = rule_words(broken, TAKEN)
    if problem is not None:
        values = {"name": name, "parent_id": parent_id}
        return _location_page(request, session, signed, found, problem, values)
    session.commit()
    log.info("location saved", location_id=location_id)
    return redirect(f"{LOCATIONS}/{location_id}", notice="saved")


@router.post(f"{LOCATIONS}/{{location_id}}/retire")
def retire_location(
    request: Request, signed: AdminSignedIn, session: DbSession, location_id: int
) -> Response:
    found = _changeable(session, location_id)
    if found is None or found.retired_at is not None:
        return not_found()
    # Once retired, a place cannot be counted or adjusted, so nothing at it
    # could be put right.
    if holds_stock(session, found.id):
        problem = "Move what is here elsewhere, or count it, first."
        return _location_page(request, session, signed, found, problem)

    def retire() -> None:
        found.retired_at = now()

    if broken := attempt(session, retire):
        problem = rule_words(broken, TAKEN)
        return _location_page(request, session, signed, found, problem)
    session.commit()
    log.info("location retired", location_id=location_id)
    return redirect(LOCATIONS, notice="retired")


@router.post(f"{LOCATIONS}/{{location_id}}/restore")
def restore_location(
    request: Request, signed: AdminSignedIn, session: DbSession, location_id: int
) -> Response:
    found = _changeable(session, location_id)
    if found is None or found.retired_at is None:
        return not_found()

    def restore() -> None:
        found.retired_at = None

    if broken := attempt(session, restore):
        taken = "A current place beside it already has its name: rename one first."
        return _location_page(
            request, session, signed, found, rule_words(broken, taken)
        )
    session.commit()
    log.info("location restored", location_id=location_id)
    return redirect(f"{LOCATIONS}/{location_id}", notice="restored")
