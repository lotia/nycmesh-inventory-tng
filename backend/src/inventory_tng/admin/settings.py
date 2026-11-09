"""Settings administrators change: how signing in works
(docs/decisions/0012), the site's time zone (0013), how the scan page
behaves (0011), and the reasons an adjustment may give (admin/counts.py).

Sign-in settings are sensitive: changing them asks for a fresh code, and
every administrator with an address is told, since one who could weaken
them could otherwise do it unseen."""

import functools
from typing import Annotated, NamedTuple

import structlog
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import accounts, adjustment_reasons, mail, scan_settings
from inventory_tng.admin.common import (
    ADMIN,
    RULES,
    admin_page,
    name_problem,
    now,
    text_form,
)
from inventory_tng.db import DbSession, refusal, settings_row, unique
from inventory_tng.mail import Notify
from inventory_tng.models import (
    CONFIRM_HOURS,
    CONFIRM_IDLE_MINUTES,
    NAME_CHECK_MINUTES,
    SESSION_DAYS,
    TOTP_POLICIES,
    AdjustmentReason,
    Admin,
    ScanSettings,
    SignInSettings,
    SiteSettings,
    whole,
)
from inventory_tng.pages import NOTICES, not_found, redirect
from inventory_tng.sign_in import AdminSignedIn, ConfirmedAdmin, SignedIn, changed
from inventory_tng.time_zones import known_zones

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

SETTINGS = f"{ADMIN}/settings"

NOTICES.update(
    {
        "settings_saved": "Saved. Open scan pages pick it up within ten minutes.",
        "reason_retired": "Retired: it is no longer offered.",
        "site_saved": "Saved: times are shown in the new zone.",
    }
)
REASON_TAKEN = "There is already a reason by that name."


class Number(NamedTuple):
    """One of the sign-in settings' whole numbers, as the page asks for it."""

    field: str
    low: int
    high: int
    unit: str
    label: str
    hint: str


# Their ranges are the model's, which its check constraints hold.
NUMBERS = (
    Number(
        "session_days",
        *SESSION_DAYS,
        "days",
        "Sessions last",
        "days, used or not; from the next sign-in",
    ),
    Number(
        "confirm_hours",
        *CONFIRM_HOURS,
        "hours",
        "A code for a sensitive change lasts",
        "hours",
    ),
    Number(
        "confirm_idle_minutes",
        *CONFIRM_IDLE_MINUTES,
        "minutes",
        "…or until unused for",
        "minutes",
    ),
)


@functools.cache
def zones() -> list[str]:
    """The zones to pick from, by name."""
    return sorted(
        name
        for name in known_zones()
        if name == "UTC"
        or ("/" in name and not name.startswith(("Etc/", "SystemV/", "US/")))
    )


def _page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    problems: dict[str, str] | None = None,
    values: dict[str, str] | None = None,
) -> HTMLResponse:
    scan = session.get_one(ScanSettings, 1)
    sign_in = accounts.sign_in_settings(session)
    site = session.get_one(SiteSettings, 1)
    names = zones()
    reasons = session.scalars(
        select(AdjustmentReason).order_by(
            AdjustmentReason.retired_at.is_not(None), AdjustmentReason.id
        )
    ).all()
    return admin_page(
        request,
        signed,
        "settings.html",
        {
            "title": "Settings",
            "here": SETTINGS,
            "values": {
                "name_check_minutes": str(scan.name_check_minutes),
                "totp_policy": sign_in.totp_policy,
                "time_zone": site.time_zone,
            }
            | {n.field: str(getattr(sign_in, n.field)) for n in NUMBERS}
            | (values or {}),
            # A zone set outside this page, such as US/Eastern, still shows.
            "zones": names if site.time_zone in names else [site.time_zone, *names],
            "policies": TOTP_POLICIES,
            "numbers": NUMBERS,
            "name_check_min": NAME_CHECK_MINUTES[0],
            "name_check_max": NAME_CHECK_MINUTES[1],
            "reasons": reasons,
            "problems": problems,
        },
        400 if problems else 200,
    )


@router.get(SETTINGS)
def settings(request: Request, signed: AdminSignedIn, session: DbSession) -> Response:
    return _page(request, session, signed)


@router.post(f"{SETTINGS}/scan")
def save_scan_settings(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    name_check_minutes: Annotated[str, Form()] = "",
) -> Response:
    minutes = whole(name_check_minutes, *NAME_CHECK_MINUTES)
    if minutes is None:
        low, high = NAME_CHECK_MINUTES
        problem = f"A whole number of minutes, from {low} to {high}."
        return _page(
            request,
            session,
            signed,
            problems={"name_check_minutes": problem},
            values={"name_check_minutes": name_check_minutes},
        )
    scan_settings.change(session, minutes)
    session.commit()
    log.info("scan settings changed", name_check_minutes=minutes)
    return redirect(SETTINGS, notice="settings_saved")


@router.post(f"{SETTINGS}/reasons")
def add_reason(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    name: Annotated[str, Form()] = "",
) -> Response:
    name = name.strip()
    problem = name_problem(name)
    added = None
    if problem is None:
        added, _ = refusal(
            session, lambda: adjustment_reasons.add(session, name), "unique"
        )
    if added is None:
        problems = {"reason": problem or REASON_TAKEN}
        return _page(request, session, signed, problems=problems)
    session.commit()
    log.info("adjustment reason added", adjustment_reason_id=added.id)
    return redirect(SETTINGS, notice="added")


@router.post(f"{SETTINGS}/reasons/{{reason_id}}")
def change_reason(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    reason_id: int,
    name: Annotated[str, Form()] = "",
    action: Annotated[str, Form()] = "rename",
) -> Response:
    """Rename, retire or bring back, as the button pressed says."""
    reason = adjustment_reasons.locked(session, reason_id)
    if reason is None:
        return not_found()
    action = action if action in {"retire", "restore"} else "rename"
    name = name.strip() or reason.name

    def change() -> None:
        if action == "retire":
            adjustment_reasons.retire(reason, now())
        elif action == "restore":
            adjustment_reasons.restore(reason)
        else:
            adjustment_reasons.rename(reason, name)

    problem = name_problem(name)
    if problem is None and not unique(session, change):
        problem = REASON_TAKEN
    if problem is not None:
        return _page(
            request,
            session,
            signed,
            problems={f"reason_{reason_id}": problem},
        )
    session.commit()
    log.info("adjustment reason changed", adjustment_reason_id=reason_id, action=action)
    notice = {"retire": "reason_retired", "restore": "restored"}
    return redirect(SETTINGS, notice=notice.get(action, "saved"))


@router.post(f"{SETTINGS}/sign-in")
def save_sign_in_settings(
    request: Request,
    signed: ConfirmedAdmin,
    session: DbSession,
    notify: Notify,
    form: Annotated[dict[str, str], Depends(text_form)],
) -> Response:
    problems: dict[str, str] = {}
    policy = form.get("totp_policy", "")
    if policy not in TOTP_POLICIES:
        problems["totp_policy"] = "Required or optional?"
    chosen: dict[str, object] = {"totp_policy": policy}
    for n in NUMBERS:
        chosen[n.field] = whole(form.get(n.field, ""), n.low, n.high)
        if chosen[n.field] is None:
            problems[n.field] = f"Whole {n.unit}, from {n.low} to {n.high}."
    if problems:
        return _page(request, session, signed, problems=problems, values=form)
    current = settings_row(session, SignInSettings)
    if all(getattr(current, field) == value for field, value in chosen.items()):
        # Nothing to record, and nobody to tell.
        return redirect(SETTINGS, notice="saved")
    for field, value in chosen.items():
        setattr(current, field, value)
    changed(signed, session)
    log.info("sign-in settings changed", **chosen)
    told = mail.sign_in_settings_changed(current)
    for admin_id, email in session.execute(
        select(Admin.id, Admin.email).where(
            Admin.retired_at.is_(None), Admin.email.is_not(None)
        )
    ):
        notify.tell(email, admin_id, told)
    return redirect(SETTINGS, notice="saved")


@router.post(f"{SETTINGS}/site")
def save_site_settings(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    time_zone: Annotated[str, Form()] = "",
) -> Response:
    """The zone times are shown in, which SiteSettings checks."""
    site = settings_row(session, SiteSettings)
    if time_zone == site.time_zone:
        return redirect(SETTINGS, notice="site_saved")

    def save() -> None:
        site.time_zone = time_zone

    _, broken = refusal(session, save, "site_settings_time_zone")
    if broken is not None:
        return _page(
            request,
            session,
            signed,
            problems={"time_zone": RULES[broken]},
            values={"time_zone": time_zone},
        )
    session.commit()
    log.info("site time zone changed")
    return redirect(SETTINGS, notice="site_saved")
