"""How the devices pages' forms are read and checked: a new registration
code, a device's name, and the filter that chooses devices, and what is
wrong, in words, beside the field it is about. The handlers in
admin/devices.py read the request and pass the form's fields."""

from datetime import timedelta

from sqlalchemy.orm import Session

from inventory_tng.core.local_time import parse_local, site_zone
from inventory_tng.core.phrases import Words, gettext, lazy_gettext
from inventory_tng.devices import devices
from inventory_tng.models import whole
from inventory_tng.web.forms import NAME_MAX, up_to

MAX_USES = 500
MAX_DAYS = 90
STATES: dict[str, bool | None] = {"current": True, "revoked": False, "all": None}

A_TIME = lazy_gettext("A date and time, such as 2026-11-02 13:05.")
AFTER_FROM = lazy_gettext("A time after the one it is from.")
A_CODE_NUMBER = lazy_gettext("A code's number.")


def uses_problem(most: int) -> str:
    """A code's most uses refused: from 1 to `most`."""
    return gettext("From 1 to %(max)d devices.", max=most)


def days_problem(most: int) -> str:
    """A code's days refused: from 1 to `most`."""
    return gettext("From 1 to %(max)d days.", max=most)


def label_problem(label: str) -> str | None:
    """What is wrong with a code's or a device's name, stripped: too long.
    Any longer would not fit the printed page."""
    if len(label) > NAME_MAX:
        return up_to(NAME_MAX)
    return None


def code_settings(
    uses: str, days: str, label: str
) -> tuple[int | None, int | None, dict[str, Words]]:
    """A new code's most uses and days, as typed, and what is wrong with
    them or its name, stripped."""
    problems: dict[str, Words] = {}
    max_uses, lifetime = whole(uses, 1, MAX_USES), whole(days, 1, MAX_DAYS)
    if max_uses is None:
        problems["uses"] = uses_problem(MAX_USES)
    if lifetime is None:
        problems["days"] = days_problem(MAX_DAYS)
    if (problem := label_problem(label)) is not None:
        problems["label"] = problem
    return max_uses, lifetime, problems


def device_filter(
    session: Session, since: str, until: str, code: str, shown: str
) -> tuple[devices.DeviceFilter, dict[str, Words], dict[str, str]]:
    """The filter a form or link chose, what was wrong with it, and the
    filter as the page carries it, each field as typed (an unknown `shown`
    is current). `until` takes in the whole minute it names."""
    shown = shown if shown in STATES else "current"
    zone = site_zone(session)
    problems: dict[str, Words] = {}
    start = end = None
    if since.strip() and (start := parse_local(since, zone)) is None:
        problems["since"] = A_TIME
    if until.strip():
        end = parse_local(until, zone)
        if end is None:
            problems["until"] = A_TIME
        elif start is not None and end < start:
            problems["until"] = AFTER_FROM
        else:
            # Whatever seconds were typed. At the last minute a datetime
            # holds, no end at all.
            try:
                end = end.replace(second=0, microsecond=0) + timedelta(minutes=1)
            except OverflowError:
                end = None
    code_id = whole(code, 1, 2**31 - 1) if code.strip() else None
    if code.strip() and code_id is None:
        problems["code"] = A_CODE_NUMBER
    chosen = devices.DeviceFilter(
        since=start, until=end, code_id=code_id, current=STATES[shown]
    )
    return (
        chosen,
        problems,
        {"since": since, "until": until, "code": code, "state": shown},
    )
