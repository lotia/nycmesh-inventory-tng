"""Volunteers: the names the scan page offers (docs/decisions/0004). One who
can hold stock has a custody location of their own, named for them. A
duplicate is merged into the volunteer it duplicates: it is retired, and the
ledger keeps its rows, which readers follow forward. The rules are the
database's (migration 0002); this page says them in words."""

from collections.abc import Mapping
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from inventory_tng.accounts import email_problem
from inventory_tng.admin.common import ADMIN, name_problem, now, section
from inventory_tng.db import DbSession, attempt
from inventory_tng.ledger import balance
from inventory_tng.models import Location, Volunteer
from inventory_tng.pages import NOTICES, not_found, page, redirect
from inventory_tng.sign_in import AdminSignedIn, SignedIn

router = APIRouter(include_in_schema=False)

VOLUNTEERS = section(f"{ADMIN}/volunteers", "Volunteers")
RULES = {
    "unique": "Another current volunteer already has that name or email.",
    "location_custody_volunteer": "Stop them holding stock first.",
    "volunteer_merge_target": "Others are merged into them: merge those elsewhere "
    "first, or pick a current volunteer to merge into.",
}

NOTICES.update(
    {
        "volunteer_added": "Volunteer added.",
        "volunteer_saved": "Saved.",
        "volunteer_retired": "Retired. Bring them back from the list if you need to.",
        "volunteer_restored": "Brought back.",
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


def _holding(session: Session, location_id: int) -> Decimal:
    """Everything still at a location, all items together: zero when
    empty."""
    held = session.scalar(
        select(func.coalesce(func.sum(func.abs(balance.c.quantity)), 0)).where(
            balance.c.location_id == location_id
        )
    )
    return Decimal(held or 0)


def _list_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    problems: Mapping[str, str] | None = None,
    values: Mapping[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    rows = session.scalars(select(Volunteer).order_by(func.lower(Volunteer.name))).all()
    holders = set(
        session.scalars(
            select(Location.volunteer_id).where(
                Location.kind == "custody", Location.retired_at.is_(None)
            )
        )
    )
    names = {v.id: v.name for v in rows}
    return page(
        request,
        "volunteers.html",
        {
            "title": "Volunteers",
            "admin": signed.admin,
            "current": [v for v in rows if v.retired_at is None],
            "retired": [v for v in rows if v.retired_at is not None],
            "holders": holders,
            "names": names,
            "problems": problems or {},
            "values": values or {},
        },
        status_code,
    )


def _volunteer_page(
    request: Request,
    session: Session,
    signed: SignedIn,
    volunteer: Volunteer,
    *,
    problems: Mapping[str, str] | None = None,
    values: Mapping[str, str] | None = None,
    status_code: int = 200,
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
    return page(
        request,
        "volunteer.html",
        {
            "title": volunteer.name,
            "admin": signed.admin,
            "volunteer": volunteer,
            "custody": _custody(session, volunteer.id),
            "merged_into": merged_into,
            "others": others,
            "problems": problems or {},
            "values": values or {},
        },
        status_code,
    )


def _refused(
    request: Request,
    session: Session,
    signed: SignedIn,
    volunteer: Volunteer,
    problem: str,
    values: Mapping[str, str] | None = None,
) -> HTMLResponse:
    return _volunteer_page(
        request,
        session,
        signed,
        volunteer,
        problems={"volunteer": problem},
        values=values,
        status_code=400,
    )


def _find(session: Session, volunteer_id: int) -> Volunteer | None:
    return session.get(Volunteer, volunteer_id, with_for_update=True)


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
        problems["name"] = RULES.get(broken, broken)
    if problems:
        values = {"name": name, "email": email}
        return _list_page(
            request, session, signed, problems=problems, values=values, status_code=400
        )
    session.commit()
    return redirect(f"{VOLUNTEERS}/{volunteer.id}", notice="volunteer_added")


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
    found = _find(session, volunteer_id)
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
        problems["name"] = RULES.get(broken, broken)
    if problems:
        values = {"name": name, "email": email}
        return _volunteer_page(
            request,
            session,
            signed,
            found,
            problems=problems,
            values=values,
            status_code=400,
        )
    session.commit()
    return redirect(f"{VOLUNTEERS}/{volunteer_id}", notice="volunteer_saved")


@router.post(f"{VOLUNTEERS}/{{volunteer_id}}/custody")
def hold_stock(
    request: Request, signed: AdminSignedIn, session: DbSession, volunteer_id: int
) -> Response:
    found = _find(session, volunteer_id)
    if found is None or found.retired_at is not None:
        return not_found()
    if _custody(session, found.id) is None:
        custody = Location(name=found.name, kind="custody", volunteer_id=found.id)
        if broken := attempt(session, lambda: session.add(custody)):
            return _refused(request, session, signed, found, RULES.get(broken, broken))
    session.commit()
    return redirect(f"{VOLUNTEERS}/{volunteer_id}", notice="custody_on")


@router.post(f"{VOLUNTEERS}/{{volunteer_id}}/custody/end")
def stop_holding_stock(
    request: Request, signed: AdminSignedIn, session: DbSession, volunteer_id: int
) -> Response:
    found = _find(session, volunteer_id)
    custody = _custody(session, volunteer_id) if found else None
    if found is None or custody is None:
        return not_found()
    if _holding(session, custody.id):
        problem = "They still hold stock: move it back to a store first."
        return _refused(request, session, signed, found, problem)
    custody.retired_at = now()
    session.commit()
    return redirect(f"{VOLUNTEERS}/{volunteer_id}", notice="custody_off")


@router.post(f"{VOLUNTEERS}/{{volunteer_id}}/retire")
def retire_volunteer(
    request: Request, signed: AdminSignedIn, session: DbSession, volunteer_id: int
) -> Response:
    found = _find(session, volunteer_id)
    if found is None or found.retired_at is not None:
        return not_found()

    def retire() -> None:
        found.retired_at = now()

    if broken := attempt(session, retire):
        return _refused(request, session, signed, found, RULES.get(broken, broken))
    session.commit()
    return redirect(VOLUNTEERS, notice="volunteer_retired")


@router.post(f"{VOLUNTEERS}/{{volunteer_id}}/merge")
def merge_volunteer(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    volunteer_id: int,
    into_id: Annotated[str, Form()] = "",
) -> Response:
    """This one is a duplicate of `into_id`: retired, pointing at it."""
    found = _find(session, volunteer_id)
    if found is None or found.retired_at is not None:
        return not_found()
    into = session.get(Volunteer, int(into_id)) if into_id.isdigit() else None
    if into is None or into.id == found.id or into.retired_at is not None:
        return _refused(request, session, signed, found, "Pick who they duplicate.")

    def merge() -> None:
        found.merged_into_id, found.retired_at = into.id, now()

    if broken := attempt(session, merge):
        return _refused(request, session, signed, found, RULES.get(broken, broken))
    session.commit()
    return redirect(f"{VOLUNTEERS}/{volunteer_id}", notice="volunteer_merged")


@router.post(f"{VOLUNTEERS}/{{volunteer_id}}/restore")
def restore_volunteer(
    request: Request, signed: AdminSignedIn, session: DbSession, volunteer_id: int
) -> Response:
    found = _find(session, volunteer_id)
    if found is None or found.retired_at is None:
        return not_found()

    def restore() -> None:
        # A merged duplicate comes back as itself.
        found.retired_at, found.merged_into_id = None, None

    if broken := attempt(session, restore):
        return _refused(request, session, signed, found, RULES.get(broken, broken))
    session.commit()
    return redirect(f"{VOLUNTEERS}/{volunteer_id}", notice="volunteer_restored")
