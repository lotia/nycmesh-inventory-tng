"""Volunteers: the names the scan page offers (docs/decisions/0004). One who
can hold stock has a custody location of their own, named for them. A
duplicate is merged into the volunteer it duplicates: it is retired, and the
ledger keeps its rows, which readers follow forward. The rules are
volunteers/volunteers.py's; this page says them in words."""

from collections.abc import Mapping
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import exists, func, not_
from sqlalchemy.orm import Session

from inventory_tng.admins.guards import AdminSignedIn, SignedIn
from inventory_tng.core.db import DbSession, attempt
from inventory_tng.core.phrases import Words, gettext, lazy_gettext
from inventory_tng.models import Location, Volunteer
from inventory_tng.volunteers import queries, volunteers
from inventory_tng.volunteers.forms import (
    PICK_INTO,
    STILL_HOLD,
    TAKEN,
    merging,
    volunteer_problems,
)
from inventory_tng.web.admin import (
    ADMIN,
    SECTION_VOLUNTEERS,
    admin_page,
    now,
    retirement,
    undo_retire,
)
from inventory_tng.web.forms import ALERTS, RULES, form_version, rule_words
from inventory_tng.web.listing import AskedView, Listing, Searched, Sort, Sorting
from inventory_tng.web.pages import NOTICES, not_found, redirect

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

VOLUNTEERS = f"{ADMIN}/volunteers"
BRING_THEM_BACK = lazy_gettext("Bring them back")

MERGED = lazy_gettext("Merged. Their past records now count as the other's.")
CUSTODY_ON = lazy_gettext("They can hold stock now.")
CUSTODY_OFF = lazy_gettext("They no longer hold stock.")
NOTICES.update(
    {
        "volunteer_merged": MERGED,
        "custody_on": CUSTODY_ON,
        "custody_off": CUSTODY_OFF,
    }
)


def merge_title(name: str, into: str) -> str:
    return gettext("Merge %(name)s into %(into)s?", name=name, into=into)


# Whether a volunteer holds stock: has a current custody location.
HOLDS = exists().where(
    Location.kind == "custody",
    Location.retired_at.is_(None),
    Location.volunteer_id == Volunteer.id,
)
# How the volunteers list sorts: by name, as it always has, or by whether
# they hold stock, those who do first (Ali, 2026-11-15, inventory-tng-qwb).
SORTING = Sorting(
    "name",
    {
        "name": Sort.of(func.lower(Volunteer.name)),
        "holds": Sort.of(HOLDS, first="desc"),
    },
)
# What the holds-stock filter offers.
HOLDING = {"yes": HOLDS, "no": not_(HOLDS)}

# How the volunteers list finds one: by name.
SEARCHED = Searched(
    VOLUNTEERS, Volunteer, Volunteer.search_text, Volunteer.name, sorting=SORTING
)


def _listed(
    asked: AskedView,
    q: str = "",
    show: str = "",
    pick: str = "",
    holds: str = "",
) -> Listing[Volunteer]:
    """The volunteers list as its query string asks for it, with its filter
    by holding stock or not (Ali, 2026-11-15): anything else is none."""
    held = HOLDING.get(holds)
    return SEARCHED.read(
        q,
        show,
        pick,
        asked,
        filters={"holds": holds if held is not None else ""},
        where=[held] if held is not None else (),
    )


Listed = Annotated[Listing[Volunteer], Depends(_listed)]


def _list_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    listing: Listing[Volunteer] | None = None,
    problems: Mapping[str, Words] | None = None,
    values: Mapping[str, str] | None = None,
    undo: str | None = None,
) -> HTMLResponse:
    listing = (listing or SEARCHED.read()).find(session)
    shown = [v.id for v in listing.rows]
    holders = queries.ids_where(session, shown, HOLDS)
    merged_into = {v.merged_into_id for v in listing.rows} - {None}
    names = queries.volunteer_names(session, merged_into) if merged_into else {}
    return admin_page(
        request,
        signed,
        "volunteers.html",
        {
            "title": SECTION_VOLUNTEERS,
            "undo": undo,
            "undo_words": BRING_THEM_BACK,
            "here": VOLUNTEERS,
            "listing": listing,
            "holders": holders,
            "names": names,
            "problems": problems,
            "values": values,
        },
        400 if problems else 200,
    )


def _volunteer_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    volunteer: Volunteer,
    *,
    problems: Mapping[str, Words] | None = None,
    values: Mapping[str, str] | None = None,
    alert: Words | None = None,
) -> HTMLResponse:
    others = queries.current_others(session, volunteer.id)
    merged_into = (
        session.get(Volunteer, volunteer.merged_into_id)
        if volunteer.merged_into_id
        else None
    )
    return admin_page(
        request,
        signed,
        "volunteer.html",
        {
            "title": volunteer.name,
            "here": f"{VOLUNTEERS}/{volunteer.id}",
            "back_link": (VOLUNTEERS, SECTION_VOLUNTEERS),
            "volunteer": volunteer,
            **retirement(session, volunteer),
            "custody": volunteers.custody(session, volunteer.id),
            "merged_into": merged_into,
            "others": others,
            "problems": problems,
            "values": values,
            "alert": alert,
        },
    )


def _refused(
    request: Request,
    session: Session,
    signed: SignedIn,
    volunteer: Volunteer,
    problem: Words,
) -> HTMLResponse:
    """Their page again, saying why that was not done."""
    return _volunteer_page(
        request, session, signed, volunteer, problems={"volunteer": problem}
    )


@router.get(VOLUNTEERS)
def list_volunteers(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    listing: Listed,
    undo: str = "",
) -> Response:
    back = undo_retire(session, Volunteer, undo, VOLUNTEERS)
    return _list_page(request, session, signed, listing=listing, undo=back)


@router.get(f"{VOLUNTEERS}/suggest")
def suggest_volunteers(
    session: DbSession, signed: AdminSignedIn, listing: Listed
) -> JSONResponse:
    """What the search box offers as one types (_listing.html)."""
    offered = listing.suggest(session)
    return JSONResponse(offered, headers={"Cache-Control": "no-store"})


@router.post(VOLUNTEERS)
def add_volunteer(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    name: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
) -> Response:
    name, email = name.strip(), email.strip()
    problems = volunteer_problems(name, email)
    volunteer, broken = None, None
    if not problems:
        volunteer, broken = attempt(
            session, lambda: volunteers.add(session, name, email or None)
        )
    if broken:
        problems["name"] = rule_words(broken, TAKEN)
    if volunteer is None:
        values = {"name": name, "email": email}
        return _list_page(request, session, signed, problems=problems, values=values)
    session.commit()
    log.info("volunteer added", volunteer_id=volunteer.id)
    return redirect(f"{VOLUNTEERS}/{volunteer.id}", notice="added")


@router.get(f"{VOLUNTEERS}/{{volunteer_id}}")
def volunteer(
    request: Request, signed: AdminSignedIn, session: DbSession, volunteer_id: int
) -> Response:
    found = session.get(Volunteer, volunteer_id)
    if found is None:
        return not_found()
    return _volunteer_page(request, session, signed, found)


@router.post(f"{VOLUNTEERS}/{{volunteer_id}}")
def save_volunteer(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    volunteer_id: int,
    name: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    version: Annotated[str, Form()] = "",
) -> Response:
    found = session.get(Volunteer, volunteer_id)
    if found is None:
        return not_found()
    name, email = name.strip(), email.strip()
    problems = volunteer_problems(name, email)
    if not problems:
        seen = form_version(version)
        _, broken = attempt(
            session,
            lambda: volunteers.save(session, found, name, email or None, seen=seen),
        )
        if broken in ALERTS:
            alert = RULES[broken]
            return _volunteer_page(request, session, signed, found, alert=alert)
        if broken:
            problems["name"] = rule_words(broken, TAKEN)
    if problems:
        values = {"name": name, "email": email, "version": version}
        return _volunteer_page(
            request, session, signed, found, problems=problems, values=values
        )
    session.commit()
    log.info("volunteer saved", volunteer_id=volunteer_id)
    return redirect(f"{VOLUNTEERS}/{volunteer_id}", notice="saved")


@router.post(f"{VOLUNTEERS}/{{volunteer_id}}/custody")
def hold_stock(
    request: Request, signed: AdminSignedIn, session: DbSession, volunteer_id: int
) -> Response:
    found = session.get(Volunteer, volunteer_id)
    # A retired volunteer holds nothing.
    if found is None or found.retired_at is not None:
        return not_found()
    _, broken = attempt(session, lambda: volunteers.hold_stock(session, found))
    if broken:
        return _refused(request, session, signed, found, rule_words(broken, TAKEN))
    session.commit()
    log.info("custody started", volunteer_id=volunteer_id)
    return redirect(
        f"{VOLUNTEERS}/{volunteer_id}", notice="custody_on", section="holding"
    )


@router.post(f"{VOLUNTEERS}/{{volunteer_id}}/custody/end")
def stop_holding_stock(
    request: Request, signed: AdminSignedIn, session: DbSession, volunteer_id: int
) -> Response:
    found = session.get(Volunteer, volunteer_id)
    if found is None:
        return not_found()
    _, broken = attempt(
        session, lambda: volunteers.stop_holding_stock(session, found, now())
    )
    if broken:
        # location_holds_stock: the one rule ending custody can break.
        problem = STILL_HOLD
        return _refused(request, session, signed, found, problem)
    session.commit()
    log.info("custody ended", volunteer_id=volunteer_id)
    return redirect(
        f"{VOLUNTEERS}/{volunteer_id}", notice="custody_off", section="holding"
    )


@router.post(f"{VOLUNTEERS}/{{volunteer_id}}/retire")
def retire_volunteer(
    request: Request, signed: AdminSignedIn, session: DbSession, volunteer_id: int
) -> Response:
    found = session.get(Volunteer, volunteer_id)
    if found is None:
        return not_found()
    _, broken = attempt(session, lambda: volunteers.retire(session, found, now()))
    if broken:
        return _refused(request, session, signed, found, rule_words(broken, TAKEN))
    session.commit()
    log.info("volunteer retired", volunteer_id=volunteer_id)
    return redirect(VOLUNTEERS, notice="retired", undo=str(volunteer_id))


@router.get(f"{VOLUNTEERS}/{{volunteer_id}}/merge")
def confirm_merge(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    volunteer_id: int,
    into_id: str = "",
) -> Response:
    """Merging can't be undone as such: say what it does first."""
    found, into = merging(session, volunteer_id, into_id)
    if found is None:
        return not_found()
    if into is None:
        return _refused(request, session, signed, found, PICK_INTO)
    return admin_page(
        request,
        signed,
        "volunteer_merge.html",
        {
            "title": merge_title(found.name, into.name),
            "here": f"{VOLUNTEERS}/{volunteer_id}/merge",
            "keep": f"{VOLUNTEERS}/{volunteer_id}",
            "volunteer": found,
            "into": into,
        },
    )


@router.post(f"{VOLUNTEERS}/{{volunteer_id}}/merge")
def merge_volunteer(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    volunteer_id: int,
    into_id: Annotated[str, Form()] = "",
) -> Response:
    """This one is a duplicate of `into_id`: retired, pointing at it."""
    found, into = merging(session, volunteer_id, into_id)
    if found is None:
        return not_found()
    if into is None:
        return _refused(request, session, signed, found, PICK_INTO)
    _, broken = attempt(
        session, lambda: volunteers.merge(session, found, into.id, now())
    )
    if broken:
        return _refused(request, session, signed, found, rule_words(broken, TAKEN))
    session.commit()
    log.info("volunteer merged", volunteer_id=volunteer_id, merged_into_id=into.id)
    return redirect(f"{VOLUNTEERS}/{volunteer_id}", notice="volunteer_merged")


@router.post(f"{VOLUNTEERS}/{{volunteer_id}}/restore")
def restore_volunteer(
    request: Request, signed: AdminSignedIn, session: DbSession, volunteer_id: int
) -> Response:
    found = session.get(Volunteer, volunteer_id)
    if found is None:
        return not_found()
    _, broken = attempt(session, lambda: volunteers.restore(session, found))
    if broken:
        return _refused(request, session, signed, found, rule_words(broken, TAKEN))
    session.commit()
    log.info("volunteer restored", volunteer_id=volunteer_id)
    return redirect(f"{VOLUNTEERS}/{volunteer_id}", notice="restored")
