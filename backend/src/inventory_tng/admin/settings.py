"""Settings administrators change: how signing in works
(docs/decisions/0012), the site's time zone (0013), how the scan page
behaves (0011), and the reasons an adjustment may give (admin/counts.py).

Sign-in settings are sensitive: changing them asks for a fresh code, and
every administrator with an address is told, since one who could weaken
them could otherwise do it unseen."""

from typing import Annotated, NamedTuple

import structlog
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from inventory_tng import accounts, activity, adjustment_reasons, mail, scan_settings
from inventory_tng.admin.common import (
    ADMIN,
    CHANGED,
    RULES,
    admin_page,
    form_version,
    name_problem,
    now,
    text_form,
)
from inventory_tng.db import DbSession, RuleBroken, refusal, settings_row
from inventory_tng.mail import Notify
from inventory_tng.models import (
    ACCOUNT_CHANGE_MINUTES,
    CONFIRM_HOURS,
    CONFIRM_IDLE_MINUTES,
    NAME_CHECK_MINUTES,
    SESSION_DAYS,
    TOTP_POLICIES,
    AdjustmentReason,
    ScanSettings,
    SignInSettings,
    SiteSettings,
    whole,
)
from inventory_tng.pages import NOTICES, not_found, redirect
from inventory_tng.sign_in import AdminSignedIn, ConfirmedAdmin, SignedIn, changed
from inventory_tng.time_zones import zones

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
    Number(
        "account_change_minutes",
        *ACCOUNT_CHANGE_MINUTES,
        "minutes",
        "Inviting, resetting, retiring or bringing back an administrator needs a"
        " code typed within",
        "minutes, and only while the code above lasts; 0 asks each time, and an"
        " authenticator gives a new code only every 30 seconds",
    ),
)


def _page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    problems: dict[str, str] | None = None,
    values: dict[str, str] | None = None,
    alert: str | None = None,
) -> HTMLResponse:
    scan = session.get_one(ScanSettings, 1)
    sign_in = accounts.sign_in_settings(session)
    site = session.get_one(SiteSettings, 1)
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
            "title": "Site settings",
            "here": SETTINGS,
            "values": {
                "name_check_minutes": str(scan.name_check_minutes),
                "totp_policy": sign_in.totp_policy,
                "time_zone": site.time_zone,
                # What each form was shown at (models.Versioned), named as
                # its form sends it, so one shown again keeps the one sent.
                "scan_version": str(scan.edit_version),
                "sign_in_version": str(sign_in.edit_version),
                "site_version": str(site.edit_version),
            }
            | {n.field: str(getattr(sign_in, n.field)) for n in NUMBERS}
            | (values or {}),
            # A zone set outside this page, such as US/Eastern, still shows.
            "zones": zones(site.time_zone),
            "policies": TOTP_POLICIES,
            "numbers": NUMBERS,
            "name_check_min": NAME_CHECK_MINUTES[0],
            "name_check_max": NAME_CHECK_MINUTES[1],
            "reasons": reasons,
            "problems": problems,
            "alert": alert,
        },
    )


def _changed(request: Request, session: Session, signed: SignedIn) -> HTMLResponse:
    """The page as it is now, as a form's row changed since it was shown."""
    return _page(request, session, signed, alert=RULES[CHANGED])


@router.get(SETTINGS)
def settings(request: Request, signed: AdminSignedIn, session: DbSession) -> Response:
    return _page(request, session, signed)


@router.post(f"{SETTINGS}/scan")
def save_scan_settings(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    name_check_minutes: Annotated[str, Form()] = "",
    scan_version: Annotated[str, Form()] = "",
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
            values={
                "name_check_minutes": name_check_minutes,
                "scan_version": scan_version,
            },
        )
    seen = form_version(scan_version)
    _, broken = refusal(
        session, lambda: scan_settings.change(session, minutes, seen=seen), CHANGED
    )
    if broken is not None:
        return _changed(request, session, signed)
    session.commit()
    log.info("scan settings changed", name_check_minutes=minutes)
    return redirect(SETTINGS, notice="settings_saved", section="scan-page")


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
    return redirect(SETTINGS, notice="added", section="reasons")


@router.post(f"{SETTINGS}/reasons/{{reason_id}}")
def change_reason(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    reason_id: int,
    name: Annotated[str, Form()] = "",
    action: Annotated[str, Form()] = "rename",
    version: Annotated[str, Form()] = "",
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
            adjustment_reasons.rename(reason, name, seen=form_version(version))

    problem = name_problem(name)
    if problem is None:
        _, broken = refusal(session, change, "unique", CHANGED)
        if broken == CHANGED:
            return _changed(request, session, signed)
        problem = broken and REASON_TAKEN
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
    return redirect(SETTINGS, notice=notice.get(action, "saved"), section="reasons")


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
    try:
        seen = form_version(form.get("sign_in_version", ""))
        current = settings_row(session, SignInSettings, seen=seen)
    except RuleBroken:  # changed_since_shown, before anything is written.
        return _changed(request, session, signed)
    if all(getattr(current, field) == value for field, value in chosen.items()):
        # Nothing to record, and nobody to tell.
        return redirect(SETTINGS, notice="saved", section="sign-in")
    for field, value in chosen.items():
        setattr(current, field, value)
    # Mailed to every administrator in its own words, with the new values.
    activity.record(
        session,
        "sign_in_settings_changed",
        signed.admin.id,
        None,
        notify,
        others=mail.sign_in_settings_changed(current),
    )
    changed(signed, session)
    log.info("sign-in settings changed", **chosen)
    return redirect(SETTINGS, notice="saved", section="sign-in")


@router.post(f"{SETTINGS}/site")
def save_site_settings(
    request: Request,
    signed: AdminSignedIn,
    session: DbSession,
    time_zone: Annotated[str, Form()] = "",
    site_version: Annotated[str, Form()] = "",
) -> Response:
    """The zone times are shown in, which SiteSettings checks."""
    try:
        site = settings_row(session, SiteSettings, seen=form_version(site_version))
    except RuleBroken:  # changed_since_shown, before anything is written.
        return _changed(request, session, signed)
    if time_zone == site.time_zone:
        return redirect(SETTINGS, notice="site_saved", section="time-zone")

    def save() -> None:
        site.time_zone = time_zone

    _, broken = refusal(session, save, "site_settings_time_zone")
    if broken is not None:
        return _page(
            request,
            session,
            signed,
            problems={"time_zone": RULES[broken]},
            values={"time_zone": time_zone, "site_version": site_version},
        )
    session.commit()
    log.info("site time zone changed")
    return redirect(SETTINGS, notice="site_saved", section="time-zone")
