"""How the settings page's forms are read and checked: how long the scan
page waits, the sign-in settings' policy and whole numbers, and an
adjustment reason's name, and what is wrong, in words. The handlers in
admin/settings.py read the request and pass the form's fields."""

from collections.abc import Callable, Mapping
from typing import NamedTuple

from babel.support import LazyProxy

from inventory_tng.core.phrases import Words, gettext, lazy_gettext
from inventory_tng.models import (
    ACCOUNT_CHANGE_MINUTES,
    CONFIRM_HOURS,
    CONFIRM_IDLE_MINUTES,
    NAME_CHECK_MINUTES,
    SESSION_DAYS,
    TOTP_POLICIES,
    whole,
)

REASON_TAKEN = lazy_gettext("There is already a reason by that name.")
REQUIRED_OR_OPTIONAL = lazy_gettext("Required or optional?")
# A whole number refused, by its unit, from the lowest to the highest.
WHOLE: dict[str, Callable[[int, int], str]] = {
    "days": lambda low, high: gettext(
        "Whole days, from %(low)d to %(high)d.", low=low, high=high
    ),
    "hours": lambda low, high: gettext(
        "Whole hours, from %(low)d to %(high)d.", low=low, high=high
    ),
    "minutes": lambda low, high: gettext(
        "Whole minutes, from %(low)d to %(high)d.", low=low, high=high
    ),
}


def whole_minutes(low: int, high: int) -> str:
    return gettext(
        "A whole number of minutes, from %(low)d to %(high)d.", low=low, high=high
    )


class Number(NamedTuple):
    """One of the sign-in settings' whole numbers, as the page asks for it."""

    field: str
    low: int
    high: int
    unit: str
    label: LazyProxy
    hint: LazyProxy


# Their ranges are the model's, which its check constraints hold.
NUMBERS = (
    Number(
        "session_days",
        *SESSION_DAYS,
        "days",
        lazy_gettext("Sessions last"),
        lazy_gettext("days, used or not; from the next sign-in"),
    ),
    Number(
        "confirm_hours",
        *CONFIRM_HOURS,
        "hours",
        lazy_gettext("A code for a sensitive change lasts"),
        lazy_gettext("hours"),
    ),
    Number(
        "confirm_idle_minutes",
        *CONFIRM_IDLE_MINUTES,
        "minutes",
        lazy_gettext("…or until unused for"),
        lazy_gettext("minutes"),
    ),
    Number(
        "account_change_minutes",
        *ACCOUNT_CHANGE_MINUTES,
        "minutes",
        # Translators: before a box for a number of minutes.
        lazy_gettext(
            "Inviting, resetting, retiring or bringing back an administrator "
            "needs a code typed within"
        ),
        # Translators: after a box for a number of minutes.
        lazy_gettext(
            "minutes, and only while the code above lasts; 0 asks each time, "
            "and an authenticator gives a new code only every 30 seconds"
        ),
    ),
)


def scan_minutes(typed: str) -> int | str:
    """How long the scan page waits before it checks a name, as typed, or
    what is wrong with it as a str."""
    minutes = whole(typed, *NAME_CHECK_MINUTES)
    if minutes is None:
        low, high = NAME_CHECK_MINUTES
        return whole_minutes(low, high)
    return minutes


def sign_in_fields(
    form: Mapping[str, str],
) -> tuple[dict[str, object], dict[str, Words]]:
    """The sign-in settings as the form chose them, and what is wrong."""
    problems: dict[str, Words] = {}
    policy = form.get("totp_policy", "")
    if policy not in TOTP_POLICIES:
        problems["totp_policy"] = REQUIRED_OR_OPTIONAL
    chosen: dict[str, object] = {"totp_policy": policy}
    for n in NUMBERS:
        chosen[n.field] = whole(form.get(n.field, ""), n.low, n.high)
        if chosen[n.field] is None:
            problems[n.field] = WHOLE[n.unit](n.low, n.high)
    return chosen, problems
