"""The settings this telemetry reads, and what they mean when nobody set one.

A module of its own because both halves need it and neither can import the
other: `logs.py` imports `console.py` to draw a record, so anything they share
has to sit under both. Small, like `hosts.py`, and for the same reason -- a
function of its argument that a test can hold directly.
"""

import os

DEFAULTS = {
    "DJANGO_LOG_LEVEL": "INFO",
    "DJANGO_LOG_LEVELS": "",
    "DJANGO_LOG_FORMAT": "console",
    "DJANGO_LOG_LAYOUT": "",
    "DJANGO_LOG_CONTEXT": "hidden",
}


def setting(name: str, environment: dict[str, str] | None = None) -> str:
    """One setting, treating an empty value as an unset one.

    `django-environ` applies a default only when a variable is ABSENT, and a
    variable set to nothing at all is present. So a developer who cleared the
    value in `.env` rather than deleting the line -- which `.env.sample` all
    but invites, shipping two of these empty -- got an unbootable checkout
    whose refusal never mentioned the file that caused it. The same shape
    blanks a chart value into a CrashLoopBackOff, because the template emits
    the variable whether or not it has anything to say.

    Nothing is lost by the leniency: every one of these has a default that IS
    the sensible answer, so "set to nothing" and "not set" wanting the same
    thing is not a coincidence.

    The rest of this application's settings still have the sharp edge. Fixing
    that is inventory-tng-nb8.14, because it reaches every variable Django
    reads rather than the five here.
    """
    value = (environment if environment is not None else os.environ).get(name, "")
    return value.strip() or DEFAULTS[name]
