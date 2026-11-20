"""How the devices pages' forms are read and checked: new registration
codes, a device's name, and the filter that chooses devices, and what is
wrong, in words, beside the field it is about. The handlers in
devices/devices_routes.py read the request and pass the form's fields."""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.orm import Session

from inventory_tng.core.local_time import parse_local, site_zone
from inventory_tng.core.phrases import Words, gettext, lazy_gettext, number
from inventory_tng.devices import devices
from inventory_tng.models.base import whole
from inventory_tng.web.forms import NAME_MAX, up_to

MAX_USES = 500
MAX_DAYS = 90
# Made together, as many as print on one sheet, two by two (Ali,
# 2026-11-19): a phrase is never kept, so they cannot be gathered later.
MAX_CODES = 4
STATES: dict[str, bool | None] = {"current": True, "revoked": False, "all": None}

A_TIME = lazy_gettext("A date and time, such as 2026-11-02 13:05.")
AFTER_FROM = lazy_gettext("A time after the one it is from.")
A_CODE_NUMBER = lazy_gettext("A code's number.")


def uses_problem(most: int) -> str:
    """A code's most uses refused: from 1 to `most`."""
    return gettext("From 1 to %(max)s devices.", max=number(most))


def days_problem(most: int) -> str:
    """A code's days refused: from 1 to `most`."""
    return gettext("From 1 to %(max)s days.", max=number(most))


def codes_problem(most: int) -> str:
    """How many codes to make refused: from 1 to `most`."""
    return gettext("From 1 to %(max)s codes.", max=number(most))


def label_problem(label: str) -> str | None:
    """What is wrong with a code's or a device's name, stripped: too long.
    Any longer would not fit the printed page."""
    if len(label) > NAME_MAX:
        return up_to(NAME_MAX)
    return None


@dataclass(frozen=True)
class CodeSettings:
    """New codes as a form asks for them: how many, each registering up to
    `max_uses` devices for `days`, under `label` (None for none)."""

    count: int
    max_uses: int
    days: int
    label: str | None


def code_settings(
    uses: str, days: str, label: str, count: str
) -> tuple[CodeSettings | None, dict[str, Words]]:
    """New codes' settings as typed, or None, and what is wrong with them,
    beside each field."""
    problems: dict[str, Words] = {}
    max_uses, lifetime = whole(uses, 1, MAX_USES), whole(days, 1, MAX_DAYS)
    many = whole(count, 1, MAX_CODES)
    label = label.strip()
    if many is None:
        problems["count"] = codes_problem(MAX_CODES)
    if max_uses is None:
        problems["uses"] = uses_problem(MAX_USES)
    if lifetime is None:
        problems["days"] = days_problem(MAX_DAYS)
    if (problem := label_problem(label)) is not None:
        problems["label"] = problem
    if problems or many is None or max_uses is None or lifetime is None:
        return None, problems
    return CodeSettings(many, max_uses, lifetime, label or None), problems


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
