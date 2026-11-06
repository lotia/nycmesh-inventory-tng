"""Volunteers: the names the scan page offers (docs/decisions/0004). One who
can hold stock has a custody location of their own, named for them. A
duplicate is merged into the volunteer it duplicates: it is retired, and the
ledger keeps its rows, which readers follow forward. The rules are the
database's (migration 0002); this page says them in words."""

from collections.abc import Mapping
from typing import Annotated

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng.accounts import email_problem
from inventory_tng.admin.common import (
    ADMIN,
    admin_page,
    chosen,
    holds_stock,
    name_problem,
    now,
    rule_words,
)
from inventory_tng.db import DbSession, attempt, get_locked
from inventory_tng.models import Location, Volunteer
from inventory_tng.pages import NOTICES, not_found, redirect
from inventory_tng.sign_in import AdminSignedIn, SignedIn

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

VOLUNTEERS = f"{ADMIN}/volunteers"
TAKEN = "Another current volunteer already has that name or email."

NOTICES.update(
    {
        "volunteer_merged": "Merged. Their past records now count as the other's.",
        "custody_on": "They can hold stock now.",
        "custody_off": "They no longer hold stock.",
    }
)


def _custody(session: Session, volunteer_id: int) -> Location | None:
    return session.scalar(
        select(Location).where(
            Location.kind == "custody",
            Location.volunteer_id == volunteer_id,
            Location.retired_at.is_(None),
        )
    )


def _list_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    problems: Mapping[str, str] | None = None,
    values: Mapping[str, str] | None = None,
) -> HTMLResponse:
    rows = session.scalars(select(Volunteer).order_by(func.lower(Volunteer.name))).all()
    holders = set(
        session.scalars(
            select(Location.volunteer_id).where(
                Location.kind == "custody", Location.retired_at.is_(None)
            )
        )
    )
    return admin_page(
        request,
        signed,
        "volunteers.html",
        {
            "title": "Volunteers",
            "here": VOLUNTEERS,
            "current": [v for v in rows if v.retired_at is None],
            "retired": [v for v in rows if v.retired_at is not None],
            "holders": holders,
            "names": {v.id: v.name for v in rows},
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
            "list": VOLUNTEERS,
            "volunteer": volunteer,
            "custody": _custody(session, volunteer.id),
            "merged_into": merged_into,
            "others": others,
            "problems": problems,
            "values": values,
        },
        400 if problems else 200,
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
def volunteers(request: Request, signed: AdminSignedIn, session: DbSession) -> Response:
    return _list_page(request, session, signed)


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
    volunteer = Volunteer(name=name, email=email or None)
    if not problems and (broken := attempt(session, lambda: session.add(volunteer))):
        problems["name"] = rule_words(broken, TAKEN)
    if problems:
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
) -> Response:
    found = get_locked(session, Volunteer, volunteer_id)
    if found is None:
        return not_found()
    name, email = name.strip(), email.strip()
    problems = _checked(name, email)
    custody = _custody(session, found.id)

    def save() -> None:
        found.name, found.email = name, email or None
        # Their custody is named for them.
        if custody is not None:
            custody.name = name

    if not problems and (broken := attempt(session, save)):
        problems["name"] = rule_words(broken, TAKEN)
    if problems:
        values = {"name": name, "email": email}
        return _volunteer_page(
            request, session, signed, found, problems=problems, values=values
        )
    session.commit()
    log.info("volunteer saved", volunteer_id=volunteer_id)
    return redirect(f"{VOLUNTEERS}/{volunteer_id}", notice="saved")


def _current(session: Session, volunteer_id: int) -> Volunteer | None:
    """A current volunteer, locked."""
    found = get_locked(session, Volunteer, volunteer_id)
    return None if found is None or found.retired_at is not None else found


@router.post(f"{VOLUNTEERS}/{{volunteer_id}}/custody")
def hold_stock(
    request: Request, signed: AdminSignedIn, session: DbSession, volunteer_id: int
) -> Response:
    found = _current(session, volunteer_id)
    if found is None:
        return not_found()
    if _custody(session, found.id) is None:
        custody = Location(name=found.name, kind="custody", volunteer_id=found.id)
        if broken := attempt(session, lambda: session.add(custody)):
            return _refused(request, session, signed, found, rule_words(broken, TAKEN))
    session.commit()
    log.info("custody started", volunteer_id=volunteer_id)
    return redirect(f"{VOLUNTEERS}/{volunteer_id}", notice="custody_on")


@router.post(f"{VOLUNTEERS}/{{volunteer_id}}/custody/end")
def stop_holding_stock(
    request: Request, signed: AdminSignedIn, session: DbSession, volunteer_id: int
) -> Response:
    found = get_locked(session, Volunteer, volunteer_id)
    custody = _custody(session, volunteer_id) if found else None
    if found is None or custody is None:
        return not_found()
    if holds_stock(session, custody.id):
        problem = "They still hold stock: move it back to a store first."
        return _refused(request, session, signed, found, problem)
    custody.retired_at = now()
    session.commit()
    log.info("custody ended", volunteer_id=volunteer_id)
    return redirect(f"{VOLUNTEERS}/{volunteer_id}", notice="custody_off")


@router.post(f"{VOLUNTEERS}/{{volunteer_id}}/retire")
def retire_volunteer(
    request: Request, signed: AdminSignedIn, session: DbSession, volunteer_id: int
) -> Response:
    found = _current(session, volunteer_id)
    if found is None:
        return not_found()

    def retire() -> None:
        found.retired_at = now()

    if broken := attempt(session, retire):
        return _refused(request, session, signed, found, rule_words(broken, TAKEN))
    session.commit()
    log.info("volunteer retired", volunteer_id=volunteer_id)
    return redirect(VOLUNTEERS, notice="retired")


@router.post(f"{VOLUNTEERS}/{{volunteer_id}}/merge")
def merge_volunteer(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    volunteer_id: int,
    into_id: Annotated[str, Form()] = "",
) -> Response:
    """This one is a duplicate of `into_id`: retired, pointing at it."""
    found = _current(session, volunteer_id)
    if found is None:
        return not_found()
    into = chosen(session, Volunteer, into_id)
    if into is None or into.id == found.id:
        return _refused(request, session, signed, found, "Pick who they duplicate.")

    def merge() -> None:
        found.merged_into_id, found.retired_at = into.id, now()

    if broken := attempt(session, merge):
        return _refused(request, session, signed, found, rule_words(broken, TAKEN))
    session.commit()
    log.info("volunteer merged", volunteer_id=volunteer_id, merged_into_id=into.id)
    return redirect(f"{VOLUNTEERS}/{volunteer_id}", notice="volunteer_merged")


@router.post(f"{VOLUNTEERS}/{{volunteer_id}}/restore")
def restore_volunteer(
    request: Request, signed: AdminSignedIn, session: DbSession, volunteer_id: int
) -> Response:
    found = get_locked(session, Volunteer, volunteer_id)
    if found is None or found.retired_at is None:
        return not_found()

    def restore() -> None:
        # A merged duplicate comes back as itself.
        found.retired_at, found.merged_into_id = None, None

    if broken := attempt(session, restore):
        return _refused(request, session, signed, found, rule_words(broken, TAKEN))
    session.commit()
    log.info("volunteer restored", volunteer_id=volunteer_id)
    return redirect(f"{VOLUNTEERS}/{volunteer_id}", notice="restored")
