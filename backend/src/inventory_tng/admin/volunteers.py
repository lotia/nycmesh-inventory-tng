"""Volunteers: the names the scan page offers (docs/decisions/0004). One who
can hold stock has a custody location of their own, named for them. A
duplicate is merged into the volunteer it duplicates: it is retired, and the
ledger keeps its rows, which readers follow forward. The rules are
volunteers.py's; this page says them in words."""

from collections.abc import Mapping
from typing import Annotated

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng import volunteers
from inventory_tng.accounts import email_problem
from inventory_tng.admin.common import (
    ADMIN,
    ALERTS,
    RULES,
    admin_page,
    chosen,
    form_version,
    name_problem,
    now,
    retirement,
    rule_words,
    undo_retire,
)
from inventory_tng.admin.listing import Listing, Searched
from inventory_tng.db import DbSession, attempt
from inventory_tng.models import Location, Volunteer
from inventory_tng.pages import NOTICES, not_found, redirect
from inventory_tng.sign_in import AdminSignedIn, SignedIn

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

VOLUNTEERS = f"{ADMIN}/volunteers"
TAKEN = "Another current volunteer already has that name or email."
PICK_INTO = "Pick who they duplicate."

NOTICES.update(
    {
        "volunteer_merged": "Merged. Their past records now count as the other's.",
        "custody_on": "They can hold stock now.",
        "custody_off": "They no longer hold stock.",
    }
)


# How the volunteers list finds one: by name.
SEARCHED = Searched(Volunteer, Volunteer.name, Volunteer.name)


def _list_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    listing: Listing[Volunteer] | None = None,
    problems: Mapping[str, str] | None = None,
    values: Mapping[str, str] | None = None,
    undo: str | None = None,
) -> HTMLResponse:
    listing = (listing or Listing(VOLUNTEERS)).find(
        session, SEARCHED, select(Volunteer)
    )
    shown = [v.id for v in listing.rows]
    holders = set(
        session.scalars(
            select(Location.volunteer_id).where(
                Location.kind == "custody",
                Location.retired_at.is_(None),
                Location.volunteer_id.in_(shown),
            )
        )
    )
    merged_into = {v.merged_into_id for v in listing.rows} - {None}
    names = (
        session.execute(
            select(Volunteer.id, Volunteer.name).where(Volunteer.id.in_(merged_into))
        )
        if merged_into
        else ()
    )
    return admin_page(
        request,
        signed,
        "volunteers.html",
        {
            "title": "Volunteers",
            "undo": undo,
            "undo_words": "Bring them back",
            "here": VOLUNTEERS,
            "listing": listing,
            "holders": holders,
            "names": {ident: name for ident, name in names},
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
    problems: Mapping[str, str] | None = None,
    values: Mapping[str, str] | None = None,
    alert: str | None = None,
) -> HTMLResponse:
    others = session.scalars(
        select(Volunteer)
        .where(Volunteer.retired_at.is_(None), Volunteer.id != volunteer.id)
        .order_by(func.lower(Volunteer.name))
    ).all()
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
            "back_link": (VOLUNTEERS, "Volunteers"),
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
    problem: str,
) -> HTMLResponse:
    """Their page again, saying why that was not done."""
    return _volunteer_page(
        request, session, signed, volunteer, problems={"volunteer": problem}
    )


def _checked(name: str, email: str) -> dict[str, str]:
    problems = {}
    if problem := name_problem(name):
        problems["name"] = problem
    if problem := email_problem(email):
        problems["email"] = problem
    return problems


@router.get(VOLUNTEERS)
def list_volunteers(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    q: str = "",
    show: str = "",
    page: str = "",
    pick: str = "",
    undo: str = "",
) -> Response:
    listing = Listing[Volunteer].read(VOLUNTEERS, q, show, page, pick)
    back = undo_retire(session, Volunteer, undo, VOLUNTEERS)
    return _list_page(request, session, signed, listing=listing, undo=back)


@router.get(f"{VOLUNTEERS}/suggest")
def suggest_volunteers(
    session: DbSession, signed: AdminSignedIn, q: str = "", show: str = ""
) -> JSONResponse:
    """What the search box offers as one types (_listing.html)."""
    listing = Listing[Volunteer].read(VOLUNTEERS, q, show)
    offered = listing.suggest(session, SEARCHED, select(Volunteer))
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
    problems = _checked(name, email)
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
    problems = _checked(name, email)
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
        problem = "They still hold stock: move it back to a store first."
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


def _merging(
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


@router.get(f"{VOLUNTEERS}/{{volunteer_id}}/merge")
def confirm_merge(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    volunteer_id: int,
    into_id: str = "",
) -> Response:
    """Merging can't be undone as such: say what it does first."""
    found, into = _merging(session, volunteer_id, into_id)
    if found is None:
        return not_found()
    if into is None:
        return _refused(request, session, signed, found, PICK_INTO)
    return admin_page(
        request,
        signed,
        "volunteer_merge.html",
        {
            "title": f"Merge {found.name} into {into.name}?",
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
    found, into = _merging(session, volunteer_id, into_id)
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
