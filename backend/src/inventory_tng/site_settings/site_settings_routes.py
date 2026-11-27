"""Settings administrators change: how signing in works
(docs/decisions/0012), the site's time zone (0013), how the scan page
behaves (0011), and the reasons an adjustment may give
(stock/counts_routes.py).

Sign-in settings are sensitive: changing them asks for a fresh code, and
every administrator with an address is told, since one who could weaken
them could otherwise do it unseen."""

from typing import Annotated

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from inventory_tng.admins import accounts, activity, mail_messages
from inventory_tng.admins.guards import AdminSignedIn, ConfirmedAdmin, SignedIn, changed
from inventory_tng.core.db_errors import RuleBroken, refusal
from inventory_tng.core.db_locking import settings_row, stale_as_read
from inventory_tng.core.db_once_committed import once_committed
from inventory_tng.core.db_sessions import DbSession
from inventory_tng.core.mail import Notify
from inventory_tng.core.phrases import Words, lazy_gettext
from inventory_tng.core.time_zones import zones
from inventory_tng.models.site_settings import (
    NAME_CHECK_MINUTES,
    TOTP_POLICIES,
    ScanSettings,
    SignInSettings,
    SiteSettings,
)
from inventory_tng.site_settings import forms, queries, scan_settings
from inventory_tng.site_settings.paths import SETTINGS
from inventory_tng.stock import adjustment_reasons
from inventory_tng.web.admin_pages import TextForm, admin_page, now
from inventory_tng.web.forms import CHANGED, RULES, form_version, name_problem
from inventory_tng.web.navigation import SECTION_SETTINGS
from inventory_tng.web.pages import add_notices, not_found, redirect

log = structlog.get_logger(__name__)

router = APIRouter(include_in_schema=False)

SETTINGS_SAVED = lazy_gettext("Saved. Open scan pages pick it up within ten minutes.")
REASON_RETIRED = lazy_gettext("Retired: it is no longer offered.")
SITE_SAVED = lazy_gettext("Saved: times are shown in the new zone.")
add_notices(
    {
        "settings_saved": SETTINGS_SAVED,
        "reason_retired": REASON_RETIRED,
        "site_saved": SITE_SAVED,
    }
)


def _page(
    request: Request,
    session: Session,
    signed: SignedIn,
    *,
    problems: dict[str, Words] | None = None,
    values: dict[str, str] | None = None,
    alert: Words | None = None,
) -> HTMLResponse:
    scan = session.get_one(ScanSettings, 1)
    sign_in = accounts.sign_in_settings(session)
    site = session.get_one(SiteSettings, 1)
    reasons = queries.reasons_current_first(session)
    return admin_page(
        request,
        signed,
        "site_settings/settings.html",
        {
            "title": SECTION_SETTINGS,
            "here": SETTINGS,
            "values": {
                "name_check_minutes": str(scan.name_check_minutes),
                "totp_policy": sign_in.totp_policy,
                "time_zone": site.time_zone,
                # What each form was shown at (models.base.Versioned), named as
                # its form sends it, so one shown again keeps the one sent.
                "scan_version": str(scan.edit_version),
                "sign_in_version": str(sign_in.edit_version),
                "site_version": str(site.edit_version),
            }
            | {n.field: str(getattr(sign_in, n.field)) for n in forms.NUMBERS}
            | (values or {}),
            # A zone set outside this page, such as US/Eastern, still shows.
            "zones": zones(site.time_zone),
            "policies": TOTP_POLICIES,
            "numbers": forms.NUMBERS,
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
    seen = form_version(scan_version)
    # Changed since it was shown: said before what is wrong with what was
    # typed (inventory-tng-ryjv), as each settings form does.
    try:
        settings_row(session, ScanSettings, seen=seen)
    except RuleBroken:  # changed_since_shown, before anything is written.
        return _changed(request, session, signed)
    minutes = forms.scan_minutes(name_check_minutes)
    if isinstance(minutes, str):
        return _page(
            request,
            session,
            signed,
            problems={"name_check_minutes": minutes},
            values={
                "name_check_minutes": name_check_minutes,
                "scan_version": scan_version,
            },
        )
    scan_settings.change(session, minutes, seen=seen)
    session.commit()
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
        # Refused, its box holds what was typed, beside its problem
        # (inventory-tng-ikx2).
        problems = {"reason": problem or forms.REASON_TAKEN}
        values = {"reason": name}
        return _page(request, session, signed, problems=problems, values=values)
    session.commit()
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
    seen = form_version(version)
    # A rename changed since it was shown: said before what is wrong with
    # the name (inventory-tng-ryjv). The reason is locked above.
    if action == "rename" and stale_as_read(reason, seen):
        return _changed(request, session, signed)
    name = name.strip() or reason.name

    def change() -> None:
        if action == "retire":
            adjustment_reasons.retire(session, reason, now())
        elif action == "restore":
            adjustment_reasons.restore(session, reason)
        else:
            adjustment_reasons.rename(session, reason, name, seen=seen)

    # Only a rename uses the name, so only a rename is refused for it; a
    # retire or bring-back never is for what its box holds
    # (inventory-tng-j4ao).
    problem = name_problem(name) if action == "rename" else None
    if problem is None:
        _, broken = refusal(session, change, "unique")
        problem = broken and forms.REASON_TAKEN
    if problem is not None:
        # A rename refused shows what was typed, beside its problem; a
        # retire or bring-back refused, the name stored, as it did not use
        # what was typed (inventory-tng-jlsi).
        field = f"reason_{reason_id}"
        typed = {field: name} if action == "rename" else {}
        return _page(request, session, signed, problems={field: problem}, values=typed)
    session.commit()
    notice = {"retire": "reason_retired", "restore": "restored"}
    return redirect(SETTINGS, notice=notice.get(action, "saved"), section="reasons")


@router.post(f"{SETTINGS}/sign-in")
def save_sign_in_settings(
    request: Request,
    signed: ConfirmedAdmin,
    session: DbSession,
    notify: Notify,
    form: TextForm,
) -> Response:
    try:
        seen = form_version(form.get("sign_in_version", ""))
        current = settings_row(session, SignInSettings, seen=seen)
    except RuleBroken:  # changed_since_shown, before anything is written.
        return _changed(request, session, signed)
    # Checked once the version is: a stale form says so first
    # (inventory-tng-ryjv).
    chosen, problems = forms.sign_in_fields(form)
    if problems:
        return _page(request, session, signed, problems=problems, values=form)
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
        others=mail_messages.sign_in_settings_changed(current),
    )
    once_committed(session, log.info, "sign-in settings changed", **chosen)
    changed(signed, session)
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
        once_committed(session, log.info, "site time zone changed")

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
    return redirect(SETTINGS, notice="site_saved", section="time-zone")
