"""Locations: stores, the areas inside them, and vehicles
(docs/decisions/0003). A volunteer's custody is a location too, made and
retired with the volunteer, so it is shown here but changed there. The tree's
rules are places/locations.py's; this page says them in words."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from inventory_tng.admins.guards import AdminSignedIn, SignedIn
from inventory_tng.core.db_errors import attempt
from inventory_tng.core.db_sessions import DbSession
from inventory_tng.core.local_time import zone_at
from inventory_tng.core.phrases import Words, lazy_gettext
from inventory_tng.core.time_zones import zones
from inventory_tng.labels import labels
from inventory_tng.models.labels import Label
from inventory_tng.models.places import STORE_KINDS, Location
from inventory_tng.places import locations, queries
from inventory_tng.places.forms import KINDS, TAKEN, new_place, place_change
from inventory_tng.places.paths import LOCATIONS
from inventory_tng.places.queries import changeable
from inventory_tng.volunteers.queries import volunteer_names
from inventory_tng.web.admin_pages import (
    admin_page,
    current_rows,
    now,
    retirement,
    undo_retire,
)
from inventory_tng.web.forms import ALERTS, RULES, form_version, rule_words
from inventory_tng.web.navigation import SECTION_LOCATIONS
from inventory_tng.web.pages import not_found, redirect

router = APIRouter(include_in_schema=False)

CUSTODY = lazy_gettext("Volunteer's custody")


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
    problems: Mapping[str, Words] | None = None,
    values: Mapping[str, str] | None = None,
    undo: str | None = None,
) -> HTMLResponse:
    current = current_rows(session, Location)
    custody = [loc for loc in current if loc.kind == "custody"]
    volunteers = volunteer_names(session, [loc.volunteer_id for loc in custody])
    return admin_page(
        request,
        signed,
        "places/locations.html",
        {
            "title": SECTION_LOCATIONS,
            "undo": undo,
            "here": LOCATIONS,
            "tree": _tree([loc for loc in current if loc.kind != "custody"]),
            "custody": [(loc, volunteers.get(loc.volunteer_id, "")) for loc in custody],
            "retired": queries.retired_places(session),
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
    problem: Words | None = None,
    values: Mapping[str, str] | None = None,
    alert: Words | None = None,
    field: str = "name",
) -> HTMLResponse:
    areas = queries.current_areas_of(session, location.id)
    return admin_page(
        request,
        signed,
        "places/location.html",
        {
            "title": location.name,
            "here": f"{LOCATIONS}/{location.id}",
            "back_link": (LOCATIONS, SECTION_LOCATIONS),
            "location": location,
            **retirement(session, location),
            "kind": KINDS.get(location.kind, CUSTODY),
            "areas": areas,
            "labels": labels.current_of(session, Label.location_id == location.id),
            "parents": _parents(current_rows(session, Location), location),
            "zones": zones(location.time_zone),
            # What it takes when it has no zone of its own.
            "inherited": zone_at(session, location.parent_id),
            "problems": {field: problem} if problem else None,
            "values": values,
            "alert": alert,
        },
    )


@router.get(LOCATIONS)
def list_locations(
    request: Request, signed: AdminSignedIn, session: DbSession, undo: str = ""
) -> Response:
    back = undo_retire(session, Location, undo, LOCATIONS)
    return _list_page(request, session, signed, undo=back)


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
    parent, problems = new_place(session, name, kind, parent_id)
    location, broken = None, None
    if not problems:
        under = parent.id if parent else None
        location, broken = attempt(
            session, lambda: locations.add(session, name, kind, under)
        )
    if broken:
        problems["name"] = rule_words(broken, TAKEN)
    if location is None:
        return _list_page(request, session, signed, problems=problems, values=form)
    session.commit()
    return redirect(f"{LOCATIONS}/{location.id}", notice="added")


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
    time_zone: Annotated[str, Form()] = "",
    version: Annotated[str, Form()] = "",
) -> Response:
    found = changeable(session, location_id)
    if found is None:
        return not_found()
    name = name.strip()
    broken = None
    parent, problem = place_change(session, found, name, parent_id)

    if problem is None:
        under = parent.id if parent else None
        seen = form_version(version)
        zone = time_zone or None

        def save() -> None:
            locations.save(session, found, name, under, time_zone=zone, seen=seen)

        _, broken = attempt(session, save)
        if broken in ALERTS:
            alert = RULES[broken]
            return _location_page(request, session, signed, found, alert=alert)
        problem = broken and rule_words(broken, TAKEN)
    if problem is not None:
        values = {
            "name": name,
            "parent_id": parent_id,
            "time_zone": time_zone,
            "version": version,
        }
        # A refused zone is said by the zone; anything else by the name.
        field = "time_zone" if broken == "location_time_zone" else "name"
        return _location_page(
            request, session, signed, found, problem, values, field=field
        )
    session.commit()
    return redirect(f"{LOCATIONS}/{location_id}", notice="saved")


@router.post(f"{LOCATIONS}/{{location_id}}/retire")
def retire_location(
    request: Request, signed: AdminSignedIn, session: DbSession, location_id: int
) -> Response:
    found = changeable(session, location_id)
    if found is None:
        return not_found()
    _, broken = attempt(session, lambda: locations.retire(session, found, now()))
    if broken:
        problem = rule_words(broken, TAKEN)
        return _location_page(request, session, signed, found, problem)
    session.commit()
    return redirect(LOCATIONS, notice="retired", undo=str(location_id))


@router.post(f"{LOCATIONS}/{{location_id}}/restore")
def restore_location(
    request: Request, signed: AdminSignedIn, session: DbSession, location_id: int
) -> Response:
    found = changeable(session, location_id)
    if found is None:
        return not_found()
    _, broken = attempt(session, lambda: locations.restore(session, found))
    if broken:
        return _location_page(request, session, signed, found, RULES[broken])
    session.commit()
    return redirect(f"{LOCATIONS}/{location_id}", notice="restored")
